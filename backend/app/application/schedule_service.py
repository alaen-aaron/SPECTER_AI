"""
Schedule use-case service (Phase 2/3, hardened M7.5 Phase 1).

Manages workflow schedules — one-shot, cron, or periodic triggers
backed by Celery Beat.

M7.5 Phase 1 changes:

- **Real cron semantics**: `next_run_at` is computed by the pure domain
  cron parser (`app/domain/cron.py`), not naive timedelta math. A cron
  expression is validated when the schedule is created — a malformed or
  never-matching expression is rejected instead of being stored and
  treated as advisory.
- **Expiry (`expires_at`)**: a schedule is bounded. When the computed
  next run would land after `expires_at`, the schedule is created/paused
  inactive so it can never fire once the deadline passes.
- **Durable fire-lock**: the beat loop claims due rows with
  `FOR UPDATE SKIP LOCKED` (`SchedulesRepository.claim_due`) so two beats
  can never fire the same occurrence.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from app.domain.cron import InvalidCronExpressionError, next_run, parse_cron_expression
from app.domain.entities import CampaignScheduleConfig, Schedule
from app.domain.exceptions import (
    InvalidScheduleConfigError,
    ScheduleNotFoundError,
    WorkflowNotFoundError,
)
from app.domain.repositories import ScheduleRepository, WorkflowRepository
from app.domain.value_objects import ScheduleFrequency, ScheduleKind

# Default cron per frequency, used only when the caller supplies neither a
# frequency default, so every recurring schedule has REAL cron semantics.
_DEFAULT_CRON = {
    ScheduleFrequency.HOURLY: "0 * * * *",
    ScheduleFrequency.DAILY: "0 0 * * *",
    ScheduleFrequency.WEEKLY: "0 0 * * 0",
}

# ONCE fires on the next beat poll.
_ONCE_DELAY_SECONDS = 10


class ScheduleService:
    def __init__(
        self,
        schedule_repository: ScheduleRepository,
        workflow_repository: WorkflowRepository,
    ) -> None:
        self._schedules = schedule_repository
        self._workflows = workflow_repository

    async def create(
        self,
        workflow_id: UUID | None,
        project_id: UUID,
        frequency: ScheduleFrequency,
        cron_expression: str | None = None,
        created_by: UUID | None = None,
        *,
        kind: ScheduleKind = ScheduleKind.WORKFLOW,
        campaign: CampaignScheduleConfig | None = None,
        expires_at: datetime | None = None,
    ) -> Schedule:
        now = datetime.now(UTC)

        if kind is ScheduleKind.CAMPAIGN:
            # A campaign trigger needs the objective/budget payload and a human
            # creator to act as the AutonomousRun's initiator. The workflow_id
            # column is deliberately NULL for this kind.
            if workflow_id is not None:
                raise InvalidScheduleConfigError("A campaign schedule cannot reference a workflow.")
            if campaign is None:
                raise InvalidScheduleConfigError(
                    "A campaign schedule requires campaign configuration."
                )
            self._validate_campaign(campaign)
            if created_by is None:
                raise InvalidScheduleConfigError(
                    "A campaign schedule requires a created_by user to act as "
                    "the campaign initiator."
                )
        else:
            if workflow_id is None:
                raise InvalidScheduleConfigError(
                    "A workflow schedule requires a workflow_id."
                )
            workflow = await self._workflows.get(workflow_id)
            if workflow is None:
                raise WorkflowNotFoundError(workflow_id)

        cron_expr = self._resolve_cron(frequency, cron_expression)
        next_run_at = self._compute_next_run(now, frequency, cron_expr)

        schedule = Schedule(
            id=uuid4(),
            project_id=project_id,
            workflow_id=workflow_id if kind is ScheduleKind.WORKFLOW else None,
            kind=kind,
            frequency=frequency,
            cron_expression=cron_expr,
            is_active=True,
            next_run_at=next_run_at,
            expires_at=expires_at,
            created_by=created_by,
            created_at=now,
            updated_at=now,
            campaign_config=campaign if kind is ScheduleKind.CAMPAIGN else None,
        )
        self._enforce_expiry(schedule, now)
        await self._schedules.create(schedule)
        return schedule

    async def get(self, schedule_id: UUID) -> Schedule:
        schedule = await self._schedules.get(schedule_id)
        if schedule is None:
            raise ScheduleNotFoundError(schedule_id)
        return schedule

    async def list_for_project(self, project_id: UUID) -> list[Schedule]:
        return await self._schedules.list_for_project(project_id)

    async def pause(self, schedule_id: UUID) -> Schedule:
        schedule = await self.get(schedule_id)
        schedule.is_active = False
        schedule.updated_at = datetime.now(UTC)
        await self._schedules.update(schedule)
        return schedule

    async def resume(self, schedule_id: UUID) -> Schedule:
        schedule = await self.get(schedule_id)
        now = datetime.now(UTC)
        if schedule.is_expired(now):
            # An already-expired schedule stays dead — resuming must not
            # resurrect a trigger whose bounded lifetime has ended.
            schedule.is_active = False
            schedule.next_run_at = None
            schedule.updated_at = now
            await self._schedules.update(schedule)
            return schedule

        schedule.is_active = True
        schedule.next_run_at = self._compute_next_run(
            now, schedule.frequency, schedule.cron_expression
        )
        self._enforce_expiry(schedule, now)
        schedule.updated_at = now
        await self._schedules.update(schedule)
        return schedule

    async def delete(self, schedule_id: UUID) -> None:
        await self.get(schedule_id)
        await self._schedules.delete(schedule_id)

    async def mark_run(self, schedule_id: UUID) -> None:
        """Called after a scheduled workflow execution completes (same tx)."""
        schedule = await self.get(schedule_id)
        now = datetime.now(UTC)
        schedule.last_run_at = now

        if schedule.frequency is ScheduleFrequency.ONCE:
            schedule.is_active = False
            schedule.next_run_at = None
        else:
            schedule.next_run_at = self._compute_next_run(
                now, schedule.frequency, schedule.cron_expression
            )
            self._enforce_expiry(schedule, now)

        schedule.updated_at = now
        await self._schedules.update(schedule)

    # --- internals ----------------------------------------------------------

    def _validate_campaign(self, campaign: CampaignScheduleConfig) -> None:
        """Bounds a campaign schedule to what an interactive campaign allows.

        Mirrors `CreateAutonomousRunRequest` (1<=max_actions<=50,
        60<=max_runtime_seconds<=7200) so a scheduled campaign can never
        request more autonomy than the interactive entry point permits.
        """
        if not 1 <= campaign.max_actions <= 50:
            raise InvalidScheduleConfigError(
                f"campaign max_actions must be between 1 and 50 (got {campaign.max_actions})"
            )
        if not 60 <= campaign.max_runtime_seconds <= 7200:
            raise InvalidScheduleConfigError(
                "campaign max_runtime_seconds must be between 60 and 7200 "
                f"(got {campaign.max_runtime_seconds})"
            )

    def _resolve_cron(
        self,
        frequency: ScheduleFrequency,
        cron_expression: str | None,
    ) -> str | None:
        if frequency is ScheduleFrequency.ONCE:
            return None
        if cron_expression:
            try:
                parse_cron_expression(cron_expression)
            except InvalidCronExpressionError as exc:
                raise InvalidScheduleConfigError(str(exc)) from exc
            return cron_expression
        default = _DEFAULT_CRON.get(frequency)
        if default is None:
            raise InvalidScheduleConfigError(f"Unsupported frequency: {frequency}")
        return default

    def _compute_next_run(
        self,
        from_dt: datetime,
        frequency: ScheduleFrequency,
        cron_expression: str | None = None,
    ) -> datetime | None:
        if frequency is ScheduleFrequency.ONCE:
            return from_dt + timedelta(seconds=_ONCE_DELAY_SECONDS)
        if not cron_expression:
            raise InvalidScheduleConfigError(
                f"Recurring schedule '{frequency.value}' requires a cron expression."
            )
        try:
            cron = parse_cron_expression(cron_expression)
        except InvalidCronExpressionError as exc:
            raise InvalidScheduleConfigError(str(exc)) from exc
        computed = next_run(from_dt, cron)
        if computed is None:
            raise InvalidScheduleConfigError(
                f"Cron expression '{cron_expression}' can never match "
                "(e.g. Feb 30) — refusing to store it."
            )
        return computed

    def _enforce_expiry(self, schedule: Schedule, now: datetime) -> None:
        """If the next run lands past the deadline, disable the schedule.

        A schedule whose express lifetime has ended must never fire; it is
        left visible (so an auditor can see why it stopped) but dead.
        """
        if schedule.expires_at is None:
            return
        if now > schedule.expires_at:
            schedule.is_active = False
            schedule.next_run_at = None
            return
        if schedule.next_run_at is not None and schedule.next_run_at > schedule.expires_at:
            schedule.is_active = False
            schedule.next_run_at = None
