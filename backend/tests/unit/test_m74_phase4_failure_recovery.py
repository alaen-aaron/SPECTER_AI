"""
M7.4 Phase 4 — Failure Recovery, Cancellation & Concurrency Tests (A–Z).

Covers (against deterministic fakes, no Postgres required):

  A–C  Failure taxonomy: retryable(), reapprove bridge, failure persistence.
  D–G  Action recording: idempotent increment, retry bookkeeping, guards.
  H–M  Recovery reconcile: TRANSPORT-only retry, budget, terminal, missing rows.
  N–P  Orchestrator cycle lock: reconcile ordering, lock-held fast fail, audit.
  Q–S  Cooperative cancellation: linked-scan soft cancel, back-compat, guards.
  T–W  Recovery supervisor: recover_stale advance / fail-closed / live leaves.
  X–Z  Retry lineage, no re-dispatch duplication, and an end-to-end smoke run.

Every assertion is deterministic and fail-closed; recovery never guesses.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.application.action_classifier import ActionClassificationPolicy
from app.application.autonomous_orchestrator import AutonomousOrchestrator
from app.application.autonomous_recovery import AutonomousRecoveryService
from app.application.autonomous_service import AutonomousService
from app.domain.entities import (
    AutonomousRun,
    AutonomousRunAction,
    PlannedAction,
    Scan,
)
from app.domain.exceptions import (
    AutonomousActionNotRetryableError,
    AutonomousCycleNotAllowedError,
    AutonomousRunNotCancellableError,
    PlannedActionNotApprovableError,
)
from app.domain.value_objects import (
    ActionCategory,
    AutonomousRunStatus,
    PlannedActionStatus,
    ScanFailureKind,
    ScanStatus,
)
from tests.fakes import (
    FakeAuditLogRepository,
    FakeAutonomousRunActionRepository,
    FakeAutonomousRunRepository,
    FakePlannerService,
    FakeScanLauncher,
    FakeScanRepository,
)

NOW = datetime(2026, 8, 27, 10, 0, 0, tzinfo=UTC)


class _FixedClock:
    def __init__(self, now: datetime = NOW) -> None:
        self._now = now

    def utcnow(self) -> datetime:
        return self._now


def _run(status: AutonomousRunStatus = AutonomousRunStatus.EXECUTING) -> AutonomousRun:
    return AutonomousRun(
        id=uuid4(),
        project_id=uuid4(),
        initiated_by=uuid4(),
        status=status,
        objective="phase4 recovery test",
        max_actions=20,
        max_runtime_seconds=1800,
        started_at=NOW,
        created_at=NOW,
    )


def _action(
    run: AutonomousRun,
    *,
    planned_action_id: UUID,
    scan_id: UUID,
    status: str = "executed",
    retry_count: int = 0,
) -> AutonomousRunAction:
    return AutonomousRunAction(
        id=uuid4(),
        run_id=run.id,
        project_id=run.project_id,
        cycle=1,
        action_type="recon",
        plugin="ping",
        title="probe",
        target_ids=[uuid4()],
        category=ActionCategory.CATEGORY_2,
        status=status,
        planned_action_id=planned_action_id,
        scan_id=scan_id,
        retry_count=retry_count,
        created_at=NOW,
    )


def _planned(
    run: AutonomousRun,
    status: PlannedActionStatus = PlannedActionStatus.EXECUTED,
) -> PlannedAction:
    return PlannedAction(
        id=uuid4(),
        project_id=run.project_id,
        action_type="recon",
        title="probe",
        description="",
        justification="",
        plugin="ping",
        target_ids=[uuid4()],
        status=status,
        created_by=run.initiated_by,
        objective=run.objective,
        risk_level="low",
    )


def _failed_scan(
    run: AutonomousRun,
    failure_kind: ScanFailureKind,
    *,
    status: ScanStatus = ScanStatus.FAILED,
) -> Scan:
    return Scan(
        id=uuid4(),
        project_id=run.project_id,
        initiated_by=run.initiated_by,
        plugin="ping",
        status=status,
        target_ids=[uuid4()],
        plugin_config={},
        created_at=NOW,
        completed_at=NOW if status is ScanStatus.FAILED else None,
        error_message="boom",
        failure_kind=failure_kind,
    )


def _recovery_rig(
    run: AutonomousRun | None = None,
    *,
    max_retries_per_action: int = 1,
) -> tuple[
    AutonomousRun,
    AutonomousService,
    AutonomousRecoveryService,
    FakePlannerService,
    FakeScanLauncher,
    FakeScanRepository,
    FakeAuditLogRepository,
    FakeAutonomousRunRepository,
    FakeAutonomousRunActionRepository,
    _FixedClock,
]:
    """Standard recovery rig: real service+recovery, every repo is a fake."""
    clk = _FixedClock()
    rr = FakeAutonomousRunRepository()
    ar = FakeAutonomousRunActionRepository()
    ar.set_run_repo(rr)
    svc = AutonomousService(run_repo=rr, action_repo=ar, clock=clk)
    planner = FakePlannerService()
    launcher = FakeScanLauncher()
    scans = FakeScanRepository()
    audit = FakeAuditLogRepository()
    run_obj = run or _run()
    rr._runs[run_obj.id] = run_obj
    recovery = AutonomousRecoveryService(
        autonomous_service=svc,
        planner=planner,
        launcher=launcher,
        scan_repository=scans,
        audit_repository=audit,
        max_retries_per_action=max_retries_per_action,
        clock=clk,
    )
    return (
        run_obj,
        svc,
        recovery,
        planner,
        launcher,
        scans,
        audit,
        rr,
        ar,
        clk,
    )


def _seed_transport_failure(
    svc: AutonomousService,
    arr: FakeAutonomousRunActionRepository,
    scans: FakeScanRepository,
    planner: FakePlannerService,
    run: AutonomousRun,
) -> tuple[AutonomousRunAction, PlannedAction, Scan]:
    """One executed action whose scan FAILED with failure_kind=transport."""
    pa = _planned(run)
    planner._store[pa.id] = pa
    scan = _failed_scan(run, ScanFailureKind.TRANSPORT)
    scans._scans[scan.id] = scan
    action = _action(run, planned_action_id=pa.id, scan_id=scan.id)
    arr._actions[action.id] = action
    run.actions_completed = 1
    return action, pa, scan


def _audit_names(audit: FakeAuditLogRepository) -> list[str]:
    return [e.action for e in audit._entries]


# ---------------------------------------------------------------------------
# A: Failure taxonomy — only TRANSPORT is retryable
# ---------------------------------------------------------------------------


def test_a_only_transport_is_retryable() -> None:
    assert ScanFailureKind.TRANSPORT in ScanFailureKind.retryable()
    assert ScanFailureKind.TOOL not in ScanFailureKind.retryable()
    assert ScanFailureKind.DOMAIN not in ScanFailureKind.retryable()
    assert ScanFailureKind("transport") is ScanFailureKind.TRANSPORT
    with pytest.raises(ValueError):
        ScanFailureKind("mystery")
    assert ScanFailureKind.retryable() == frozenset({ScanFailureKind.TRANSPORT})


# ---------------------------------------------------------------------------
# B: Reapprove bridge — EXECUTED → APPROVED exclusively
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_b_reapprove_only_from_executed() -> None:
    planner = FakePlannerService()
    run = _run()

    execut_done = _planned(run, status=PlannedActionStatus.EXECUTED)
    planner._store[execut_done.id] = execut_done
    returned = await planner.reapprove(execut_done.id, approved_by=run.initiated_by)
    assert returned.status is PlannedActionStatus.APPROVED
    assert returned.approved_by == run.initiated_by

    # A second reapprove on a now-APPROVED (or any non-EXECUTED) row must fail:
    with pytest.raises(PlannedActionNotApprovableError):
        await planner.reapprove(execut_done.id)
    fresh = _planned(run, status=PlannedActionStatus.PENDING_REVIEW)
    planner._store[fresh.id] = fresh
    with pytest.raises(PlannedActionNotApprovableError):
        await planner.reapprove(fresh.id)


# ---------------------------------------------------------------------------
# C: Failure kind persists through the repository boundary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_c_failure_kind_persisted_by_repo() -> None:
    scans = FakeScanRepository()
    scan = _failed_scan(_run(), ScanFailureKind.TOOL)
    scans._scans[scan.id] = scan
    await scans.fail(
        scan.id, "tool blew up", 3, ScanFailureKind.DOMAIN
    )
    assert scans._scans[scan.id].failure_kind is ScanFailureKind.DOMAIN


# ---------------------------------------------------------------------------
# D: record_action_execution is idempotent — one budget increment ever
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_d_record_execution_idempotent() -> None:
    clk = _FixedClock()
    rr = FakeAutonomousRunRepository()
    ar = FakeAutonomousRunActionRepository()
    ar.set_run_repo(rr)
    svc = AutonomousService(run_repo=rr, action_repo=ar, clock=clk)
    run = _run()
    rr._runs[run.id] = run
    action = _action(run, planned_action_id=uuid4(), scan_id=uuid4(), status="proposed")
    ar._actions[action.id] = action

    first = await svc.record_action_execution(action.id, uuid4())
    assert run.actions_completed == 1
    second_scan = uuid4()
    await svc.record_action_execution(action.id, second_scan)  # recovery adoption
    assert run.actions_completed == 1  # never a second increment
    refreshed = ar._actions[action.id]
    assert refreshed.scan_id == second_scan
    assert first.status == "executed"


# ---------------------------------------------------------------------------
# E: retry_action_execution — re-points scan, bumps retries, links lineage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e_retry_records_lineage_without_reincrement() -> None:
    clk = _FixedClock()
    rr = FakeAutonomousRunRepository()
    ar = FakeAutonomousRunActionRepository()
    ar.set_run_repo(rr)
    svc = AutonomousService(run_repo=rr, action_repo=ar, clock=clk)
    run = _run()
    rr._runs[run.id] = run
    original_scan = uuid4()
    action = _action(run, planned_action_id=uuid4(), scan_id=original_scan)
    ar._actions[action.id] = action
    run.actions_completed = 1

    retry_scan = uuid4()
    await svc.retry_action_execution(action.id, retry_scan, max_retries=1)

    refreshed = ar._actions[action.id]
    assert refreshed.scan_id == retry_scan
    assert refreshed.retry_count == 1
    assert refreshed.result_summary["scan_attempt_ids"] == [str(original_scan)]
    assert run.actions_completed == 1  # the retry must NOT re-add to the budget


# ---------------------------------------------------------------------------
# F–G: retry_action_execution guards — non-executed and exhausted budgets
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_f_retry_rejects_non_executed_action() -> None:
    clk = _FixedClock()
    rr = FakeAutonomousRunRepository()
    ar = FakeAutonomousRunActionRepository()
    svc = AutonomousService(run_repo=rr, action_repo=ar, clock=clk)
    run = _run()
    rr._runs[run.id] = run
    action = _action(run, planned_action_id=uuid4(), scan_id=uuid4(), status="proposed")
    ar._actions[action.id] = action

    with pytest.raises(AutonomousActionNotRetryableError):
        await svc.retry_action_execution(action.id, uuid4(), max_retries=1)


@pytest.mark.asyncio
async def test_g_retry_rejects_exhausted_budget() -> None:
    clk = _FixedClock()
    rr = FakeAutonomousRunRepository()
    ar = FakeAutonomousRunActionRepository()
    svc = AutonomousService(run_repo=rr, action_repo=ar, clock=clk)
    run = _run()
    rr._runs[run.id] = run
    action = _action(run, planned_action_id=uuid4(), scan_id=uuid4(), retry_count=1)
    ar._actions[action.id] = action

    with pytest.raises(AutonomousActionNotRetryableError):
        await svc.retry_action_execution(action.id, uuid4(), max_retries=1)


# ---------------------------------------------------------------------------
# H: reconcile retries a TRANSPORT-failed executed action exactly once
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_h_reconcile_retries_transport_once() -> None:
    run, svc, recovery, planner, launcher, scans, audit, rr, ar, _ = _recovery_rig()
    action, pa, failed_scan = _seed_transport_failure(svc, ar, scans, planner, run)

    outcome = await recovery.reconcile(run.id)

    assert outcome.retried == 1
    assert outcome.acted is True
    # The M7.2 planned action went EXECUTED -> (reapprove) APPROVED -> EXECUTED.
    assert planner._store[pa.id].status is PlannedActionStatus.EXECUTED
    assert pa.id in planner.approved
    assert len(launcher.calls) == 1
    refreshed = ar._actions[action.id]
    assert refreshed.retry_count == 1
    assert refreshed.scan_id != failed_scan.id
    assert refreshed.result_summary["scan_attempt_ids"] == [str(failed_scan.id)]
    # Never a second budget increment.
    assert run.actions_completed == 1
    assert "ai.autonomous.execution_retry" in _audit_names(audit)

    # A second reconcile sees retry_count == budget and settles to no-op.
    again = await recovery.reconcile(run.id)
    assert again.retried == 0
    assert len(launcher.calls) == 1


# ---------------------------------------------------------------------------
# I–J: reconcile must NOT retry TOOL or DOMAIN failures (tool may have run)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_i_reconcile_ignores_tool_failure() -> None:
    run, svc, recovery, planner, launcher, scans, audit, _, ar, _ = _recovery_rig()
    pa = _planned(run)
    planner._store[pa.id] = pa
    scan = _failed_scan(run, ScanFailureKind.TOOL)
    scans._scans[scan.id] = scan
    action = _action(run, planned_action_id=pa.id, scan_id=scan.id)
    ar._actions[action.id] = action

    outcome = await recovery.reconcile(run.id)

    assert outcome.retried == 0
    assert len(launcher.calls) == 0
    assert pa.id not in planner.approved
    assert "ai.autonomous.execution_retry" not in _audit_names(audit)


@pytest.mark.asyncio
async def test_j_reconcile_ignores_domain_failure() -> None:
    run, svc, recovery, planner, launcher, scans, audit, _, ar, _ = _recovery_rig()
    pa = _planned(run)
    planner._store[pa.id] = pa
    scan = _failed_scan(run, ScanFailureKind.DOMAIN)
    scans._scans[scan.id] = scan
    action = _action(run, planned_action_id=pa.id, scan_id=scan.id)
    ar._actions[action.id] = action

    outcome = await recovery.reconcile(run.id)

    assert outcome.retried == 0
    assert len(launcher.calls) == 0
    assert "ai.autonomous.execution_retry" not in _audit_names(audit)


# ---------------------------------------------------------------------------
# K: reconcile skips when the scan row cannot be reached
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_k_reconcile_skips_missing_scan_row() -> None:
    run, svc, recovery, planner, launcher, _, audit, _, ar, _ = _recovery_rig()
    pa = _planned(run)
    planner._store[pa.id] = pa
    action = _action(run, planned_action_id=pa.id, scan_id=uuid4())  # row absent
    ar._actions[action.id] = action

    outcome = await recovery.reconcile(run.id)

    assert outcome.retried == 0
    assert len(launcher.calls) == 0
    assert "ai.autonomous.execution_retry" not in _audit_names(audit)


# ---------------------------------------------------------------------------
# L: reconcile is a no-op for terminal runs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_l_reconcile_skips_terminal_run() -> None:
    run = _run(status=AutonomousRunStatus.COMPLETED)
    run, svc, recovery, planner, launcher, scans, audit, _, ar, _ = _recovery_rig(run)
    pa = _planned(run)
    planner._store[pa.id] = pa
    scan = _failed_scan(run, ScanFailureKind.TRANSPORT)
    scans._scans[scan.id] = scan
    action = _action(run, planned_action_id=pa.id, scan_id=scan.id)
    ar._actions[action.id] = action

    outcome = await recovery.reconcile(run.id)

    assert outcome.retried == 0
    assert len(launcher.calls) == 0
    assert "ai.autonomous.execution_retry" not in _audit_names(audit)


# ---------------------------------------------------------------------------
# M: reconcile honors the retry budget already spent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_m_reconcile_respects_spent_budget() -> None:
    run, svc, recovery, planner, launcher, scans, _, _, ar, _ = _recovery_rig()
    pa = _planned(run)
    planner._store[pa.id] = pa
    scan = _failed_scan(run, ScanFailureKind.TRANSPORT)
    scans._scans[scan.id] = scan
    action = _action(run, planned_action_id=pa.id, scan_id=scan.id, retry_count=1)
    ar._actions[action.id] = action

    outcome = await recovery.reconcile(run.id)

    assert outcome.retried == 0
    assert len(launcher.calls) == 0


# ---------------------------------------------------------------------------
# N–P: orchestrator cycle — recovery reconciliation + durable cycle lock
# ---------------------------------------------------------------------------


def _orchestrator_rig(
    recovery: AutonomousRecoveryService,
    rr: FakeAutonomousRunRepository,
    run: AutonomousRun,
    *,
    cycle_lock_available: bool = True,
) -> tuple[AutonomousOrchestrator, _FixedClock]:
    clk = _FixedClock()
    rr.cycle_lock_available = cycle_lock_available
    orch = AutonomousOrchestrator(
        autonomous_service=recovery._svc,
        planner=recovery._planner,
        launcher=recovery._launcher,
        run_repository=rr,
        classification=ActionClassificationPolicy(
            auto_eligible_plugins=frozenset({"ping"})
        ),
        audit_repository=recovery._audit,
        recovery=recovery,
        cycle_max_actions=3,
        session_timeout_seconds=15.0,
        clock=clk,
    )
    return orch, clk


@pytest.mark.asyncio
async def test_n_cycle_reconciles_before_planning() -> None:
    run, svc, recovery, planner, launcher, scans, audit, rr, ar, _ = _recovery_rig(
        run=_run(status=AutonomousRunStatus.EXECUTING)
    )
    _seed_transport_failure(svc, ar, scans, planner, run)
    orch, _ = _orchestrator_rig(recovery, rr, run)

    await orch.cycle(run.id)

    # Recovery ran before anything else: exactly one retry dispatch and none
    # of the run's own planning/execution added a second scan.
    refreshed = ar._actions[
        [a for a in ar._actions.values() if a.run_id == run.id][0].id
    ]
    assert refreshed.retry_count == 1
    assert len(launcher.calls) == 1
    assert rr.cycle_lock_attempts == 1
    assert "ai.autonomous.execution_retry" in _audit_names(audit)


@pytest.mark.asyncio
async def test_o_cycle_fails_closed_when_lock_held() -> None:
    run, svc, recovery, planner, launcher, scans, audit, rr, ar, _ = _recovery_rig(
        run=_run(status=AutonomousRunStatus.EXECUTING)
    )
    _seed_transport_failure(svc, ar, scans, planner, run)
    orch, _ = _orchestrator_rig(recovery, rr, run, cycle_lock_available=False)

    with pytest.raises(AutonomousCycleNotAllowedError) as excinfo:
        await orch.cycle(run.id)
    assert excinfo.value.current_status == "concurrent_cycle"

    # Fail-closed: nothing was dispatched while a peer held the lock.
    assert len(launcher.calls) == 0
    assert "ai.autonomous.concurrent_cycle_blocked" in _audit_names(audit)


@pytest.mark.asyncio
async def test_p_cycle_attempts_lock_on_success() -> None:
    run, svc, recovery, planner, launcher, scans, audit, rr, ar, _ = _recovery_rig(
        run=_run(status=AutonomousRunStatus.EXECUTING)
    )
    _seed_transport_failure(svc, ar, scans, planner, run)
    orch, _ = _orchestrator_rig(recovery, rr, run)

    await orch.cycle(run.id)

    assert rr.cycle_lock_attempts == 1


# ---------------------------------------------------------------------------
# Q–S: cooperative cancellation of a run's linked scans
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_q_cancel_soft_cancels_linked_scans() -> None:
    clk = _FixedClock()
    rr = FakeAutonomousRunRepository()
    ar = FakeAutonomousRunActionRepository()
    ar.set_run_repo(rr)
    scans = FakeScanRepository()
    run = _run()
    rr._runs[run.id] = run
    scan = Scan(
        id=uuid4(),
        project_id=run.project_id,
        initiated_by=run.initiated_by,
        plugin="ping",
        status=ScanStatus.QUEUED,
        target_ids=[uuid4()],
        plugin_config={},
        created_at=NOW,
    )
    scans._scans[scan.id] = scan
    called: list[UUID] = []

    async def canceller(scan_id: UUID) -> Scan:
        called.append(scan_id)
        s = scans._scans[scan_id]
        s.status = ScanStatus.CANCELLED
        return s

    svc = AutonomousService(run_repo=rr, action_repo=ar, clock=clk, scan_canceller=canceller)
    action = _action(run, planned_action_id=uuid4(), scan_id=scan.id)
    ar._actions[action.id] = action

    result = await svc.cancel(run.id)

    assert result.status is AutonomousRunStatus.CANCELLED
    assert called == [scan.id]
    assert scans._scans[scan.id].status is ScanStatus.CANCELLED


@pytest.mark.asyncio
async def test_r_cancel_without_canceller_still_flips_run() -> None:
    clk = _FixedClock()
    rr = FakeAutonomousRunRepository()
    ar = FakeAutonomousRunActionRepository()
    svc = AutonomousService(run_repo=rr, action_repo=ar, clock=clk)
    run = _run()
    rr._runs[run.id] = run
    action = _action(run, planned_action_id=uuid4(), scan_id=uuid4())
    ar._actions[action.id] = action

    result = await svc.cancel(run.id)

    assert result.status is AutonomousRunStatus.CANCELLED


@pytest.mark.asyncio
async def test_s_cancel_terminal_run_rejected() -> None:
    clk = _FixedClock()
    rr = FakeAutonomousRunRepository()
    ar = FakeAutonomousRunActionRepository()
    svc = AutonomousService(run_repo=rr, action_repo=ar, clock=clk)
    run = _run(status=AutonomousRunStatus.FAILED)
    rr._runs[run.id] = run

    with pytest.raises(AutonomousRunNotCancellableError):
        await svc.cancel(run.id)


# ---------------------------------------------------------------------------
# T–W: recovery supervisor — recover_stale advance / fail-closed / live
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t_recover_stale_advances_when_all_executed_scans_terminal() -> None:
    run, svc, recovery, _, _, scans, audit, _, ar, _ = _recovery_rig(
        run=_run(status=AutonomousRunStatus.EXECUTING)
    )
    for _ in range(2):
        scan = _failed_scan(run, ScanFailureKind.TOOL)
        scans._scans[scan.id] = scan
        executed = _action(run, planned_action_id=uuid4(), scan_id=scan.id)
        ar._actions[executed.id] = executed

    outcome = await recovery.recover_stale(run.id)

    assert outcome.advanced_to_observing is True
    assert outcome.acted is True
    assert run.status is AutonomousRunStatus.OBSERVING
    assert "ai.autonomous.recovered" in _audit_names(audit)


@pytest.mark.asyncio
async def test_u_recover_stale_fails_closed_on_missing_scan() -> None:
    run, svc, recovery, _, launcher, _, audit, _, ar, _ = _recovery_rig(
        run=_run(status=AutonomousRunStatus.EXECUTING)
    )
    executed = _action(run, planned_action_id=uuid4(), scan_id=uuid4())
    ar._actions[executed.id] = executed

    outcome = await recovery.recover_stale(run.id)

    assert outcome.failed_stalled is True
    assert run.status is AutonomousRunStatus.FAILED
    assert run.error_message is not None
    assert "ai.autonomous.stalled" in _audit_names(audit)
    assert len(launcher.calls) == 0


@pytest.mark.asyncio
async def test_v_recover_stale_leaves_live_scans_alone() -> None:
    run, svc, recovery, _, launcher, scans, audit, _, ar, _ = _recovery_rig(
        run=_run(status=AutonomousRunStatus.EXECUTING)
    )
    live = _failed_scan(run, ScanFailureKind.TOOL, status=ScanStatus.RUNNING)
    scans._scans[live.id] = live
    executed = _action(run, planned_action_id=uuid4(), scan_id=live.id)
    ar._actions[executed.id] = executed

    outcome = await recovery.recover_stale(run.id)

    assert outcome.acted is False
    assert run.status is AutonomousRunStatus.EXECUTING  # still progressing
    assert len(launcher.calls) == 0
    assert _audit_names(audit) == []


@pytest.mark.asyncio
async def test_w_recover_stale_noop_on_terminal_run() -> None:
    run, svc, recovery, _, launcher, _, audit, _, _, _ = _recovery_rig(
        run=_run(status=AutonomousRunStatus.CANCELLED)
    )

    outcome = await recovery.recover_stale(run.id)

    assert outcome.acted is False
    assert len(launcher.calls) == 0
    assert _audit_names(audit) == []


# ---------------------------------------------------------------------------
# X: a retry dispatch must produce a brand-new scan (never reuse the old one)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_x_retry_scan_is_distinct() -> None:
    run, svc, recovery, planner, launcher, scans, _, _, ar, _ = _recovery_rig()
    action, pa, failed_scan = _seed_transport_failure(svc, ar, scans, planner, run)

    await recovery.reconcile(run.id)

    retry_scan_id = ar._actions[action.id].scan_id
    assert retry_scan_id is not None
    assert retry_scan_id != failed_scan.id
    assert retry_scan_id == launcher.scans[retry_scan_id].id
    # The M7.2 planned action carries the retry's scan link too.
    assert planner._store[pa.id].scan_id == retry_scan_id


# ---------------------------------------------------------------------------
# Y: recovery never fabricates new action rows or a second budget increment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_y_recovery_adds_no_new_action_rows() -> None:
    run, svc, recovery, planner, launcher, scans, _, _, ar, _ = _recovery_rig()
    _seed_transport_failure(svc, ar, scans, planner, run)

    await recovery.reconcile(run.id)

    assert len([a for a in ar._actions.values() if a.run_id == run.id]) == 1
    assert run.actions_completed == 1


# ---------------------------------------------------------------------------
# Z: end-to-end smoke — a full cycle, a transport failure, a reconciled retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_z_end_to_end_transport_failure_then_recovery() -> None:
    run = _run(status=AutonomousRunStatus.CREATED)
    run, svc, recovery, planner, launcher, scans, audit, rr, ar, clk = _recovery_rig(run)
    orch, _ = _orchestrator_rig(recovery, rr, run)
    planner.proposal_specs = [
        {
            "action_type": "recon",
            "title": "probe",
            "plugin": "ping",
            "risk_level": "low",
            "accepted": True,
        }
    ]

    first = await orch.cycle(run.id)
    assert first.stopped_because == "executed"
    assert len(launcher.scans) == 1

    # The worker's transport failure: the ORIGINAL scan lands with
    # failure_kind=transport (plugin never ran). The scan row must be visible
    # to the recovery service (the real pipeline persists via SQLAlchemy).
    dispatched = next(iter(launcher.scans.values()))
    scans._scans[dispatched.id] = dispatched
    await scans.fail(dispatched.id, "executor unreachable", 0, ScanFailureKind.TRANSPORT)
    acted_run = await svc.get(run.id)
    action = (await svc.list_actions(acted_run.id))[0]
    assert action.status == "executed"

    # Next cycle reconciles under the lock: one retry, distinct scan, budget
    # consumption unchanged.
    second = await orch.cycle(run.id)
    assert len(launcher.scans) == 2
    refreshed = (await svc.list_actions(run.id))[0]
    assert refreshed.retry_count == 1
    assert refreshed.scan_id != dispatched.id
    assert run.actions_completed == 1
    assert "ai.autonomous.execution_retry" in _audit_names(audit)
    assert second.run.status is AutonomousRunStatus.COMPLETED  # no new facts