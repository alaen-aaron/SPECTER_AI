"""Campaign schedule firing (M7.5 Phase 3).

Bridges the M7.5.1 scheduler to the M7.4 AutonomousRun state machine: a
``CAMPAIGN``-kind schedule, once claimed by the beat loop's durable
fire-lock, creates exactly one AutonomousRun in the SAME transaction and
hands it to the existing M7.4 machinery (orchestrator cycle → Planner →
approval gate → executor). The M7.4 subsystem is a protected black box:
nothing here touches the state machine, planner, validator, recovery, or
approval policy.

Exactly-once delivery of a *committed* occurrence falls out of three
existing guarantees, no new lock/queue needed:

- ``claim_due`` (FOR UPDATE SKIP LOCKED) means one beat owns each
  occurrence — and the claim dies with this transaction, so a failure
  before commit re-fires it (at-least-once).
- ``uq_autonomous_runs_active_project`` (partial unique index, M7.4
  Phase 4) is the DATABASE-level one-active-run-per-project guard: the
  application check that precedes it can race, the index cannot. A
  re-delivered fire that meets an already-running campaign becomes an
  audited SKIP, never a duplicate.
- ``mark_run`` consumes/advances the occurrence in the same commit, so a
  committed campaign is never re-fired.

Business rejections (project not active / no active authorization /
already-running campaign) CONSUME the occurrence with an audit event —
recurring schedules stay on track for their next slot and ONCE schedules
stop cleanly instead of wedging the beat loop every 30s. Unexpected
exceptions are NOT consumed: the caller rolls back and the schedule stays
due (the existing at-least-once retry path).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from enum import Enum
from uuid import UUID, uuid4

from app.application.autonomous_service import AutonomousService
from app.application.schedule_service import ScheduleService
from app.application.scope_guard_service import ScopeGuardService
from app.domain.entities import AuditLogEntry, Schedule
from app.domain.exceptions import (
    AutonomousRunActiveExistsError,
    NoActiveAuthorizationError,
    ProjectNotActiveError,
    ProjectNotFoundError,
)
from app.domain.repositories import AuditLogRepository
from app.domain.value_objects import ScheduleKind


class CampaignFireOutcome(str, Enum):
    """What a claimed campaign fire resolved to (all paths consume the
    occurrence with an audit event except `FAILED`, which rolls back)."""

    FIRED = "fired"
    SKIPPED_ACTIVE_RUN = "skipped_active_run"
    REJECTED = "rejected"


class CampaignFireResult:
    """Result of one claimed campaign fire (immutable view)."""

    __slots__ = ("outcome", "run_id", "schedule_id", "reason")

    def __init__(
        self,
        outcome: CampaignFireOutcome,
        *,
        run_id: UUID | None = None,
        schedule_id: UUID | None = None,
        reason: str | None = None,
    ) -> None:
        self.outcome = outcome
        self.run_id = run_id
        self.schedule_id = schedule_id
        self.reason = reason


class _Clock:
    def utcnow(self) -> datetime:
        return datetime.now(UTC)


class CampaignSchedulerService:
    """Fires a claimed CAMPAIGN schedule inside the caller's transaction.

    Constructing this with a `ScheduleService` that shares the beat
    loop's session means claim → validate → create run → audit → advance
    all commit atomically: a committed fire is a committed campaign.
    """

    _SCOPE_PREFLIGHT_FAILURES = (
        ProjectNotFoundError,
        ProjectNotActiveError,
        NoActiveAuthorizationError,
    )

    def __init__(
        self,
        schedule_service: ScheduleService,
        autonomous_service: AutonomousService,
        scope_guard: ScopeGuardService,
        audit_repository: AuditLogRepository,
        *,
        clock: _Clock | None = None,
        id_factory: Callable[[], UUID] | None = None,
    ) -> None:
        self._schedules = schedule_service
        self._autonomous = autonomous_service
        self._scope_guard = scope_guard
        self._audit = audit_repository
        self._clock = clock or _Clock()
        self._id = id_factory or uuid4

    async def fire(self, schedule: Schedule) -> CampaignFireResult:
        """Process one claimed, due campaign schedule (same transaction)."""
        if schedule.kind is not ScheduleKind.CAMPAIGN or schedule.campaign_config is None:
            raise ValueError(
                f"CampaignSchedulerService.fire called for non-campaign schedule {schedule.id}"
            )

        now = self._clock.utcnow()
        actor = schedule.created_by or schedule.project_id

        # 1. Authorization preflight — reuse the existing Scope Guard
        #    (project exists, ACTIVE, has an active authorization record).
        #    No target list here: real targets are chosen by the planner
        #    and re-checked against Scope Guard at execution time inside
        #    M7.4. A project whose authorization lapsed is a rejection,
        #    never a silent scan.
        try:
            await self._scope_guard.validate_targets(schedule.project_id, [])
        except self._SCOPE_PREFLIGHT_FAILURES as exc:
            await self._audit.add(
                AuditLogEntry(
                    id=self._id(),
                    organization_id=None,
                    actor_id=actor,
                    action="scheduler.campaign_rejected",
                    target_type="schedule",
                    target_id=schedule.id,
                    ip_address=None,
                    created_at=now,
                    after_state={
                        "project_id": str(schedule.project_id),
                        "reason": f"{type(exc).__name__}: {exc}",
                    },
                )
            )
            await self._schedules.mark_run(schedule.id)
            return CampaignFireResult(
                outcome=CampaignFireOutcome.REJECTED,
                schedule_id=schedule.id,
                reason=f"{type(exc).__name__}: {exc}",
            )

        config = schedule.campaign_config
        try:
            run = await self._autonomous.create(
                project_id=schedule.project_id,
                initiated_by=actor,
                objective=config.objective,
                max_actions=config.max_actions,
                max_runtime_seconds=config.max_runtime_seconds,
            )
        except AutonomousRunActiveExistsError:
            # M7.4 invariant: at most one non-terminal run per project.
            # This schedule's slot already has a live campaign; consume
            # this occurrence with an auditable skip (pure deferral would
            # wedge a repeating schedule into a 30s spin loop).
            await self._audit.add(
                AuditLogEntry(
                    id=self._id(),
                    organization_id=None,
                    actor_id=actor,
                    action="scheduler.campaign_skipped_active_run",
                    target_type="schedule",
                    target_id=schedule.id,
                    ip_address=None,
                    created_at=now,
                    after_state={
                        "project_id": str(schedule.project_id),
                        "reason": "an autonomous run is already active for this project",
                    },
                )
            )
            await self._schedules.mark_run(schedule.id)
            return CampaignFireResult(
                outcome=CampaignFireOutcome.SKIPPED_ACTIVE_RUN,
                schedule_id=schedule.id,
                reason="active autonomous run exists",
            )

        await self._audit.add(
            AuditLogEntry(
                id=self._id(),
                organization_id=None,
                actor_id=actor,
                action="scheduler.campaign_created",
                target_type="schedule",
                target_id=schedule.id,
                ip_address=None,
                created_at=now,
                after_state={
                    "project_id": str(schedule.project_id),
                    "run_id": str(run.id),
                    "objective": config.objective,
                    "max_actions": config.max_actions,
                    "max_runtime_seconds": config.max_runtime_seconds,
                    "initiated_by": str(schedule.created_by) if schedule.created_by else None,
                },
            )
        )
        await self._schedules.mark_run(schedule.id)
        return CampaignFireResult(
            outcome=CampaignFireOutcome.FIRED,
            run_id=run.id,
            schedule_id=schedule.id,
        )
