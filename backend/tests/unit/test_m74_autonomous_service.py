"""
M7.4 Phase 1 — Autonomous Orchestration Tests (A–T).

Verifies the AutonomousService CRUD + state machine, action lifecycle,
budget enforcement, concurrency guard, and domain entity contracts.
All tests run against fakes (no Postgres required).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.application.autonomous_service import AutonomousService
from app.domain.entities import AutonomousRun
from app.domain.exceptions import (
    AutonomousActionNotApprovableError,
    AutonomousRunActiveExistsError,
    AutonomousRunBudgetExceededError,
    AutonomousRunInvalidTransitionError,
    AutonomousRunNotCancellableError,
    AutonomousRunNotFoundError,
)
from app.domain.value_objects import (
    VALID_AUTONOMOUS_TRANSITIONS,
    ActionCategory,
    AutonomousRunStatus,
)
from tests.fakes import FakeAutonomousRunActionRepository, FakeAutonomousRunRepository

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pid() -> UUID:
    return uuid4()


def _uid() -> UUID:
    return uuid4()


class _FixedClock:
    """Deterministic clock for tests."""

    def __init__(self, now: datetime | None = None) -> None:
        self._now = now or datetime(2026, 8, 26, 12, 0, 0, tzinfo=UTC)

    def utcnow(self) -> datetime:
        return self._now

    def advance(self, seconds: int) -> None:
        self._now += timedelta(seconds=seconds)


def _svc(
    *,
    clock: _FixedClock | None = None,
    run_repo: FakeAutonomousRunRepository | None = None,
    action_repo: FakeAutonomousRunActionRepository | None = None,
) -> tuple[
    AutonomousService,
    FakeAutonomousRunRepository,
    FakeAutonomousRunActionRepository,
    _FixedClock,
]:
    clk = clock or _FixedClock()
    rr = run_repo or FakeAutonomousRunRepository()
    ar = action_repo or FakeAutonomousRunActionRepository()
    ar.set_run_repo(rr)
    return AutonomousService(run_repo=rr, action_repo=ar, clock=clk), rr, ar, clk


def _make_run(
    *,
    project_id: UUID | None = None,
    status: AutonomousRunStatus = AutonomousRunStatus.CREATED,
    initiated_by: UUID | None = None,
    started_at: datetime | None = None,
    max_actions: int = 20,
    max_runtime_seconds: int = 1800,
) -> AutonomousRun:
    now = datetime.now(UTC)
    return AutonomousRun(
        id=uuid4(),
        project_id=project_id or _pid(),
        initiated_by=initiated_by or _uid(),
        status=status,
        objective="test run",
        max_actions=max_actions,
        max_runtime_seconds=max_runtime_seconds,
        started_at=started_at,
        created_at=now,
    )


# ---------------------------------------------------------------------------
# A: Create run — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_create_run() -> None:
    svc, rr, _, _ = _svc()
    pid = _pid()
    uid = _uid()
    run = await svc.create(project_id=pid, initiated_by=uid, objective="recon")
    assert run.status == AutonomousRunStatus.CREATED
    assert run.project_id == pid
    assert run.initiated_by == uid
    assert run.objective == "recon"
    assert run.max_actions == 20
    assert run.max_runtime_seconds == 1800
    assert run.current_cycle == 0
    assert run.actions_completed == 0
    assert await rr.get(run.id) is not None


# ---------------------------------------------------------------------------
# B: Create run — concurrency guard (one active per project)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_b_create_run_rejects_concurrent() -> None:
    svc, rr, _, _ = _svc()
    pid = _pid()
    existing = _make_run(project_id=pid, status=AutonomousRunStatus.PLANNING)
    await rr.create(existing)
    with pytest.raises(AutonomousRunActiveExistsError):
        await svc.create(project_id=pid, initiated_by=_uid())


# ---------------------------------------------------------------------------
# C: Create run — different projects allowed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_c_create_run_different_projects() -> None:
    svc, _, _, _ = _svc()
    r1 = await svc.create(project_id=_pid(), initiated_by=_uid())
    r2 = await svc.create(project_id=_pid(), initiated_by=_uid())
    assert r1.id != r2.id


# ---------------------------------------------------------------------------
# D: Get run — found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_d_get_run_found() -> None:
    svc, rr, _, _ = _svc()
    run = _make_run()
    await rr.create(run)
    result = await svc.get(run.id)
    assert result.id == run.id


# ---------------------------------------------------------------------------
# E: Get run — not found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e_get_run_not_found() -> None:
    svc, _, _, _ = _svc()
    with pytest.raises(AutonomousRunNotFoundError):
        await svc.get(uuid4())


# ---------------------------------------------------------------------------
# F: Cancel run — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_f_cancel_run() -> None:
    svc, rr, _, _ = _svc()
    run = _make_run(status=AutonomousRunStatus.PLANNING)
    await rr.create(run)
    result = await svc.cancel(run.id)
    assert result.status == AutonomousRunStatus.CANCELLED


# ---------------------------------------------------------------------------
# G: Cancel run — rejects terminal state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_g_cancel_run_terminal_rejected() -> None:
    svc, rr, _, _ = _svc()
    run = _make_run(status=AutonomousRunStatus.COMPLETED)
    await rr.create(run)
    with pytest.raises(AutonomousRunNotCancellableError):
        await svc.cancel(run.id)


# ---------------------------------------------------------------------------
# H: Full lifecycle: CREATED → PLANNING → AWAITING_APPROVAL → EXECUTING → OBSERVING → COMPLETED
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_h_full_lifecycle() -> None:
    svc, rr, _, _ = _svc()
    run = _make_run()
    await rr.create(run)

    # CREATED → PLANNING
    run = await svc.start_planning(run.id)
    assert run.status == AutonomousRunStatus.PLANNING
    assert run.started_at is not None

    # PLANNING → AWAITING_APPROVAL
    run = await svc.plan_complete(run.id)
    assert run.status == AutonomousRunStatus.AWAITING_APPROVAL

    # AWAITING_APPROVAL → EXECUTING
    run = await svc.approval_granted(run.id)
    assert run.status == AutonomousRunStatus.EXECUTING

    # EXECUTING → OBSERVING
    run = await svc.execution_complete(run.id)
    assert run.status == AutonomousRunStatus.OBSERVING

    # OBSERVING → COMPLETED
    run = await svc.observation_complete(run.id, should_continue=False)
    assert run.status == AutonomousRunStatus.COMPLETED
    assert run.completed_at is not None


# ---------------------------------------------------------------------------
# I: Re-plan cycle: OBSERVING → PLANNING (with budget check)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_i_replan_cycle() -> None:
    svc, rr, _, _ = _svc()
    run = _make_run(max_actions=20, max_runtime_seconds=3600)
    run.actions_completed = 0
    await rr.create(run)

    # Manually set to OBSERVING
    run.status = AutonomousRunStatus.OBSERVING
    await rr.update(run)

    # should_continue=True → PLANNING (re-plan)
    run = await svc.observation_complete(run.id, should_continue=True)
    assert run.status == AutonomousRunStatus.PLANNING
    assert run.current_cycle == 1


# ---------------------------------------------------------------------------
# J: Re-plan budget exceeded (max_actions)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_j_replan_budget_exceeded_actions() -> None:
    svc, rr, _, _ = _svc()
    run = _make_run(max_actions=3, max_runtime_seconds=3600)
    run.actions_completed = 3
    await rr.create(run)

    run.status = AutonomousRunStatus.OBSERVING
    await rr.update(run)

    with pytest.raises(AutonomousRunBudgetExceededError):
        await svc.observation_complete(run.id, should_continue=True)


# ---------------------------------------------------------------------------
# K: Re-plan budget exceeded (max_runtime)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_k_replan_budget_exceeded_runtime() -> None:
    clock = _FixedClock()
    svc, rr, _, _ = _svc(clock=clock)
    run = _make_run(max_actions=20, max_runtime_seconds=60)
    run.started_at = clock.utcnow()
    await rr.create(run)

    clock.advance(61)
    run.status = AutonomousRunStatus.OBSERVING
    await rr.update(run)

    with pytest.raises(AutonomousRunBudgetExceededError):
        await svc.observation_complete(run.id, should_continue=True)


# ---------------------------------------------------------------------------
# L: Fail from any non-terminal state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_l_fail_run() -> None:
    svc, rr, _, _ = _svc()
    for state in [
        AutonomousRunStatus.CREATED,
        AutonomousRunStatus.PLANNING,
        AutonomousRunStatus.EXECUTING,
    ]:
        run = _make_run(status=state)
        await rr.create(run)
        result = await svc.fail(run.id, "something broke")
        assert result.status == AutonomousRunStatus.FAILED
        assert result.error_message == "something broke"
        assert result.completed_at is not None


# ---------------------------------------------------------------------------
# M: Fail from terminal state → rejected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_m_fail_run_terminal_rejected() -> None:
    svc, rr, _, _ = _svc()
    run = _make_run(status=AutonomousRunStatus.COMPLETED)
    await rr.create(run)
    with pytest.raises(AutonomousRunInvalidTransitionError):
        await svc.fail(run.id, "oops")


# ---------------------------------------------------------------------------
# N: Heartbeat
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_n_heartbeat() -> None:
    clock = _FixedClock()
    svc, rr, _, _ = _svc(clock=clock)
    run = _make_run()
    await rr.create(run)
    result = await svc.heartbeat(run.id)
    assert result.last_heartbeat_at == clock.utcnow()


# ---------------------------------------------------------------------------
# O: Propose / Approve / Reject actions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_o_action_lifecycle() -> None:
    svc, rr, ar, _ = _svc()
    run = _make_run()
    await rr.create(run)

    # Propose
    action = await svc.propose_action(
        run_id=run.id,
        cycle=1,
        action_type="scan",
        plugin="nmap",
        title="Scan target",
        category=ActionCategory.CATEGORY_1,
    )
    assert action.status == "proposed"
    assert action.plugin == "nmap"
    assert action.cycle == 1

    # Approve
    approver = _uid()
    action = await svc.approve_action(action.id, approved_by=approver)
    assert action.status == "approved"
    assert action.approved_by == approver
    assert action.approved_at is not None

    # Re-propose a new action and reject
    action2 = await svc.propose_action(
        run_id=run.id,
        cycle=1,
        action_type="scan",
        plugin="httpx",
        title="HTTP probe",
    )
    action2 = await svc.reject_action(action2.id, reason="not needed")
    assert action2.status == "rejected"
    assert action2.rejection_reason == "not needed"


# ---------------------------------------------------------------------------
# P: Approve non-proposed action → rejected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_p_approve_non_proposed_rejected() -> None:
    svc, rr, _, _ = _svc()
    run = _make_run()
    await rr.create(run)
    action = await svc.propose_action(run_id=run.id, cycle=1, action_type="scan")
    await svc.approve_action(action.id, approved_by=_uid())
    with pytest.raises(AutonomousActionNotApprovableError):
        await svc.approve_action(action.id, approved_by=_uid())


# ---------------------------------------------------------------------------
# Q: Record action execution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_q_record_action_execution() -> None:
    svc, rr, ar, _ = _svc()
    run = _make_run()
    run.actions_completed = 0
    await rr.create(run)

    action = await svc.propose_action(run_id=run.id, cycle=1, action_type="scan")
    scan_id = uuid4()
    action = await svc.record_action_execution(action.id, scan_id)
    assert action.status == "executed"
    assert action.scan_id == scan_id

    updated_run = await svc.get(run.id)
    assert updated_run.actions_completed == 1


# ---------------------------------------------------------------------------
# R: Invalid state transitions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_r_invalid_transitions() -> None:
    svc, rr, _, _ = _svc()

    # CREATED → EXECUTING (skip PLANNING/AWAITING)
    run = _make_run(status=AutonomousRunStatus.CREATED)
    await rr.create(run)
    with pytest.raises(AutonomousRunInvalidTransitionError):
        await svc.approval_granted(run.id)

    # PLANNING → OBSERVING (skip AWAITING/EXECUTING)
    run2 = _make_run(status=AutonomousRunStatus.PLANNING)
    await rr.create(run2)
    with pytest.raises(AutonomousRunInvalidTransitionError):
        await svc.execution_complete(run2.id)


# ---------------------------------------------------------------------------
# S: List actions by status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_s_list_actions_filtered() -> None:
    svc, rr, _, _ = _svc()
    run = _make_run()
    await rr.create(run)

    await svc.propose_action(run_id=run.id, cycle=1, action_type="scan", plugin="nmap")
    a2 = await svc.propose_action(run_id=run.id, cycle=1, action_type="scan", plugin="httpx")
    await svc.approve_action(a2.id, approved_by=_uid())

    proposed = await svc.list_actions(run.id, status="proposed")
    approved = await svc.list_actions(run.id, status="approved")
    assert len(proposed) == 1
    assert len(approved) == 1
    assert proposed[0].plugin == "nmap"
    assert approved[0].plugin == "httpx"


# ---------------------------------------------------------------------------
# T: Domain entity is_terminal / is_cancellable / can_transition_to
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t_entity_contract() -> None:
    # Terminal states
    for terminal in [
        AutonomousRunStatus.COMPLETED,
        AutonomousRunStatus.CANCELLED,
        AutonomousRunStatus.FAILED,
    ]:
        run = _make_run(status=terminal)
        assert run.is_terminal is True
        assert run.is_cancellable is False

    # Non-terminal states
    for non_terminal in [
        AutonomousRunStatus.CREATED,
        AutonomousRunStatus.PLANNING,
        AutonomousRunStatus.EXECUTING,
    ]:
        run = _make_run(status=non_terminal)
        assert run.is_terminal is False
        assert run.is_cancellable is True

    # Transition validation
    run = _make_run(status=AutonomousRunStatus.CREATED)
    assert run.can_transition_to(AutonomousRunStatus.PLANNING) is True
    assert run.can_transition_to(AutonomousRunStatus.EXECUTING) is False
    assert run.can_transition_to(AutonomousRunStatus.CANCELLED) is True

    # Exhaustive: every transition in VALID_AUTONOMOUS_TRANSITIONS is allowed
    for from_status, to_statuses in VALID_AUTONOMOUS_TRANSITIONS.items():
        run = _make_run(status=from_status)
        for to_status in to_statuses:
            assert run.can_transition_to(to_status) is True


# ---------------------------------------------------------------------------
# U: Auto-approve all proposed actions in approve_run endpoint logic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_u_auto_approve_actions() -> None:
    """Simulates the approve_run endpoint: approve all proposed, then transition."""
    svc, rr, _, _ = _svc()
    run = _make_run(status=AutonomousRunStatus.AWAITING_APPROVAL)
    await rr.create(run)

    approver = _uid()
    await svc.propose_action(run_id=run.id, cycle=0, action_type="scan", plugin="nmap")
    await svc.propose_action(run_id=run.id, cycle=0, action_type="scan", plugin="httpx")

    # Approve all
    actions = await svc.list_actions(run.id, status="proposed")
    for action in actions:
        await svc.approve_action(action.id, approved_by=approver)

    # Transition to EXECUTING
    run = await svc.approval_granted(run.id)
    assert run.status == AutonomousRunStatus.EXECUTING

    all_actions = await svc.list_actions(run.id)
    assert all(a.status == "approved" for a in all_actions)


# ---------------------------------------------------------------------------
# V: Propose action validates run exists
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v_propose_action_validates_run() -> None:
    svc, _, _, _ = _svc()
    with pytest.raises(AutonomousRunNotFoundError):
        await svc.propose_action(run_id=uuid4(), cycle=0, action_type="scan")


# ---------------------------------------------------------------------------
# W: List actions — nonexistent run returns empty
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_w_list_actions_empty() -> None:
    svc, rr, _, _ = _svc()
    run = _make_run()
    await rr.create(run)
    actions = await svc.list_actions(run.id)
    assert actions == []


# ---------------------------------------------------------------------------
# X: Fail preserves error_message and sets completed_at
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_x_fail_preserves_error_details() -> None:
    clock = _FixedClock()
    svc, rr, _, _ = _svc(clock=clock)
    run = _make_run(status=AutonomousRunStatus.EXECUTING)
    await rr.create(run)
    result = await svc.fail(run.id, "timeout after 300s")
    assert result.error_message == "timeout after 300s"
    assert result.completed_at == clock.utcnow()


# ---------------------------------------------------------------------------
# Y: Full cycle: create → plan → approve → execute → observe → re-plan → complete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_y_multi_cycle() -> None:
    svc, rr, _, _ = _svc()
    run = _make_run(max_actions=10, max_runtime_seconds=3600)
    run.actions_completed = 0
    await rr.create(run)

    # Cycle 0
    run = await svc.start_planning(run.id)
    assert run.status == AutonomousRunStatus.PLANNING

    # Propose + approve
    await svc.propose_action(run_id=run.id, cycle=0, action_type="scan", plugin="nmap")
    await svc.plan_complete(run.id)
    run = await svc.get(run.id)
    assert run.status == AutonomousRunStatus.AWAITING_APPROVAL

    # Approve + execute
    for action in await svc.list_actions(run.id, status="proposed"):
        await svc.approve_action(action.id, approved_by=_uid())
    run = await svc.approval_granted(run.id)
    assert run.status == AutonomousRunStatus.EXECUTING

    # Record execution
    for action in await svc.list_actions(run.id, status="approved"):
        await svc.record_action_execution(action.id, scan_id=uuid4())

    # Execute → Observe
    run = await svc.execution_complete(run.id)
    assert run.status == AutonomousRunStatus.OBSERVING

    # Observe → re-plan (cycle 1)
    run = await svc.observation_complete(run.id, should_continue=True)
    assert run.status == AutonomousRunStatus.PLANNING
    assert run.current_cycle == 1

    # Complete this time
    run = await svc.plan_complete(run.id)
    for action in await svc.list_actions(run.id, status="proposed"):
        await svc.approve_action(action.id, approved_by=_uid())
    run = await svc.approval_granted(run.id)
    for action in await svc.list_actions(run.id, status="approved"):
        await svc.record_action_execution(action.id, scan_id=uuid4())
    run = await svc.execution_complete(run.id)
    run = await svc.observation_complete(run.id, should_continue=False)
    assert run.status == AutonomousRunStatus.COMPLETED
    assert run.actions_completed >= 1
