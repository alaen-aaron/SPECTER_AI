"""Autonomous recovery & retry settlement (M7.4 Phase 4).

Responsible for everything a crash/restart/stranded run must be able to
fall back on, always fail-closed, always audited, always re-entrant:

1. `reconcile(run_id)` — called at the top of every operator-driven
   `cycle()`. Settles the ONE legitimate re-dispatch: an executed
   action whose scan FAILED with `failure_kind == transport` (the
   plugin never ran) and whose `retry_count` is still under budget
   (default 1). The M7.2 PlannedAction is re-armed via the exclusive
   `reapprove()` bridge and re-executed through the SAME
   `execute_approved -> ScanService.create -> Scope Guard -> Celery`
   pipeline as the original attempt. No other failure kind is retried:
   a tool that ran (or a scan refused by policy) is never auto-re-run.

2. `recover_stale(run_id)` — used by the recovery supervisor (Celery
   beat) for runs surfaced by `list_stale_active`. Runs `reconcile`
   first, then advances an EXECUTING run to OBSERVING when every
   executed action's scan is terminal (a purely internal boundary the
   next operator cycle would hit anyway), and FAILS the run closed when
   an executed action's scan row has vanished (ambiguity — nobody can
   prove what happened to the work). Live scans are left alone: they
   are progressing, not stalled.

Crash-window safety is inherited from the Phase 3 transaction model:
`ScanService.create` + planned-action EXECUTED + `record_action_execution`
all commit in ONE request-scoped transaction (dispatch fires only after
commit), so a cycle either lands atomically or leaves no trace — recovery
never has to guess between the windows the idle design feared.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from app.application.autonomous_service import AutonomousService
from app.application.planner_service import ScanLauncher
from app.domain.entities import (
    AuditLogEntry,
    AutonomousRun,
    AutonomousRunAction,
    PlannedAction,
    Scan,
)
from app.domain.repositories import (
    AuditLogRepository,
    ScanRepository,
)
from app.domain.value_objects import (
    AutonomousRunStatus,
    ScanFailureKind,
    ScanStatus,
)


class _Clock(Protocol):
    def utcnow(self) -> datetime: ...


class _ReapproveAndExecutePlanner(Protocol):
    """Narrow view of PlannerService used by recovery:

    - `reapprove` re-arms a transport-failed EXECUTED planned action.
    - `execute_approved` is the single sanctioned dispatch bridge.
    """

    async def reapprove(
        self, action_id: UUID, *, approved_by: UUID | None = None
    ) -> PlannedAction: ...

    async def execute_approved(
        self,
        action_id: UUID,
        initiated_by: UUID,
        launch_scan: ScanLauncher,
        expected_project_id: UUID | None = None,
    ) -> tuple[PlannedAction, object]: ...


@dataclass(frozen=True, slots=True)
class RecoveryOutcome:
    """What one recovery pass did (or decided not to do)."""

    run_id: UUID
    retried: int
    advanced_to_observing: bool
    failed_stalled: bool

    @property
    def acted(self) -> bool:
        return self.retried > 0 or self.advanced_to_observing or self.failed_stalled


class AutonomousRecoveryService:
    """Fail-closed settlement for crashed / stranded autonomous runs."""

    def __init__(
        self,
        *,
        autonomous_service: AutonomousService,
        planner: _ReapproveAndExecutePlanner,
        launcher: ScanLauncher,
        scan_repository: ScanRepository,
        audit_repository: AuditLogRepository | None = None,
        max_retries_per_action: int = 1,
        clock: _Clock | None = None,
    ) -> None:
        self._svc = autonomous_service
        self._planner = planner
        self._launcher = launcher
        self._scans = scan_repository
        self._audit = audit_repository
        self._max_retries = max(0, max_retries_per_action)
        self._clock = clock or _SystemClock()

    async def reconcile(self, run_id: UUID) -> RecoveryOutcome:
        """Settle retryable (TRANSPORT) failures so a cycle sees clean state."""
        run = await self._svc.get(run_id)
        if run.is_terminal:
            return RecoveryOutcome(run_id, 0, False, False)

        retried = 0
        actions = await self._svc.list_actions(run_id, status="executed")
        for action in actions:
            if action.retry_count >= self._max_retries or action.planned_action_id is None:
                continue
            if action.scan_id is None:
                continue
            scan = await self._scans.get(action.scan_id)
            if scan is None or scan.status is not ScanStatus.FAILED:
                continue
            if scan.failure_kind is None or scan.failure_kind not in ScanFailureKind.retryable():
                continue

            await self._retry_action(run, action)
            retried += 1

        return RecoveryOutcome(run_id, retried, False, False)

    async def recover_stale(self, run_id: UUID) -> RecoveryOutcome:
        """Supervisor settlement for a stale, non-terminal run."""
        retried = (await self.reconcile(run_id)).retried
        run = await self._svc.get(run_id)
        if run.is_terminal:
            return RecoveryOutcome(run_id, retried, False, False)

        executed_scan_ids: list[UUID] = []
        actions = await self._svc.list_actions(run.id)
        for action in actions:
            if action.status == "executed" and action.scan_id is not None:
                executed_scan_ids.append(action.scan_id)

        # Ambiguity check first: an executed action pointing at a scan row
        # that no longer exists is unprovable — fail the run closed, never
        # guess that the work happened or didn't.
        executed_scans: list[Scan] = []
        for scan_id in executed_scan_ids:
            scan = await self._scans.get(scan_id)
            if scan is None:
                run = await self._svc.fail(
                    run.id,
                    f"stalled: executed action references scan {scan_id} which no longer exists",
                )
                await self._audit_event(
                    run,
                    "ai.autonomous.stalled",
                    {"reason": "executed_scan_missing", "scan_id": str(scan_id)},
                )
                return RecoveryOutcome(run_id, retried, False, True)
            executed_scans.append(scan)

        if (
            run.status is AutonomousRunStatus.EXECUTING
            and executed_scan_ids
            # Every dispatched scan reached a terminal state: nothing is
            # left to run. Advance the internal EXECUTING->OBSERVING
            # boundary so the operator's next cycle observes/decides.
            and all(scan.is_terminal for scan in executed_scans)
        ):
            run = await self._svc.execution_complete(run.id)
            await self._audit_event(
                run,
                "ai.autonomous.recovered",
                {"reason": "all_executed_scans_terminal"},
            )
            return RecoveryOutcome(run_id, retried, True, False)

        return RecoveryOutcome(run_id, retried, False, False)

    async def _retry_action(
        self, run: AutonomousRun, action: AutonomousRunAction
    ) -> None:
        """One transport retry: re-arm the planned action, dispatch again."""
        planned_id = action.planned_action_id
        assert planned_id is not None

        await self._planner.reapprove(
            planned_id,
            approved_by=run.initiated_by,
        )
        _planned, scan = await self._planner.execute_approved(
            action_id=planned_id,
            initiated_by=run.initiated_by,
            launch_scan=self._launcher,
            expected_project_id=run.project_id,
        )
        scan_id = getattr(scan, "id", None)
        if not isinstance(scan_id, UUID):
            raise RuntimeError(f"retry scan did not produce a UUID id: {scan!r}")
        await self._svc.retry_action_execution(
            action.id,
            scan_id,
            max_retries=self._max_retries,
        )
        await self._audit_event(
            run,
            "ai.autonomous.execution_retry",
            {
                "action_id": str(action.id),
                "planned_action_id": str(planned_id),
                "failed_scan_id": str(action.scan_id),
                "retry_scan_id": str(scan_id),
                "retry_count": action.retry_count + 1,
            },
        )

    async def _audit_event(
        self,
        run: AutonomousRun,
        action_name: str,
        details: dict[str, object],
    ) -> None:
        if self._audit is None:
            return
        entry = AuditLogEntry(
            id=uuid4(),
            organization_id=None,
            actor_id=run.initiated_by,
            action=action_name,
            target_type="autonomous_run",
            target_id=run.id,
            ip_address=None,
            created_at=self._clock.utcnow(),
            after_state={"run_id": str(run.id), **details},
        )
        try:
            await self._audit.add(entry)
        except Exception:  # noqa: BLE001 - best-effort audit, never abort recovery
            return


class _SystemClock:
    def utcnow(self) -> datetime:
        return datetime.now(UTC)