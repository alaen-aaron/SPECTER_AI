"""
M7.4 Phase 2 — Controlled Planner → Validator → Execution Loop Tests (A–Y).

Covers the `AutonomousOrchestrator`: a single bounded cycle per call,
budget enforcement, cooperative cancellation, category-0/1/2 decision
paths, AUTO_POLICY vs MANUAL approval provenance, planned-action
linkage, execution-time re-validation, and the run/action state machines.
All tests run against fakes (no Postgres required).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.application.action_classifier import ActionClassificationPolicy
from app.application.autonomous_orchestrator import AutonomousOrchestrator
from app.application.autonomous_service import AutonomousService
from app.domain.entities import AutonomousRun
from app.domain.exceptions import AutonomousCycleNotAllowedError
from app.domain.value_objects import AutonomousRunStatus
from tests.fakes import (
    FakeAuditLogRepository,
    FakeAutonomousRunActionRepository,
    FakeAutonomousRunRepository,
    FakePlannerService,
    FakeScanLauncher,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NOW = datetime(2026, 8, 27, 10, 0, 0, tzinfo=UTC)


class _FixedClock:
    def __init__(self, now: datetime = NOW) -> None:
        self._now = now

    def utcnow(self) -> datetime:
        return self._now

    def advance(self, seconds: int) -> None:
        self._now += timedelta(seconds=seconds)


def _make_run(
    *,
    project_id: UUID | None = None,
    initiated_by: UUID | None = None,
    status: AutonomousRunStatus = AutonomousRunStatus.CREATED,
    started_at: datetime | None = None,
    max_actions: int = 20,
    max_runtime_seconds: int = 1800,
) -> AutonomousRun:
    return AutonomousRun(
        id=uuid4(),
        project_id=project_id or uuid4(),
        initiated_by=initiated_by or uuid4(),
        status=status,
        objective="test run",
        max_actions=max_actions,
        max_runtime_seconds=max_runtime_seconds,
        started_at=started_at,
        created_at=NOW,
    )


def _make_rig(
    run: AutonomousRun | None = None,
    clock: _FixedClock | None = None,
) -> tuple[
    AutonomousRun,
    AutonomousService,
    AutonomousOrchestrator,
    FakePlannerService,
    FakeScanLauncher,
    FakeAuditLogRepository,
    _FixedClock,
]:
    """Standard wiring: real service + fake planner/launcher/audit + default policy."""
    clk = clock or _FixedClock()
    rr = FakeAutonomousRunRepository()
    ar = FakeAutonomousRunActionRepository()
    ar.set_run_repo(rr)
    svc = AutonomousService(run_repo=rr, action_repo=ar, clock=clk)
    planner = FakePlannerService()
    launcher = FakeScanLauncher()
    audit = FakeAuditLogRepository()
    run_obj = run or _make_run()
    rr._runs[run_obj.id] = run_obj  # fake persistence mirrors repo.create()
    orch = AutonomousOrchestrator(
        autonomous_service=svc,
        planner=planner,
        launcher=launcher,
        run_repository=rr,
        audit_repository=audit,
        classification=ActionClassificationPolicy(
            auto_eligible_plugins=frozenset({"ping"})
        ),
        cycle_max_actions=3,
        session_timeout_seconds=15.0,
        clock=clk,
    )
    return run_obj, svc, orch, planner, launcher, audit, clk


def _ping_spec(
    *,
    plugin: str = "ping",
    risk: str = "low",
    accepted: bool = True,
) -> dict[str, object]:
    return {
        "action_type": "recon",
        "plugin": plugin,
        "title": "Liveness check",
        "description": "verify reachability",
        "justification": "cheap signal",
        "target_ids": [uuid4()],
        "plugin_config": {"hostname": "10.0.0.1"},
        "risk_level": risk,
        "accepted": accepted,
    }


# ---------------------------------------------------------------------------
# A: CREATED -> first cycle advances through PLANNING/EXECUTING to OBSERVING
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_first_cycle_advances_from_created() -> None:
    run, _, orch, planner, launcher, _, _ = _make_rig()
    planner.proposal_specs = [_ping_spec()]

    outcome = await orch.cycle(run.id)

    assert outcome.run.status is AutonomousRunStatus.OBSERVING
    assert outcome.stopped_because == "executed"
    assert outcome.run.actions_completed == 1
    assert len(outcome.executed_scan_ids) == 1
    assert len(launcher.calls) == 1
    assert planner.plan_calls and planner.plan_calls[0]["created_by"] == run.initiated_by


# ---------------------------------------------------------------------------
# B: Validator-rejected proposal -> blocked, no execution, run completes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_b_rejected_proposal_is_blocked() -> None:
    run, _, orch, planner, launcher, _, _ = _make_rig()
    planner.proposal_specs = [_ping_spec(accepted=False)]

    outcome = await orch.cycle(run.id)

    assert outcome.run.status is AutonomousRunStatus.COMPLETED
    assert outcome.stopped_because == "completed_no_actions"
    assert len(launcher.calls) == 0
    actions = await orch._svc.list_actions(run.id)
    assert len(actions) == 1
    assert actions[0].status == "blocked"


# ---------------------------------------------------------------------------
# C: Category-0 actions are recorded but never approved or executed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_c_category0_never_approved_or_executed() -> None:
    run, _, orch, planner, launcher, _, _ = _make_rig()
    # Even an allow-listed plugin is category 0 when validation rejects it.
    planner.proposal_specs = [_ping_spec(accepted=False)]

    outcome = await orch.cycle(run.id)

    actions = await orch._svc.list_actions(run.id)
    assert actions[0].category.value == "category_0"
    assert actions[0].approval_mode is None
    assert actions[0].scan_id is None
    assert len(launcher.calls) == 0
    assert outcome.run.status is AutonomousRunStatus.COMPLETED


# ---------------------------------------------------------------------------
# D: Category-1 (validated, not allow-listed) pauses at AWAITING_APPROVAL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_d_category1_pauses_for_human() -> None:
    run, _, orch, planner, launcher, _, _ = _make_rig()
    planner.proposal_specs = [_ping_spec(plugin="nmap", risk="medium")]

    outcome = await orch.cycle(run.id)

    assert outcome.run.status is AutonomousRunStatus.AWAITING_APPROVAL
    assert outcome.stopped_because == "awaiting_approval"
    assert len(launcher.calls) == 0
    actions = await orch._svc.list_actions(run.id)
    assert actions[0].status == "proposed"
    assert actions[0].category.value == "category_1"


# ---------------------------------------------------------------------------
# E: Category-2 auto-approval + execution with AUTO_POLICY provenance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e_category2_auto_approves_and_executes() -> None:
    run, _, orch, planner, launcher, audit, _ = _make_rig()
    planner.proposal_specs = [_ping_spec()]

    outcome = await orch.cycle(run.id)

    actions = await orch._svc.list_actions(run.id)
    action = actions[0]
    assert outcome.run.actions_completed == 1
    assert action.status == "executed"
    assert action.approval_mode == "auto_policy"
    assert action.approved_by == run.initiated_by  # attributed, never fabricated
    assert action.planned_action_id is not None
    assert action.scan_id == outcome.executed_scan_ids[0]
    # The underlying M7.2 planned action was approved (attributed) and executed.
    planned = await planner.get(action.planned_action_id)
    assert planned.approved_by == run.initiated_by
    audit_actions = {e.action for e in audit._entries}
    assert "ai.autonomous.executed" in audit_actions
    assert "ai.autonomous.blocked" not in audit_actions


# ---------------------------------------------------------------------------
# F: Planner exhausted (no proposals) -> run completes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_f_planner_exhausted_completes() -> None:
    run, _, orch, _, launcher, _, _ = _make_rig()

    outcome = await orch.cycle(run.id)

    assert outcome.run.status is AutonomousRunStatus.COMPLETED
    assert outcome.stopped_because == "completed_no_actions"
    assert len(launcher.calls) == 0


# ---------------------------------------------------------------------------
# G: max_actions budget stops the loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_g_budget_max_actions_exhausted() -> None:
    run, svc, orch, planner, launcher, _, _ = _make_rig(run=_make_run(max_actions=1))
    run_obj = await svc.get(run.id)
    run_obj.actions_completed = 1
    await orch._run_repo.update(run_obj)
    await svc.start_planning(run.id)
    planner.proposal_specs = [_ping_spec()]

    outcome = await orch.cycle(run.id)

    assert outcome.run.status is AutonomousRunStatus.COMPLETED
    assert outcome.stopped_because == "budget_exhausted"
    assert len(planner.plan_calls) == 0
    assert len(launcher.calls) == 0


# ---------------------------------------------------------------------------
# H: max_runtime_seconds budget stops the loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_h_budget_max_runtime_exhausted() -> None:
    clk = _FixedClock()
    run, svc, orch, planner, launcher, _, _ = _make_rig(
        run=_make_run(
            max_runtime_seconds=10,
            started_at=clk.utcnow() - timedelta(seconds=20),
        ),
        clock=clk,
    )
    await svc.start_planning(run.id)
    planner.proposal_specs = [_ping_spec()]

    outcome = await orch.cycle(run.id)

    assert outcome.run.status is AutonomousRunStatus.COMPLETED
    assert outcome.stopped_because == "budget_exhausted"
    assert len(launcher.calls) == 0


# ---------------------------------------------------------------------------
# I: OBSERVING with budget -> re-plan (OBSERVING -> PLANNING -> burst)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_i_observing_replans_with_budget() -> None:
    run, svc, orch, planner, launcher, _, _ = _make_rig(
        run=_make_run(status=AutonomousRunStatus.OBSERVING, started_at=NOW)
    )
    run_obj = await svc.get(run.id)
    run_obj.current_cycle = 3
    await orch._run_repo.update(run_obj)
    planner.proposal_specs = [_ping_spec()]

    outcome = await orch.cycle(run.id)

    assert outcome.run.current_cycle == 4
    assert outcome.run.actions_completed == 1
    assert len(launcher.calls) == 1


# ---------------------------------------------------------------------------
# J: Exact repetition of the run's last executed action is deduplicated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_j_duplicate_last_action_skipped() -> None:
    run, _, orch, planner, launcher, _, _ = _make_rig()
    spec = _ping_spec()  # identical targets/config -> same fingerprint
    planner.proposal_specs = [spec, spec]

    outcome = await orch.cycle(run.id)

    actions = await orch._svc.list_actions(run.id)
    assert outcome.run.actions_completed == 1
    assert len(launcher.calls) == 1
    statuses = [a.status for a in actions]
    assert statuses.count("executed") == 1
    assert "duplicate" in statuses


# ---------------------------------------------------------------------------
# K: Once-only execution across cycles
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_k_once_only_across_cycles() -> None:
    run, _, orch, planner, launcher, _, _ = _make_rig()
    planner.proposal_specs = [_ping_spec()]

    first = await orch.cycle(run.id)
    assert first.run.status is AutonomousRunStatus.OBSERVING
    assert len(launcher.calls) == 1

    second = await orch.cycle(run.id)  # re-plan re-proposes the same ping

    assert second.run.status is AutonomousRunStatus.COMPLETED
    assert len(launcher.calls) == 1  # never executed twice


# ---------------------------------------------------------------------------
# L: Action <-> scan linkage survives the cycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_l_scan_id_linkage() -> None:
    run, _, orch, planner, launcher, _, _ = _make_rig()
    planner.proposal_specs = [_ping_spec()]

    outcome = await orch.cycle(run.id)

    scan_id = outcome.executed_scan_ids[0]
    assert scan_id in launcher.scans
    scan = launcher.scans[scan_id]
    assert scan.plugin == "ping"
    actions = await orch._svc.list_actions(run.id)
    assert actions[0].scan_id == scan_id
    assert actions[0].run_id == run.id


# ---------------------------------------------------------------------------
# M: Action lifecycle statuses across mixed proposals
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_m_action_lifecycle_statuses() -> None:
    run, _, orch, planner, launcher, _, _ = _make_rig()
    planner.proposal_specs = [
        _ping_spec(accepted=False),
        _ping_spec(plugin="nmap", risk="medium"),
        _ping_spec(),
    ]

    outcome = await orch.cycle(run.id)

    actions = await orch._svc.list_actions(run.id)
    statuses = {a.status for a in actions}
    assert "blocked" in statuses  # rejected ping
    assert "proposed" in statuses  # nmap awaits human -> breaks the burst
    assert len(launcher.calls) == 0
    assert outcome.run.status is AutonomousRunStatus.AWAITING_APPROVAL


# ---------------------------------------------------------------------------
# N: Run lifecycle full sequence (auto path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_n_run_lifecycle_sequence() -> None:
    run, svc, orch, planner, launcher, _, _ = _make_rig()
    planner.proposal_specs = [_ping_spec()]

    assert run.status is AutonomousRunStatus.CREATED
    await orch.cycle(run.id)
    assert (await svc.get(run.id)).status is AutonomousRunStatus.OBSERVING
    assert len(launcher.calls) == 1


# ---------------------------------------------------------------------------
# O: Manual approval path -> MANUAL provenance + execution via execute_approved
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_o_manual_approval_then_execution() -> None:
    run, svc, orch, planner, launcher, _, _ = _make_rig()
    planner.proposal_specs = [_ping_spec(plugin="nmap", risk="medium")]

    first = await orch.cycle(run.id)
    assert first.run.status is AutonomousRunStatus.AWAITING_APPROVAL
    assert len(launcher.calls) == 0

    human = uuid4()
    pending = await svc.list_actions(run.id, status="proposed")
    action = pending[0]
    await svc.approve_action(action.id, approved_by=human)
    await planner.approve(action.planned_action_id, approved_by=human)
    await svc.approval_granted(run.id)

    second = await orch.cycle(run.id)

    executed = [a for a in await svc.list_actions(run.id) if a.status == "executed"]
    assert len(executed) == 1
    assert executed[0].approval_mode == "manual"
    assert executed[0].approved_by == human
    assert executed[0].scan_id is not None
    assert len(launcher.calls) == 1
    assert second.run.status is AutonomousRunStatus.OBSERVING


# ---------------------------------------------------------------------------
# P: Attribution is accurate for auto-approval (initiator, AUTO_POLICY)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_p_auto_attribution_is_accurate() -> None:
    run, svc, orch, planner, _, _, _ = _make_rig()
    planner.proposal_specs = [_ping_spec()]

    await orch.cycle(run.id)

    auto = [a for a in await svc.list_actions(run.id) if a.status == "executed"][0]
    assert auto.approval_mode == "auto_policy"
    assert auto.approved_by == run.initiated_by


# ---------------------------------------------------------------------------
# Q: Cycle on a terminal run raises
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_q_cycle_terminal_run_raises() -> None:
    run, _, orch, _, _, _, _ = _make_rig(
        run=_make_run(status=AutonomousRunStatus.COMPLETED)
    )
    with pytest.raises(AutonomousCycleNotAllowedError):
        await orch.cycle(run.id)


# ---------------------------------------------------------------------------
# R: Execution-time re-validation rejection is surfaced (no scan dispatched)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_r_execution_time_revalidation_surfaces() -> None:
    run, svc, orch, planner, launcher, _, _ = _make_rig()
    planner.proposal_specs = [_ping_spec()]
    planner.raise_validation_rejection = True

    outcome = await orch.cycle(run.id)

    failed = [a for a in await svc.list_actions(run.id) if a.status == "failed"]
    assert len(failed) == 1
    assert len(launcher.calls) == 0
    assert outcome.run.status is AutonomousRunStatus.OBSERVING


# ---------------------------------------------------------------------------
# S: AWAITING_APPROVAL cycle is a no-op
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_s_awaiting_approval_noop() -> None:
    run, _, orch, _, launcher, _, _ = _make_rig(
        run=_make_run(status=AutonomousRunStatus.AWAITING_APPROVAL)
    )
    outcome = await orch.cycle(run.id)
    assert outcome.run.status is AutonomousRunStatus.AWAITING_APPROVAL
    assert outcome.stopped_because == "awaiting_approval"
    assert len(launcher.calls) == 0


# ---------------------------------------------------------------------------
# U: Cycle caps the number of actions per burst (cycle_max_actions)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_u_cycle_burst_limited() -> None:
    run, _, orch, planner, launcher, _, _ = _make_rig()
    planner.proposal_specs = [_ping_spec() for _ in range(6)]

    outcome = await orch.cycle(run.id)

    # Default orchestration was built with cycle_max_actions=3.
    assert outcome.run.actions_completed == 3
    assert len(launcher.calls) == 3
    assert planner.plan_calls[0]["max_actions"] == 3


# ---------------------------------------------------------------------------
# W: Pending actions without a planned-action link are skipped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_w_pending_without_linkage_skipped() -> None:
    run, svc, orch, _, launcher, _, _ = _make_rig(
        run=_make_run(status=AutonomousRunStatus.EXECUTING)
    )
    await svc.propose_action(
        run_id=run.id,
        cycle=1,
        action_type="recon",
        plugin="ping",
        title="orphan",
        target_ids=[uuid4()],
    )
    pending = await svc.list_actions(run.id)
    await svc.approve_action(pending[0].id, approved_by=uuid4())

    outcome = await orch.cycle(run.id)

    assert len(launcher.calls) == 0
    assert outcome.run.status is AutonomousRunStatus.EXECUTING


# ---------------------------------------------------------------------------
# Y: result_summary accumulates the run-wide action profile
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_y_result_summary_profile() -> None:
    run, svc, orch, planner, _, _, _ = _make_rig()
    planner.proposal_specs = [
        _ping_spec(accepted=False),
        _ping_spec(plugin="nmap", risk="medium"),
        _ping_spec(),
    ]
    await orch.cycle(run.id)

    refreshed = await svc.get(run.id)
    # Rejected probe + category-1 probe: the burst breaks at the human gate
    # before the third (auto-eligible) proposal is reached.
    assert refreshed.result_summary["total_planned"] == 2
    assert refreshed.result_summary["total_blocked"] == 1
    assert refreshed.result_summary["total_waiting_for_human"] == 1
    assert refreshed.result_summary["total_executed"] == 0