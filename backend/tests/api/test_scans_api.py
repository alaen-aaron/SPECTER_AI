"""
API-layer tests for `/projects/{id}/scans` and `/scans/{id}` endpoints
(Milestone 3). Dependency-overridden with in-memory fakes — no real
Postgres/Redis/Celery needed (those are covered by the manually-run
end-to-end test against the real stack).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.v1.deps import (
    get_current_user,
    get_organization_service,
    get_project_service,
    get_scan_service,
)
from app.application.organization_service import OrganizationService
from app.application.project_service import ProjectService
from app.application.scan_service import NullScanTaskDispatcher, ScanService
from app.application.scope_guard_service import ScopeGuardService
from app.domain.entities import AuthorizationRecord, Target, User
from app.domain.value_objects import AuthorizationStatus, ProjectRole, TargetType
from app.main import create_app
from app.plugins.echo_plugin import EchoPlugin
from app.plugins.manager import PluginManager
from app.plugins.nmap_plugin import NmapPlugin
from app.plugins.registry import PluginRegistry
from tests.fakes import (
    FakeAuthorizationRecordRepository,
    FakeOrganizationRepository,
    FakeProjectRepository,
    FakeScanRepository,
    FakeTargetRepository,
)


@pytest_asyncio.fixture
async def setup():
    app = create_app()

    org_repo = FakeOrganizationRepository()
    project_repo = FakeProjectRepository()
    target_repo = FakeTargetRepository()
    auth_repo = FakeAuthorizationRecordRepository()
    scan_repo = FakeScanRepository()

    org_service = OrganizationService(org_repo)
    project_service = ProjectService(project_repo, auth_repo)
    scope_guard = ScopeGuardService(project_repo, target_repo, auth_repo)

    registry = PluginRegistry()
    registry.register(EchoPlugin())
    registry.register(NmapPlugin())
    plugin_manager = PluginManager(registry)

    scan_service = ScanService(scan_repo, scope_guard, plugin_manager, NullScanTaskDispatcher())

    owner = User(
        id=uuid4(),
        email="owner@example.com",
        password_hash="unused",
        full_name="Owner",
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
    outsider = User(
        id=uuid4(),
        email="outsider@example.com",
        password_hash="unused",
        full_name="Outsider",
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
    await project_service.add_member(project.id, read_only_member.id, ProjectRole.READ_ONLY)

    now = datetime.now(UTC)
    target = Target(
        id=uuid4(),
        project_id=project.id,
        value="10.0.0.5",
        target_type=TargetType.IP,
        in_scope=True,
        created_at=now,
        updated_at=now,
    )
    await target_repo.add(target)

    # Move the project through Authorized -> Active with a valid record.
    from app.domain.value_objects import ProjectState

    await project_service.transition_state(project.id, ProjectState.AUTHORIZED)
    record = AuthorizationRecord(
        id=uuid4(),
        project_id=project.id,
        client_name="Acme",
        document_reference="doc.pdf",
        authorized_from=date.today() - timedelta(days=1),
        authorized_to=date.today() + timedelta(days=30),
        allowed_targets=[target.value],
        approved_by=owner.id,
        status=AuthorizationStatus.ACTIVE,
        scope_notes=None,
        evidence_pointer=None,
        created_at=now,
    )
    await auth_repo.add(record)
    await project_service.transition_state(project.id, ProjectState.ACTIVE)

    app.dependency_overrides[get_organization_service] = lambda: org_service
    app.dependency_overrides[get_project_service] = lambda: project_service
    app.dependency_overrides[get_scan_service] = lambda: scan_service

    return {
        "app": app,
        "project": project,
        "target": target,
        "owner": owner,
        "read_only_member": read_only_member,
        "outsider": outsider,
    }


def _client_as(app, user: User) -> AsyncClient:
    app.dependency_overrides[get_current_user] = lambda: user
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://testserver")


@pytest.mark.asyncio
async def test_owner_can_launch_echo_scan(setup):
    async with _client_as(setup["app"], setup["owner"]) as client:
        response = await client.post(
            f"/api/v1/projects/{setup['project'].id}/scans",
            json={"plugin": "echo", "plugin_config": {}, "target_ids": [str(setup["target"].id)]},
        )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "queued"
    assert body["plugin"] == "echo"


@pytest.mark.asyncio
async def test_read_only_member_cannot_launch_scan(setup):
    async with _client_as(setup["app"], setup["read_only_member"]) as client:
        response = await client.post(
            f"/api/v1/projects/{setup['project'].id}/scans",
            json={"plugin": "echo", "plugin_config": {}, "target_ids": [str(setup["target"].id)]},
        )
    assert response.status_code == 403
    assert response.json()["type"] == "https://specter.ai/errors/insufficient-permission"


@pytest.mark.asyncio
async def test_outsider_cannot_launch_scan(setup):
    async with _client_as(setup["app"], setup["outsider"]) as client:
        response = await client.post(
            f"/api/v1/projects/{setup['project'].id}/scans",
            json={"plugin": "echo", "plugin_config": {}, "target_ids": [str(setup["target"].id)]},
        )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_out_of_scope_target_returns_422(setup):
    other_target_id = uuid4()  # never added to any project
    async with _client_as(setup["app"], setup["owner"]) as client:
        response = await client.post(
            f"/api/v1/projects/{setup['project'].id}/scans",
            json={"plugin": "echo", "plugin_config": {}, "target_ids": [str(other_target_id)]},
        )
    assert response.status_code == 404  # TargetNotFoundError — not this project's target


@pytest.mark.asyncio
async def test_disallowed_nmap_argument_returns_422(setup):
    async with _client_as(setup["app"], setup["owner"]) as client:
        response = await client.post(
            f"/api/v1/projects/{setup['project'].id}/scans",
            json={
                "plugin": "nmap",
                "plugin_config": {
                    "target": "10.0.0.5",
                    "ports": "80",
                    "arguments": ["--script=evil"],
                },
                "target_ids": [str(setup["target"].id)],
            },
        )
    assert response.status_code == 422
    assert response.json()["type"] == "https://specter.ai/errors/invalid-plugin-config"


@pytest.mark.asyncio
async def test_unknown_plugin_returns_404(setup):
    async with _client_as(setup["app"], setup["owner"]) as client:
        response = await client.post(
            f"/api/v1/projects/{setup['project'].id}/scans",
            json={"plugin": "sqlmap", "plugin_config": {}, "target_ids": [str(setup["target"].id)]},
        )
    assert response.status_code == 404
    assert response.json()["type"] == "https://specter.ai/errors/plugin-not-found"


@pytest.mark.asyncio
async def test_get_and_list_and_cancel_lifecycle(setup):
    async with _client_as(setup["app"], setup["owner"]) as client:
        create_response = await client.post(
            f"/api/v1/projects/{setup['project'].id}/scans",
            json={"plugin": "echo", "plugin_config": {}, "target_ids": [str(setup["target"].id)]},
        )
        scan_id = create_response.json()["id"]

        get_response = await client.get(f"/api/v1/scans/{scan_id}")
        assert get_response.status_code == 200
        assert get_response.json()["id"] == scan_id

        list_response = await client.get(f"/api/v1/projects/{setup['project'].id}/scans")
        assert list_response.status_code == 200
        assert len(list_response.json()) == 1

        cancel_response = await client.delete(f"/api/v1/scans/{scan_id}")
        assert cancel_response.status_code == 200
        assert cancel_response.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_read_only_member_can_view_but_not_cancel_scan(setup):
    async with _client_as(setup["app"], setup["owner"]) as client:
        create_response = await client.post(
            f"/api/v1/projects/{setup['project'].id}/scans",
            json={"plugin": "echo", "plugin_config": {}, "target_ids": [str(setup["target"].id)]},
        )
        scan_id = create_response.json()["id"]

    async with _client_as(setup["app"], setup["read_only_member"]) as client:
        get_response = await client.get(f"/api/v1/scans/{scan_id}")
        assert get_response.status_code == 200

        cancel_response = await client.delete(f"/api/v1/scans/{scan_id}")
        assert cancel_response.status_code == 403


@pytest.mark.asyncio
async def test_get_nonexistent_scan_returns_404(setup):
    async with _client_as(setup["app"], setup["owner"]) as client:
        response = await client.get(f"/api/v1/scans/{uuid4()}")
    assert response.status_code == 404
    assert response.json()["type"] == "https://specter.ai/errors/scan-not-found"


@pytest.mark.asyncio
async def test_unauthenticated_scan_launch_returns_401(setup):
    app = setup["app"]
    app.dependency_overrides.pop(get_current_user, None)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            f"/api/v1/projects/{setup['project'].id}/scans",
            json={"plugin": "echo", "plugin_config": {}, "target_ids": [str(setup["target"].id)]},
        )
    assert response.status_code == 401
