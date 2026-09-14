"""
M7.5 Phase 2 — Autonomous Run Project Isolation (API-level).

Regression tests for the cross-project control-plane authorization
vulnerability fixed in this phase: run-scoped autonomous routes used to
authorize against a *caller-supplied* ``?project_id=`` query parameter,
so a user authorized in Project A could manipulate Project B runs by
supplying B's project_id.

After the fix the run/action resource itself is authoritative:

    caller -> load AutonomousRun(run_id) -> run.project_id -> authorize

and actions are authorized through their parent run only (never
independently). The ``?project_id=`` query parameter is ignored on
run-scoped routes; it remains the *legitimate* resource scoping on the
path-scoped create/list routes.

Like `test_permissions_api.py`, these run against the real ASGI app with
in-memory service/repo fakes injected via dependency overrides — no
database required.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.v1.deps import (
    get_autonomous_action_repository,
    get_autonomous_orchestrator,
    get_autonomous_service,
    get_current_user,
    get_organization_service,
    get_project_service,
)
from app.application.autonomous_service import AutonomousService
from app.application.organization_service import OrganizationService
from app.application.project_service import ProjectService
from app.domain.entities import AutonomousRun, AutonomousRunAction, User
from app.domain.value_objects import (
    OrganizationRole,
    ProjectRole,
)
from app.main import create_app
from tests.fakes import (
    FakeAuthorizationRecordRepository,
    FakeAutonomousRunActionRepository,
    FakeAutonomousRunRepository,
    FakeOrganizationRepository,
    FakeProjectRepository,
)

_NOT_A_PROJECT_MEMBER = "https://specter.ai/errors/not-a-project-member"
_INSUFFICIENT_PERMISSION = "https://specter.ai/errors/insufficient-permission"
_DOMAIN_ERROR = "https://specter.ai/errors/domain-error"


class _BombOrchestrator:
    """Stands in for the real orchestrator — must never run in these tests."""

    def __init__(self) -> None:
        self.cycle_calls = 0

    async def cycle(self, run_id: UUID) -> object:  # pragma: no cover
        self.cycle_calls += 1
        raise AssertionError("orchestrator.cycle must never be reached")


def _user(email: str, name: str) -> User:
    return User(
        id=uuid4(),
        email=email,
        password_hash="unused",
        full_name=name,
        is_active=True,
        created_at=datetime.now(UTC),
    )


async def _make_action(
    action_repo: FakeAutonomousRunActionRepository,
    run: AutonomousRun,
    project_id: UUID,
) -> AutonomousRunAction:
    action = AutonomousRunAction(
        id=uuid4(),
        run_id=run.id,
        project_id=project_id,
        cycle=1,
        action_type="scan",
        title="Ping sweep",
        status="proposed",
        created_at=datetime.now(UTC),
    )
    await action_repo.create(action)
    return action


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
    rita = _user("rita@example.com", "Rita")
    eve = _user("eve@example.com", "Eve")
    oliver = _user("oliver@example.com", "Oliver")

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

    # create(owner_user_id=...) already adds the owner as ProjectRole.OWNER.
    # Read-only member (also org admin of org A) — membership, not control.
    await org_service.add_member(org_a.id, rita.id, OrganizationRole.ADMIN)
    await project_service.add_member(project_a.id, rita.id, ProjectRole.READ_ONLY)
    # Oliver is an org admin of org A but no project membership anywhere.
    await org_service.add_member(org_a.id, oliver.id, OrganizationRole.ADMIN)

    # Autonomous service backed entirely by in-memory fakes.
    run_repo = FakeAutonomousRunRepository()
    action_repo = FakeAutonomousRunActionRepository()
    run_repo._action_runs = []
    action_repo.set_run_repo(run_repo)
    autonomous = AutonomousService(run_repo=run_repo, action_repo=action_repo)

    run_a = await autonomous.create(
        project_id=project_a.id,
        initiated_by=alice.id,
        objective="isolate-me-a",
        max_actions=5,
        max_runtime_seconds=300,
    )
    run_b = await autonomous.create(
        project_id=project_b.id,
        initiated_by=bob.id,
        objective="isolate-me-b",
        max_actions=5,
        max_runtime_seconds=300,
    )
    run_c = await autonomous.create(
        project_id=project_c.id,
        initiated_by=eve.id,
        objective="isolate-me-c",
        max_actions=5,
        max_runtime_seconds=300,
    )

    # One proposed action per project + one deliberately mismatched action
    # (run belongs to A, action's denormalized project_id points at C).
    action_a = await _make_action(action_repo, run_a, project_a.id)
    action_b = await _make_action(action_repo, run_b, project_b.id)
    mismatched_action = await _make_action(action_repo, run_a, project_c.id)

    app.dependency_overrides[get_organization_service] = lambda: org_service
    app.dependency_overrides[get_project_service] = lambda: project_service
    app.dependency_overrides[get_autonomous_service] = lambda: autonomous
    app.dependency_overrides[get_autonomous_action_repository] = lambda: action_repo
    app.dependency_overrides[get_autonomous_orchestrator] = lambda: _BombOrchestrator()

    return {
        "app": app,
        "org_a": org_a,
        "project_a": project_a,
        "project_b": project_b,
        "project_c": project_c,
        "alice": alice,
        "bob": bob,
        "rita": rita,
        "eve": eve,
        "oliver": oliver,
        "run_a": run_a,
        "run_b": run_b,
        "run_c": run_c,
        "action_a": action_a,
        "action_b": action_b,
        "mismatched_action": mismatched_action,
    }


def _client_as(app, user: User) -> AsyncClient:
    app.dependency_overrides[get_current_user] = lambda: user
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://testserver")


# ── Read (membership-only gate) ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_alice_can_get_own_run_without_project_id_param(setup):
    """The run-scoped GET no longer requires (or consults) ?project_id=."""
    async with _client_as(setup["app"], setup["alice"]) as client:
        resp = await client.get(f"/api/v1/autonomous-runs/{setup['run_a'].id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == str(setup["run_a"].id)


@pytest.mark.asyncio
async def test_alice_get_ignores_wrong_project_id_param(setup):
    """Passing another project's id as ?project_id= cannot worsen the result."""
    async with _client_as(setup["app"], setup["alice"]) as client:
        resp = await client.get(
            f"/api/v1/autonomous-runs/{setup['run_a'].id}",
            params={"project_id": setup["project_b"].id},
        )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_bob_cannot_get_alice_run_even_with_alice_project_id(setup):
    """The credential-sneak attempt: bob passes project A's id, must still 403."""
    async with _client_as(setup["app"], setup["bob"]) as client:
        resp = await client.get(
            f"/api/v1/autonomous-runs/{setup['run_a'].id}",
            params={"project_id": setup["project_a"].id},
        )
    assert resp.status_code == 403, resp.text
    assert resp.json()["type"] == _NOT_A_PROJECT_MEMBER


