"""Autonomous orchestrator — the CONTROLLED planning/approval/execution loop (M7.4 Phase 2).

Everything here is bounded and audited:

1. One bounded cycle per API call. `cycle()` advances the run at most
   one planning burst (cap `cycle_max_actions`) plus its state-machine
   consequence. It can never loop on its own — re-planning requires
   another operator-driven cycle call, and total work is capped by the
   run's `max_actions` / `max_runtime_seconds` budgets.
2. Planner output is UNTRUSTED. Every proposal is passed through the
   existing deterministic `ActionProposalValidator` (inside
   `PlannerService.plan`), then re-validated a second time by
   `execute_approved` at execution time, then Scope Guard + plugin
   policy again inside `ScanService.create`.
3. No alternate execution path exists. Auto-execution uses the exact
   same bridge a human would: `execute_approved() -> ScanService.create()
   -> Scope Guard -> Celery -> M7.1 isolated executor`.
4. Approval provenance is explicit. Category-2 actions are approved by
   policy — recorded as `approval_mode = AUTO_POLICY` and attributed to
   the run's initiator. A human's click is recorded as
   `approval_mode = MANUAL`. The audit trail never fabricates an
   approval of either kind.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from app.application.action_classifier import ActionClassificationPolicy
from app.application.autonomous_service import AutonomousService
from app.application.planner_service import PlannerService, ScanLauncher
from app.domain.entities import AuditLogEntry, AutonomousRun, PlannedAction
from app.domain.exceptions import (
    ActionNotExecutableError,
    ActionRejectedByValidatorError,
    AutonomousCycleNotAllowedError,
    PlannedActionNotFoundError,
)
from app.domain.repositories import AuditLogRepository, AutonomousRunRepository
from app.domain.value_objects import ActionCategory, AutonomousRunStatus


class _Clock(Protocol):
    def utcnow(self) -> datetime: ...


class _SystemClock:
    def utcnow(self) -> datetime:
        return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class CycleOutcome:
    """Returned by `cycle()`: the advanced run and a per-cycle decision profile."""

    run: AutonomousRun
    summary: dict[str, object]
    executed_scan_ids: tuple[UUID, ...]
    stopped_because: str


class AutonomousOrchestrator:
    """Bounded, operator-driven planner → validator → execution loop."""

    def __init__(
        self,
        autonomous_service: AutonomousService,
        planner: PlannerService,
        launcher: ScanLauncher,
        *,
        run_repository: AutonomousRunRepository,
        classification: ActionClassificationPolicy | None = None,
        audit_repository: AuditLogRepository | None = None,
        cycle_max_actions: int = 3,
        session_timeout_seconds: float = 15.0,
        clock: _Clock | None = None,
    ) -> None:
        self._svc = autonomous_service
        self._planner = planner
        self._launcher = launcher
        self._run_repo = run_repository
        self._classification = classification or ActionClassificationPolicy()
        self._audit = audit_repository
        self._cycle_max_actions = max(1, cycle_max_actions)
        self._session_timeout_seconds = session_timeout_seconds
        self._clock = clock or _SystemClock()

    async def cycle(self, run_id: UUID) -> CycleOutcome:
        """Advance the run exactly one bounded step (see module docstring)."""
        run = await self._svc.get(run_id)
        if run.is_terminal:
            raise AutonomousCycleNotAllowedError(run_id, run.status.value)

        if run.status is AutonomousRunStatus.AWAITING_APPROVAL:
            # Nothing to do until a human decides on the proposed actions.
            return self._outcome(
                run, stopped="awaiting_approval", extra={"waiting_for_human": 1}
            )

        if run.status is AutonomousRunStatus.CREATED:
            run = await self._svc.start_planning(run_id)
            run = await self._svc.get(run_id)

        if run.status is AutonomousRunStatus.OBSERVING:
            # Advance the loop: re-plan while the budget still allows it.
            if self._budget_ok(run):
                run = await self._svc.observation_complete(run_id, should_continue=True)
                run = await self._svc.get(run_id)
            else:
                run = await self._svc.observation_complete(run_id, should_continue=False)
                return self._outcome(run, stopped="budget_exhausted")

        if run.status is AutonomousRunStatus.EXECUTING:
            # Human-approved (MANUAL) pending actions are executed here.
            return await self._execute_pending(run)

        if run.status is AutonomousRunStatus.PLANNING:
            return await self._plan_and_decide(run)

        raise AutonomousCycleNotAllowedError(run_id, run.status.value)

    # ── Planning burst ─────────────────────────────────────────────────────

    async def _plan_and_decide(self, run: AutonomousRun) -> CycleOutcome:
        summary: dict[str, int] = {
            "planned": 0,
            "blocked": 0,
            "auto_approved": 0,
            "executed": 0,
            "waiting_for_human": 0,
            "duplicates": 0,
        }
        executed_scan_ids: list[UUID] = []

        if run.actions_completed >= run.max_actions:
            run = await self._svc.complete(run.id)
            return self._finished(run, summary, "budget_exhausted")
        if not self._budget_ok(run):
            run = await self._svc.complete(run.id)
            return self._finished(run, summary, "budget_exhausted")

        remaining = run.max_actions - run.actions_completed
        plan_outcome = await self._planner.plan(
            project_id=run.project_id,
            created_by=run.initiated_by,
            objective=run.objective,
            max_actions=min(self._cycle_max_actions, remaining),
            session_timeout_seconds=self._session_timeout_seconds,
            cancelled_check=self._cancelled_check(run),
        )

        if plan_outcome.stopped_because == "cancelled":
            return self._outcome(run, stopped="cancelled", extra=summary)

        summary["planned"] = len(plan_outcome.proposals)
        cycle = run.current_cycle + 1

        for proposal in plan_outcome.proposals:
            action: PlannedAction = proposal.action
            category = self._classification.classify(
                accepted=proposal.validation.accepted,
                plugin=action.plugin,
                risk_level=action.risk_level,
            )

            record = await self._svc.propose_action(
                run_id=run.id,
                cycle=cycle,
                action_type=action.action_type,
                plugin=action.plugin,
                title=action.title,
                description=action.description,
                justification=action.justification,
                plugin_config=dict(action.plugin_config),
                target_ids=list(action.target_ids),
                category=category,
            )
            if not proposal.persisted or category is ActionCategory.CATEGORY_0:
                # The validator rejected the proposal: PlannerService.plan()
                # never persisted a PlannedAction row for it, so there is
                # nothing the M7.2 link may point at (and nothing may ever
                # execute it). It is recorded as a blocked autonomous action.
                await self._svc.mark_action_blocked(
                    record.id,
                    reason="; ".join(proposal.validation.failed_reasons)[:2000],
                )
                summary["blocked"] += 1
                await self._audit_decision(
                    run,
                    record.id,
                    "ai.autonomous.blocked",
                    {"category": category.value,
                     "reasons": proposal.validation.failed_reasons[:10]},
                )
                continue

            await self._svc.attach_planned_action(record.id, action.id)

            if await self._is_immediate_repeat(run.id, action):
                await self._svc.mark_action_duplicate(record.id)
                summary["duplicates"] += 1
                await self._audit_decision(
                    run,
                    record.id,
                    "ai.autonomous.duplicate",
                    {"duplicate_of": str(action.id)},
                )
                continue

            if category is ActionCategory.CATEGORY_2:
                run = await self._svc.get(run.id)
                if run.status is AutonomousRunStatus.CANCELLED:
                    return self._outcome(
                        run, stopped="cancelled", executed=executed_scan_ids,
                        extra=summary,
                    )

                # Policy approval: mirrors human approve() semantics — the
                # M7.2 PlannedAction must be APPROVED before execute_approved
                # will run it — but the provenance is recorded as AUTO_POLICY
                # and attributed to the run's initiator, never a fabricated
                # human approval.
                await self._svc.mark_action_auto_approved(record.id, approved_by=run.initiated_by)
                await self._planner.approve(action.id, approved_by=run.initiated_by)
                summary["auto_approved"] += 1

                try:
                    _planned, scan = await self._planner.execute_approved(
                        action_id=action.id,
                        initiated_by=run.initiated_by,
                        launch_scan=self._launcher,
                        expected_project_id=run.project_id,
                    )
                except (
                    ActionRejectedByValidatorError,
                    ActionNotExecutableError,
                    PlannedActionNotFoundError,
                ) as exc:
                    await self._svc.mark_action_failed(
                        record.id, reason=type(exc).__name__
                    )
                    await self._audit_decision(
                        run,
                        record.id,
                        "ai.autonomous.execute_failed",
                        {"error": type(exc).__name__},
                    )
                    continue

                await self._svc.record_action_execution(record.id, scan.id)
                executed_scan_ids.append(scan.id)
                summary["executed"] += 1
                await self._audit_decision(
                    run,
                    record.id,
                    "ai.autonomous.executed",
                    {"scan_id": str(scan.id), "approval_mode": "auto_policy",
                     "attributed_to": str(run.initiated_by)},
                )
            else:
                # Category 1: the bounded cycle pauses for an explicit human
                # decision. The M7.2 PlannedAction stays PENDING_REVIEW for
                # the existing human approve/reject endpoints.
                summary["waiting_for_human"] += 1
                await self._audit_decision(
                    run,
                    record.id,
                    "ai.autonomous.awaiting_human",
                    {"category": category.value},
                )
                break

        await self._store_summary(run.id)
        run = await self._svc.get(run.id)

        if summary["waiting_for_human"]:
            if run.status is not AutonomousRunStatus.AWAITING_APPROVAL:
                run = await self._svc.plan_complete(run.id)
            return self._outcome(
                run,
                stopped="awaiting_approval",
                executed=executed_scan_ids,
                extra=summary,
            )
        if summary["executed"] or summary["auto_approved"]:
            run = await self._svc.begin_execution(run.id)
            run = await self._svc.execution_complete(run.id)
            return self._outcome(
                run,
                stopped="executed",
                executed=executed_scan_ids,
                extra=summary,
            )
        # Nothing executable: every proposal was blocked, a duplicate, or the
        # planner had nothing left to offer. The run finishes cleanly.
        run = await self._svc.complete(run.id)
        return self._finished(run, summary, "completed_no_actions")

    # ── Manual (human-approved) execution ──────────────────────────────────

    async def _execute_pending(self, run: AutonomousRun) -> CycleOutcome:
        summary: dict[str, int] = {"executed": 0}
        executed_scan_ids: list[UUID] = []
        pending = await self._svc.list_actions(run.id, status="approved")

        for record in pending:
            if record.planned_action_id is None:
                continue
            run = await self._svc.get(run.id)
            if run.status is AutonomousRunStatus.CANCELLED:
                break
            try:
                _planned, scan = await self._planner.execute_approved(
                    action_id=record.planned_action_id,
                    initiated_by=run.initiated_by,
                    launch_scan=self._launcher,
                    expected_project_id=run.project_id,
                )
            except (
                ActionRejectedByValidatorError,
                ActionNotExecutableError,
                PlannedActionNotFoundError,
            ) as exc:
                await self._svc.mark_action_failed(
                    record.id, reason=type(exc).__name__
                )
                await self._audit_decision(
                    run,
                    record.id,
                    "ai.autonomous.execute_failed",
                    {"error": type(exc).__name__},
                )
                continue

            await self._svc.record_action_execution(record.id, scan.id)
            executed_scan_ids.append(scan.id)
            summary["executed"] += 1
            await self._audit_decision(
                run,
                record.id,
                "ai.autonomous.executed",
                {"scan_id": str(scan.id),
                 "approval_mode": record.approval_mode or "manual",
                 "approved_by": str(record.approved_by) if record.approved_by else None},
            )

        await self._store_summary(run.id)
        run = await self._svc.get(run.id)
        if summary["executed"] and run.status is AutonomousRunStatus.EXECUTING:
            run = await self._svc.execution_complete(run.id)
        return self._outcome(
            run, stopped="executed", executed=executed_scan_ids, extra=summary
        )

    # ── Helpers ────────────────────────────────────────────────────────────

    def _budget_ok(self, run: AutonomousRun) -> bool:
        if run.actions_completed >= run.max_actions:
            return False
        if run.started_at is not None:
            elapsed = (self._clock.utcnow() - run.started_at).total_seconds()
            if elapsed >= run.max_runtime_seconds:
                return False
        return True

    def _cancelled_check(self, run: AutonomousRun) -> Callable[[], bool]:
        # Cooperative/soft cancellation: mirrors M7.1's soft-cancel model —
        # we flip the run status (never kill subprocesses). The closure
        # observes this cycle's run object; a concurrent cancel flips the DB
        # status, which we re-fetch before every execution step.
        def _check() -> bool:
            return run.status is AutonomousRunStatus.CANCELLED

        return _check

    @staticmethod
    def _fingerprint(action: PlannedAction) -> str:
        return "|".join(
            [
                action.action_type,
                str(action.plugin or ""),
                ",".join(sorted(str(t) for t in action.target_ids)),
                json.dumps(action.plugin_config, sort_keys=True, default=str),
            ]
        )

    async def _is_immediate_repeat(self, run_id: UUID, action: PlannedAction) -> bool:
        actions = await self._svc.list_actions(run_id)
        executed = [
            a for a in actions
            if a.status == "executed"
        ]
        if not executed:
            return False
        last = max(
            executed,
            key=lambda a: (a.cycle, a.created_at or datetime.min.replace(tzinfo=UTC)),
        )
        return (
            last.action_type == action.action_type
            and last.plugin == action.plugin
            and sorted(str(t) for t in last.target_ids)
            == sorted(str(t) for t in action.target_ids)
            and dict(last.plugin_config) == dict(action.plugin_config)
        )

    async def _store_summary(self, run_id: UUID) -> None:
        run = await self._svc.get(run_id)
        actions = await self._svc.list_actions(run_id)
        agg: dict[str, object] = {
            "total_planned": len(actions),
            "total_blocked": sum(1 for a in actions if a.status == "blocked"),
            "total_executed": sum(1 for a in actions if a.status == "executed"),
            "total_waiting_for_human": sum(
                1 for a in actions if a.status == "proposed"
            ),
            "total_auto_approved": sum(
                1 for a in actions if a.approval_mode == "auto_policy"
            ),
            "total_manual_approved": sum(
                1 for a in actions if a.approval_mode == "manual"
            ),
        }
        run.result_summary.update(agg)
        await self._run_repo.update(run)

    def _summary(
        self,
        run: AutonomousRun,
        *,
        extra: dict[str, int] | None = None,
        stopped_because: str = "",
    ) -> dict[str, object]:
        summary: dict[str, object] = dict(extra) if extra else {}
        summary["current_cycle"] = run.current_cycle or 1
        summary["actions_completed"] = run.actions_completed
        summary["stopped_because"] = stopped_because
        return summary

    def _outcome(
        self,
        run: AutonomousRun,
        *,
        executed: list[UUID] | None = None,
        extra: dict[str, int] | None = None,
        stopped: str = "",
    ) -> CycleOutcome:
        return CycleOutcome(
            run=run,
            summary=self._summary(run, extra=extra, stopped_because=stopped),
            executed_scan_ids=tuple(executed or ()),
            stopped_because=stopped,
        )

    def _finished(
        self,
        run: AutonomousRun,
        summary: dict[str, int],
        stopped: str,
    ) -> CycleOutcome:
        return self._outcome(run, extra=summary, stopped=stopped)

    async def _audit_decision(
        self,
        run: AutonomousRun,
        action_id: UUID,
        action: str,
        details: dict[str, object],
    ) -> None:
        if self._audit is None:
            return
        entry = AuditLogEntry(
            id=uuid4(),
            organization_id=None,
            actor_id=run.initiated_by,
            action=action,
            target_type="autonomous_action",
            target_id=action_id,
            ip_address=None,
            created_at=self._clock.utcnow(),
            after_state={"run_id": str(run.id), **details},
        )
        try:
            await self._audit.add(entry)
        except Exception:
            # Best-effort audit: a failed row write must never abort a run.
            return