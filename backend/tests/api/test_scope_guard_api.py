"""
API-layer tests for `POST /projects/{id}/scope-check` (SRS §16.3
preview endpoint — see `app/api/v1/routers/authorization.py` docstring
for why this exists ahead of the real Phase 2 scan-launch endpoint).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.v1.deps import (
    get_authorization_record_service,
    get_current_user,
    get_project_service,
    get_scope_guard_service,
    get_target_service,
)
from app.application.authorization_service import AuthorizationRecordService
from app.application.project_service import ProjectService
from app.application.scope_guard_service import ScopeGuardService
from app.application.target_service import TargetService
from app.domain.entities import AuthorizationRecord, Project, Target, User
from app.domain.value_objects import AuthorizationStatus, ProjectState, TargetType
from app.main import create_app
from tests.fakes import (
    FakeAuthorizationRecordRepository,
    FakeProjectRepository,
    FakeTargetRepository,
)


def _make_user() -> User:
    return User(
        id=uuid4(),
        email="tester@example.com",
        password_hash="irrelevant",
        full_name="Tester",
        is_active=True,
        created_at=datetime.now(UTC),
    )


@pytest_asyncio.fixture
async def setup() -> AsyncIterator[dict]:
    app = create_app()
    current_user = _make_user()

    project_repo = FakeProjectRepository()
    target_repo = FakeTargetRepository()
    auth_repo = FakeAuthorizationRecordRepository()

    project_service = ProjectService(project_repo, auth_repo)
    target_service = TargetService(target_repo)
    scope_guard_service = ScopeGuardService(project_repo, target_repo, auth_repo)

    app.dependency_overrides[get_current_user] = lambda: current_user
    app.dependency_overrides[get_project_service] = lambda: project_service
    app.dependency_overrides[get_target_service] = lambda: target_service
    app.dependency_overrides[get_authorization_record_service] = lambda: AuthorizationRecordService(
        auth_repo
    )
    app.dependency_overrides[get_scope_guard_service] = lambda: scope_guard_service

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield {
            "client": ac,
            "user": current_user,
            "project_service": project_service,
            "target_service": target_service,
            "auth_repo": auth_repo,
        }

    app.dependency_overrides.clear()


async def _make_authorized_project_with_target(
    setup: dict, allowed_targets: list[str]
) -> tuple[Project, Target, AuthorizationRecord]:
    """Creates a project in AUTHORIZED state with one target and a
    matching (not-yet-persisted) authorization record."""
    project = await setup["project_service"].create(
        organization_id=uuid4(),
        name="Test Project",
        description=None,
        tags=None,
        client_metadata=None,
        owner_user_id=setup["user"].id,
    )
    await setup["project_service"].transition_state(project.id, ProjectState.AUTHORIZED)
    target = await setup["target_service"].create(project.id, "10.0.0.1", TargetType.IP)

    record = AuthorizationRecord(
        id=uuid4(),
        project_id=project.id,
        client_name="Acme",
        document_reference="doc.pdf",
        authorized_from=date.today() - timedelta(days=1),
        authorized_to=date.today() + timedelta(days=30),
        allowed_targets=allowed_targets,
        approved_by=setup["user"].id,
        status=AuthorizationStatus.ACTIVE,
        scope_notes=None,
        evidence_pointer=None,
        created_at=datetime.now(UTC),
    )
    return project, target, record


@pytest.mark.asyncio
async def test_scope_check_succeeds_for_authorized_target(setup: dict) -> None:
    project, target, record = await _make_authorized_project_with_target(setup, ["10.0.0.1"])
    await setup["auth_repo"].add(record)
    await setup["project_service"].transition_state(project.id, ProjectState.ACTIVE)

    response = await setup["client"].post(
        f"/api/v1/projects/{project.id}/scope-check",
        json={"target_ids": [str(target.id)]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["project_id"] == str(project.id)
    assert body["validated_target_ids"] == [str(target.id)]


@pytest.mark.asyncio
async def test_scope_check_rejects_out_of_scope_target(setup: dict) -> None:
    project, target, record = await _make_authorized_project_with_target(setup, ["192.168.1.1"])
    await setup["auth_repo"].add(record)
    await setup["project_service"].transition_state(project.id, ProjectState.ACTIVE)

    response = await setup["client"].post(
        f"/api/v1/projects/{project.id}/scope-check",
        json={"target_ids": [str(target.id)]},
    )
    assert response.status_code == 422
    assert response.json()["type"] == "https://specter.ai/errors/out-of-scope-target"


@pytest.mark.asyncio
async def test_scope_check_rejects_when_project_not_active(setup: dict) -> None:
    project, target, _record = await _make_authorized_project_with_target(setup, [])
    # Deliberately do NOT activate — project remains in AUTHORIZED state.

    response = await setup["client"].post(
        f"/api/v1/projects/{project.id}/scope-check",
        json={"target_ids": [str(target.id)]},
    )
    assert response.status_code == 422
    assert response.json()["type"] == "https://specter.ai/errors/project-not-active"


@pytest.mark.asyncio
async def test_scope_check_requires_project_membership(setup: dict) -> None:
    """A user with no membership at all must get 403, not a scope-guard error."""
    project = await setup["project_service"].create(
        organization_id=uuid4(),
        name="Not My Project",
        description=None,
        tags=None,
        client_metadata=None,
        owner_user_id=uuid4(),  # someone else entirely
    )
    response = await setup["client"].post(
        f"/api/v1/projects/{project.id}/scope-check",
        json={"target_ids": [str(uuid4())]},
    )
    assert response.status_code == 403