@pytest.mark.asyncio
async def test_bob_cannot_get_alice_run_without_params(setup):
    async with _client_as(setup["app"], setup["bob"]) as client:
        resp = await client.get(f"/api/v1/autonomous-runs/{setup['run_a'].id}")
    assert resp.status_code == 403, resp.text
    assert resp.json()["type"] == _NOT_A_PROJECT_MEMBER


@pytest.mark.asyncio
async def test_other_org_owner_cannot_get_run(setup):
    async with _client_as(setup["app"], setup["eve"]) as client:
        resp = await client.get(f"/api/v1/autonomous-runs/{setup['run_a'].id}")
    assert resp.status_code == 403, resp.text
    assert resp.json()["type"] == _NOT_A_PROJECT_MEMBER


@pytest.mark.asyncio
async def test_org_admin_without_project_membership_cannot_get_run(setup):
    """Org-admin must not bypass project membership (mirrors require_project_role)."""
    async with _client_as(setup["app"], setup["oliver"]) as client:
        resp = await client.get(f"/api/v1/autonomous-runs/{setup['run_a'].id}")
    assert resp.status_code == 403, resp.text
    assert resp.json()["type"] == _NOT_A_PROJECT_MEMBER


# ── Control ops (Owner/Admin gate on run.project_id) ────────────────────────


