"""
API-layer tests for RBAC permission enforcement (SRS §16.2: "enforced
server-side on every endpoint... never trust frontend role display").

Uses dependency overrides so these run without a real database: current
user identity is injected directly (bypassing JWT decoding, which is
already covered by `tests/api/test_auth_api.py`), and organization/
project services are backed by in-memory fakes pre-seeded with
membership rows.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.v1.deps import get_current_user, get_organization_service, get_project_service
from app.application.organization_service import OrganizationService
from app.application.project_service import ProjectService
from app.domain.entities import User
from app.domain.value_objects import OrganizationRole, ProjectRole
from app.main import create_app
from tests.fakes import (
    FakeAuthorizationRecordRepository,
    FakeOrganizationRepository,
    FakeProjectRepository,
)


@pytest_asyncio.fixture
async def setup():
    app = create_app()

    org_repo = FakeOrganizationRepository()
    project_repo = FakeProjectRepository()
    auth_repo = FakeAuthorizationRecordRepository()

    org_service = OrganizationService(org_repo)
    project_service = ProjectService(project_repo, auth_repo)

    owner = User(
        id=uuid4(),
        email="owner@example.com",
        password_hash="unused",
        full_name="Owner",
        is_active=True,
        created_at=datetime.now(UTC),
    )
    outsider = User(
        id=uuid4(),
        email="outsider@example.com",
        password_hash="unused",
        full_name="Outsider",
        is_active=True,
        created_at=datetime.now(UTC),
    )
    read_only_member = User(
        id=uuid4(),
        email="readonly@example.com",
        password_hash="unused",
        full_name="Read Only",
        is_active=True,
        created_at=datetime.now(UTC),
    )

    org = await org_service.create("Acme Security", owner.id)
    project = await project_service.create(
        organization_id=org.id,
        name="Test Project",
        description=None,
        tags=None,
        client_metadata=None,
        owner_user_id=owner.id,
    )
    # Read-only member is on the org but explicitly Read-Only at the project level.
    await org_service.add_member(org.id, read_only_member.id, OrganizationRole.MEMBER)
    await project_service.add_member(project.id, read_only_member.id, ProjectRole.READ_ONLY)

    app.dependency_overrides[get_organization_service] = lambda: org_service
    app.dependency_overrides[get_project_service] = lambda: project_service

    return {
        "app": app,
        "org": org,
        "project": project,
        "owner": owner,
        "outsider": outsider,
        "read_only_member": read_only_member,
    }


def _client_as(app, user: User) -> AsyncClient:
    app.dependency_overrides[get_current_user] = lambda: user
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://testserver")


@pytest.mark.asyncio
async def test_owner_can_view_organization(setup):
    async with _client_as(setup["app"], setup["owner"]) as client:
        response = await client.get(f"/api/v1/organizations/{setup['org'].id}")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_outsider_cannot_view_organization(setup):
    async with _client_as(setup["app"], setup["outsider"]) as client:
        response = await client.get(f"/api/v1/organizations/{setup['org'].id}")
    assert response.status_code == 403
    assert response.json()["type"] == "https://specter.ai/errors/not-an-organization-member"


@pytest.mark.asyncio
async def test_outsider_cannot_view_project(setup):
    async with _client_as(setup["app"], setup["outsider"]) as client:
        response = await client.get(f"/api/v1/projects/{setup['project'].id}")
    assert response.status_code == 403
    assert response.json()["type"] == "https://specter.ai/errors/not-a-project-member"


@pytest.mark.asyncio
async def test_owner_can_rename_organization(setup):
    async with _client_as(setup["app"], setup["owner"]) as client:
        response = await client.patch(
            f"/api/v1/organizations/{setup['org'].id}", json={"name": "New Name"}
        )
    assert response.status_code == 200
    assert response.json()["name"] == "New Name"


@pytest.mark.asyncio
async def test_read_only_project_member_cannot_update_project(setup):
    """Read-Only is a valid project role for viewing, but must not pass
    the Owner/Admin gate on mutating endpoints (SRS FR-1.4/§16.2)."""
    async with _client_as(setup["app"], setup["read_only_member"]) as client:
        response = await client.patch(
            f"/api/v1/projects/{setup['project'].id}", json={"name": "Hijacked Name"}
        )
    assert response.status_code == 403
    assert response.json()["type"] == "https://specter.ai/errors/insufficient-permission"


@pytest.mark.asyncio
async def test_read_only_project_member_can_view_project(setup):
    """Read-Only should still pass membership-only checks (GET)."""
    async with _client_as(setup["app"], setup["read_only_member"]) as client:
        response = await client.get(f"/api/v1/projects/{setup['project'].id}")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_unauthenticated_request_returns_401_not_403(setup):
    """No token at all is a 401 (authentication failure), distinct from
    403 (authenticated but not permitted) — the two must not be conflated."""
    app = setup["app"]
    app.dependency_overrides.pop(get_current_user, None)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(f"/api/v1/organizations/{setup['org'].id}")
    assert response.status_code == 401
