"""
M7.5 Phase 3 — Campaign Schedules (API-level).

Covers the new API surface added for scheduled autonomous campaigns:

- POST /api/v1/projects/{project_id}/schedules with kind=campaign
  (payload validation, creation, RBAC via scan-launch gate)
- resource-first authorization for GET /schedules/{schedule_id}
  (the ``?project_id=`` query param is ignored — the schedule's owning
  project is authoritative, fixing the cross-project read hole)

Like `test_permissions_api.py` and `test_m75_phase2_run_isolation_api.py`,
these run against the real ASGI app with in-memory service/repo fakes
injected via dependency overrides — no database required.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.v1.deps import (
    get_current_user,
    get_organization_service,
    get_project_service,
    get_schedule_service,
)
from app.application.organization_service import OrganizationService
from app.application.project_service import ProjectService
from app.application.schedule_service import ScheduleService
from app.domain.entities import CampaignScheduleConfig, User
from app.domain.value_objects import (
    OrganizationRole,
    ProjectRole,
    ScheduleFrequency,
    ScheduleKind,
    WorkflowStatus,
)
from app.main import create_app
from tests.fakes import (
    FakeAuthorizationRecordRepository,
    FakeOrganizationRepository,
    FakeProjectRepository,
    FakeScheduleRepository,
    FakeWorkflowRepository,
)

_NOT_A_PROJECT_MEMBER = "https://specter.ai/errors/not-a-project-member"
_INSUFFICIENT_PERMISSION = "https://specter.ai/errors/insufficient-permission"
_INVALID_SCHEDULE_CONFIG = "https://specter.ai/errors/invalid-schedule-config"


def _user(email: str, name: str) -> User:
    return User(
        id=uuid4(),
        email=email,
        password_hash="unused",
        full_name=name,
        is_active=True,
        created_at=datetime.now(UTC),
    )


@pytest_asyncio.fixture
async def setup():
    app = create_app()

    org_repo = FakeOrganizationRepository()
    project_repo = FakeProjectRepository()
    auth_repo = FakeAuthorizationRecordRepository()
    org_service = OrganizationService(org_repo)
    project_service = ProjectService(project_repo, auth_repo)

    alice = _user("alice@example.com", "Alice")
    bob = _user("bob@example.com", "Bob")
    eve = _user("eve@example.com", "Eve")

    org_a = await org_service.create("Acme Security", alice.id)
    org_b = await org_service.create("Rival Corp", eve.id)

    project_a = await project_service.create(
        organization_id=org_a.id,
        name="Project A",
        description=None,
        tags=None,
        client_metadata=None,
        owner_user_id=alice.id,
    )
    project_b = await project_service.create(
        organization_id=org_a.id,
        name="Project B",
        description=None,
        tags=None,
        client_metadata=None,
        owner_user_id=bob.id,
    )
    project_c = await project_service.create(
        organization_id=org_b.id,
        name="Project C",
        description=None,
        tags=None,
        client_metadata=None,
        owner_user_id=eve.id,
    )

    # rita is a read-only member of A (org MEMBER only — not an admin,
    # so the org-admin override in the scan-launch gate does not apply).
    rita = _user("rita@example.com", "Rita")
    await org_service.add_member(org_a.id, rita.id, OrganizationRole.MEMBER)
    await project_service.add_member(project_a.id, rita.id, ProjectRole.READ_ONLY)
    # oliver is org admin of A but has NO project membership anywhere.
    oliver = _user("oliver@example.com", "Oliver")
    await org_service.add_member(org_a.id, oliver.id, OrganizationRole.ADMIN)

    # Schedule service with fully in-memory fakes.
    workflow_repo = FakeWorkflowRepository()
    schedule_repo = FakeScheduleRepository()
    schedule_service = ScheduleService(schedule_repo, workflow_repo)

    workflow_a = _make_workflow(project_a.id, alice.id)
    await workflow_repo.create(workflow_a)
    sched_workflow = await schedule_service.create(
        workflow_id=workflow_a.id,
        project_id=project_a.id,
        frequency=ScheduleFrequency.HOURLY,
        created_by=alice.id,
    )

    app.dependency_overrides[get_organization_service] = lambda: org_service
    app.dependency_overrides[get_project_service] = lambda: project_service
    app.dependency_overrides[get_schedule_service] = lambda: schedule_service

    return {
        "app": app,
        "org_a": org_a,
        "org_b": org_b,
        "project_a": project_a,
        "project_b": project_b,
        "project_c": project_c,
        "alice": alice,
        "bob": bob,
        "rita": rita,
        "eve": eve,
        "oliver": oliver,
        "schedule_service": schedule_service,
        "schedule_repo": schedule_repo,
        "workflow_repo": workflow_repo,
        "sched_workflow": sched_workflow,
    }


def _make_workflow(project_id: UUID, creator: UUID):
    from app.domain.entities import Workflow

    return Workflow(
        id=uuid4(),
        project_id=project_id,
        name="Recon",
        description=None,
        status=WorkflowStatus.ACTIVE,
        created_by=creator,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _client_as(app, user: User) -> AsyncClient:
    app.dependency_overrides[get_current_user] = lambda: user
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://testserver")


def _campaign_body():
    return {
        "frequency": "once",
        "kind": "campaign",
        "campaign": {
            "objective": "enumerate externally reachable services",
            "max_actions": 10,
            "max_runtime_seconds": 3600,
        },
    }


# ── create (scan-launch gate) ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_owner_can_create_campaign_schedule(setup):
    async with _client_as(setup["app"], setup["alice"]) as client:
        resp = await client.post(
            f"/api/v1/projects/{setup['project_a'].id}/schedules",
            json=_campaign_body(),
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["kind"] == "campaign"
    assert body["workflow_id"] is None
    assert body["campaign_config"]["objective"] == "enumerate externally reachable services"
    assert body["campaign_config"]["max_actions"] == 10
    assert body["campaign_config"]["max_runtime_seconds"] == 3600


@pytest.mark.asyncio
async def test_second_org_owner_cannot_create_campaign_schedule(setup):
    async with _client_as(setup["app"], setup["eve"]) as client:
        resp = await client.post(
            f"/api/v1/projects/{setup['project_a'].id}/schedules",
            json=_campaign_body(),
        )
    assert resp.status_code == 403, resp.text
    assert resp.json()["type"] == _INSUFFICIENT_PERMISSION


@pytest.mark.asyncio
async def test_org_admin_without_membership_can_create_campaign_schedule(setup):
    """The scan-launch gate deliberately allows org Owner/Admin even
    without project membership (org-level oversight) — same rule as
    launching a scan. This is why the schedule-scoped GET is the one
    that enforces strict project membership (see the resource-first
    tests below)."""
    async with _client_as(setup["app"], setup["oliver"]) as client:
        resp = await client.post(
            f"/api/v1/projects/{setup['project_a'].id}/schedules",
            json=_campaign_body(),
        )
    assert resp.status_code == 201, resp.text
    assert resp.json()["kind"] == "campaign"


@pytest.mark.asyncio
async def test_read_only_member_cannot_create_campaign_schedule(setup):
    async with _client_as(setup["app"], setup["rita"]) as client:
        resp = await client.post(
            f"/api/v1/projects/{setup['project_a'].id}/schedules",
            json=_campaign_body(),
        )
    assert resp.status_code == 403, resp.text
    assert resp.json()["type"] == _INSUFFICIENT_PERMISSION


# ── create validation (kind/campaign/wf exclusivity) ─────────────────────────


@pytest.mark.asyncio
async def test_campaign_with_workflow_id_rejected(setup):
    body = _campaign_body()
    body["workflow_id"] = str(uuid4())
    async with _client_as(setup["app"], setup["alice"]) as client:
        resp = await client.post(
            f"/api/v1/projects/{setup['project_a'].id}/schedules",
            json=body,
        )
    assert resp.status_code in (400, 422), resp.text
    assert resp.json()["type"] == _INVALID_SCHEDULE_CONFIG


@pytest.mark.asyncio
async def test_campaign_without_payload_rejected(setup):
    body = _campaign_body()
    body["campaign"] = None
    async with _client_as(setup["app"], setup["alice"]) as client:
        resp = await client.post(
            f"/api/v1/projects/{setup['project_a'].id}/schedules",
            json=body,
        )
    assert resp.status_code in (400, 422), resp.text
    assert resp.json()["type"] == _INVALID_SCHEDULE_CONFIG


@pytest.mark.asyncio
async def test_campaign_out_of_bounds_max_actions_rejected(setup):
    body = _campaign_body()
    body["campaign"]["max_actions"] = 0
    async with _client_as(setup["app"], setup["alice"]) as client:
        resp = await client.post(
            f"/api/v1/projects/{setup['project_a'].id}/schedules",
            json=body,
        )
    # Pydantic schema bounds fire first (422), a 400 would also be fine.
    assert resp.status_code in (400, 422), resp.text


@pytest.mark.asyncio
async def test_workflow_schedule_defaults_to_workflow_kind(setup):
    wf = next(iter(setup["workflow_repo"]._workflows.values()))
    body = {
        "frequency": "once",
        "workflow_id": str(wf.id),
    }
    async with _client_as(setup["app"], setup["alice"]) as client:
        resp = await client.post(
            f"/api/v1/projects/{setup['project_a'].id}/schedules",
            json=body,
        )
    assert resp.status_code == 201, resp.text
    assert resp.json()["kind"] == "workflow"


# ── cross-project IDOR fix on schedule-scoped GET ────────────────────────────


@pytest.mark.asyncio
async def test_owner_can_get_own_schedule_no_project_param(setup):
    sid = setup["sched_workflow"].id
    async with _client_as(setup["app"], setup["alice"]) as client:
        resp = await client.get(f"/api/v1/schedules/{sid}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == str(sid)


@pytest.mark.asyncio
async def test_bob_cannot_get_alice_schedule_with_alice_project_param(setup):
    """Credential-sneak: bob passes project A's id, must still be refused."""
    sid = setup["sched_workflow"].id
    async with _client_as(setup["app"], setup["bob"]) as client:
        resp = await client.get(
            f"/api/v1/schedules/{sid}",
            params={"project_id": setup["project_a"].id},
        )
    assert resp.status_code == 403, resp.text
    assert resp.json()["type"] == _NOT_A_PROJECT_MEMBER