@pytest.mark.asyncio
async def test_alice_can_cancel_own_run(setup):
    async with _client_as(setup["app"], setup["alice"]) as client:
        resp = await client.post(f"/api/v1/autonomous-runs/{setup['run_a'].id}/cancel")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_bob_cannot_cancel_alice_run(setup):
    async with _client_as(setup["app"], setup["bob"]) as client:
        resp = await client.post(
            f"/api/v1/autonomous-runs/{setup['run_a'].id}/cancel",
            params={"project_id": setup["project_a"].id},
        )
    assert resp.status_code == 403, resp.text
    assert resp.json()["type"] == _NOT_A_PROJECT_MEMBER
    # The run must be untouched from its owner's perspective.
    async with _client_as(setup["app"], setup["alice"]) as client:
        get_resp = await client.get(f"/api/v1/autonomous-runs/{setup['run_a'].id}")
    assert get_resp.json()["status"] == "created"


@pytest.mark.asyncio
async def test_control_ops_denied_to_other_project_owner(setup):
    """Every intended control op on run_a by bob -> 403, ownership unchanged."""
    ops = [
        "start-planning",
        "plan-complete",
        "cycle",
        "approve",
        "execution-complete",
        "observation-complete?should_continue=true",
        "heartbeat",
    ]
    async with _client_as(setup["app"], setup["bob"]) as client:
        for op in ops:
            resp = await client.post(
                f"/api/v1/autonomous-runs/{setup['run_a'].id}/{op}",
                params={"project_id": setup["project_a"].id},
            )
            assert resp.status_code == 403, f"{op}: got {resp.status_code} {resp.text}"
            assert resp.json()["type"] == _NOT_A_PROJECT_MEMBER, op
    async with _client_as(setup["app"], setup["alice"]) as client:
        get_resp = await client.get(f"/api/v1/autonomous-runs/{setup['run_a'].id}")
    assert get_resp.json()["status"] == "created"


@pytest.mark.asyncio
async def test_read_only_member_can_read_but_not_control(setup):
    async with _client_as(setup["app"], setup["rita"]) as client:
        get_resp = await client.get(f"/api/v1/autonomous-runs/{setup['run_a'].id}")
    assert get_resp.status_code == 200, get_resp.text
    async with _client_as(setup["app"], setup["rita"]) as client:
        cancel_resp = await client.post(f"/api/v1/autonomous-runs/{setup['run_a'].id}/cancel")
    assert cancel_resp.status_code == 403, cancel_resp.text
    assert cancel_resp.json()["type"] == _INSUFFICIENT_PERMISSION


@pytest.mark.asyncio
async def test_bob_cannot_control_his_own_project_via_error_setup(setup):
    """Sanity: bob CAN control his own project's run (positive control)."""
    async with _client_as(setup["app"], setup["bob"]) as client:
        resp = await client.post(f"/api/v1/autonomous-runs/{setup['run_b'].id}/cancel")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "cancelled"


# ── Action routes (authorized via parent run only) ──────────────────────────


@pytest.mark.asyncio
async def test_alice_can_approve_own_action(setup):
    async with _client_as(setup["app"], setup["alice"]) as client:
        resp = await client.post(f"/api/v1/autonomous-actions/{setup['action_a'].id}/approve")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "approved"


@pytest.mark.asyncio
async def test_bob_cannot_approve_alice_action_even_with_project_id(setup):
    async with _client_as(setup["app"], setup["bob"]) as client:
        resp = await client.post(
            f"/api/v1/autonomous-actions/{setup['action_a'].id}/approve",
            params={"project_id": setup["project_a"].id},
        )
    assert resp.status_code == 403, resp.text
    assert resp.json()["type"] == _NOT_A_PROJECT_MEMBER
    # Action must still be proposed for its owner.
    async with _client_as(setup["app"], setup["alice"]) as client:
        list_resp = await client.get(f"/api/v1/autonomous-runs/{setup['run_a'].id}/actions")
    assert list_resp.status_code == 200
    statuses = {a["id"]: a["status"] for a in list_resp.json()}
    assert statuses[str(setup["action_a"].id)] == "proposed"


@pytest.mark.asyncio
async def test_bob_cannot_reject_bob_run_via_alice_identity(setup):
    """Alice must not reach into bob's project's action for approve/reject."""
    async with _client_as(setup["app"], setup["alice"]) as client:
        for op in ("approve", "reject"):
            resp = await client.post(
                f"/api/v1/autonomous-actions/{setup['action_b'].id}/{op}",
                params={"project_id": setup["project_b"].id},
            )
            assert resp.status_code == 403, f"{op}: {resp.status_code} {resp.text}"


@pytest.mark.asyncio
async def test_other_org_owner_cannot_approve_action(setup):
    async with _client_as(setup["app"], setup["eve"]) as client:
        resp = await client.post(f"/api/v1/autonomous-actions/{setup['action_a'].id}/approve")
    assert resp.status_code == 403, resp.text
    assert resp.json()["type"] == _NOT_A_PROJECT_MEMBER


@pytest.mark.asyncio
async def test_mismatched_action_project_id_rejected(setup):
    """Defense-in-depth: action.project_id != run.project_id is refused even
    for the run's own owner (data-integrity mark of a forged action)."""
    async with _client_as(setup["app"], setup["alice"]) as client:
        resp = await client.post(
            f"/api/v1/autonomous-actions/{setup['mismatched_action'].id}/approve"
        )
    assert resp.status_code == 400, resp.text
    assert resp.json()["type"] == _DOMAIN_ERROR


# ── Nonexistent resources ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_nonexistent_run_returns_domain_error(setup):
    async with _client_as(setup["app"], setup["alice"]) as client:
        resp = await client.get(f"/api/v1/autonomous-runs/{uuid4()}")
    assert resp.status_code == 400, resp.text
    assert resp.json()["type"] == _DOMAIN_ERROR


@pytest.mark.asyncio
async def test_nonexistent_action_returns_404(setup):
    async with _client_as(setup["app"], setup["alice"]) as client:
        resp = await client.post(f"/api/v1/autonomous-actions/{uuid4()}/approve")
    assert resp.status_code == 404, resp.text
    assert resp.json()["type"] == "https://specter.ai/errors/planned-action-not-found"


# ── Path-scoped create/list routes stay legitimately project-scoped ─────────


@pytest.mark.asyncio
async def test_create_still_respects_path_project_id(setup):
    """Create is scoped by the path project_id (the resource is created
    there) — a non-member must still be refused."""
    async with _client_as(setup["app"], setup["bob"]) as client:
        resp = await client.post(
            f"/api/v1/projects/{setup['project_a'].id}/autonomous-runs",
            json={"objective": "sneak", "max_actions": 2},
        )
    assert resp.status_code == 403, resp.text
    assert resp.json()["type"] == _NOT_A_PROJECT_MEMBER


@pytest.mark.asyncio
async def test_list_still_respects_path_project_id(setup):
    async with _client_as(setup["app"], setup["bob"]) as client:
        resp = await client.get(f"/api/v1/projects/{setup['project_a'].id}/autonomous-runs")
    assert resp.status_code == 403, resp.text
    assert resp.json()["type"] == _NOT_A_PROJECT_MEMBER


@pytest.mark.asyncio
async def test_owner_can_list_own_project_runs(setup):
    async with _client_as(setup["app"], setup["alice"]) as client:
        resp = await client.get(f"/api/v1/projects/{setup['project_a'].id}/autonomous-runs")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    items = body.get("items", body)
    ids = {str(i["id"]) for i in items}
    assert str(setup["run_a"].id) in ids
    assert str(setup["run_b"].id) not in ids