@pytest.mark.asyncio
async def test_other_org_owner_cannot_get_alice_schedule(setup):
    sid = setup["sched_workflow"].id
    async with _client_as(setup["app"], setup["eve"]) as client:
        resp = await client.get(f"/api/v1/schedules/{sid}")
    assert resp.status_code == 403, resp.text
    assert resp.json()["type"] == _NOT_A_PROJECT_MEMBER


@pytest.mark.asyncio
async def test_org_admin_without_membership_cannot_get_schedule(setup):
    sid = setup["sched_workflow"].id
    async with _client_as(setup["app"], setup["oliver"]) as client:
        resp = await client.get(f"/api/v1/schedules/{sid}")
    assert resp.status_code == 403, resp.text
    assert resp.json()["type"] == _NOT_A_PROJECT_MEMBER


@pytest.mark.asyncio
async def test_read_only_member_can_read_schedule(setup):
    sid = setup["sched_workflow"].id
    async with _client_as(setup["app"], setup["rita"]) as client:
        resp = await client.get(f"/api/v1/schedules/{sid}")
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_bob_can_get_his_own_projects_schedule(setup):
    # Positive control: bob creates a schedule in his own project, then reads it.
    workflow_b = _make_workflow(setup["project_b"].id, setup["bob"].id)
    await setup["workflow_repo"].create(workflow_b)
    sched_b = await setup["schedule_service"].create(
        workflow_id=workflow_b.id,
        project_id=setup["project_b"].id,
        frequency=ScheduleFrequency.ONCE,
        created_by=setup["bob"].id,
    )
    async with _client_as(setup["app"], setup["bob"]) as client:
        resp = await client.get(f"/api/v1/schedules/{sched_b.id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == str(sched_b.id)


@pytest.mark.asyncio
async def test_nonexistent_schedule_returns_404(setup):
    async with _client_as(setup["app"], setup["alice"]) as client:
        resp = await client.get(f"/api/v1/schedules/{uuid4()}")
    assert resp.status_code == 404, resp.text
    assert resp.json()["type"] == "https://specter.ai/errors/schedule-not-found"


# ── list conversions ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_schedules_shows_campaign_configs(setup):
    created = await setup["schedule_service"].create(
        workflow_id=None,
        project_id=setup["project_a"].id,
        frequency=ScheduleFrequency.ONCE,
        created_by=setup["alice"].id,
        kind=ScheduleKind.CAMPAIGN,
        campaign=CampaignScheduleConfig(
            objective="find exposed endpoints",
            max_actions=5,
            max_runtime_seconds=600,
        ),
    )
    async with _client_as(setup["app"], setup["alice"]) as client:
        resp = await client.get(f"/api/v1/projects/{setup['project_a'].id}/schedules")
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    match = [i for i in items if i["id"] == str(created.id)]
    assert len(match) == 1
    assert match[0]["kind"] == "campaign"
    assert match[0]["campaign_config"]["objective"] == "find exposed endpoints"
