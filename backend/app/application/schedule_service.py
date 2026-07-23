"""
Schedule use-case service (Phase 2/3).

Manages workflow schedules — one-shot, cron, or periodic triggers
backed by Celery Beat.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from app.domain.entities import Schedule
from app.domain.exceptions import (
    InvalidScheduleConfigError,
    ScheduleNotFoundError,
    WorkflowNotFoundError,
)
from app.domain.repositories import ScheduleRepository, WorkflowRepository
from app.domain.value_objects import ScheduleFrequency


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
        workflow_id: UUID,
        project_id: UUID,
        frequency: ScheduleFrequency,
        cron_expression: str | None = None,
        created_by: UUID | None = None,
    ) -> Schedule:
        workflow = await self._workflows.get(workflow_id)
        if workflow is None:
            raise WorkflowNotFoundError(workflow_id)

        if frequency is ScheduleFrequency.ONCE:
            cron_expr = None
        elif cron_expression:
            cron_expr = cron_expression
        elif frequency is ScheduleFrequency.HOURLY:
            cron_expr = "0 * * * *"
        elif frequency is ScheduleFrequency.DAILY:
            cron_expr = "0 0 * * *"
        elif frequency is ScheduleFrequency.WEEKLY:
            cron_expr = "0 0 * * 0"
        else:
            raise InvalidScheduleConfigError(f"Unsupported frequency: {frequency}")

        now = datetime.now(UTC)
        next_run = self._compute_next_run(now, frequency, cron_expr)

        schedule = Schedule(
            id=uuid4(),
            workflow_id=workflow_id,
            project_id=project_id,
            frequency=frequency,
            cron_expression=cron_expr,
            is_active=True,
            next_run_at=next_run,
            created_by=created_by,
            created_at=now,
        )
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
        await self._schedules.update(schedule)
        return schedule

    async def resume(self, schedule_id: UUID) -> Schedule:
        schedule = await self.get(schedule_id)
        schedule.is_active = True
        now = datetime.now(UTC)
        schedule.next_run_at = self._compute_next_run(
            now, schedule.frequency, schedule.cron_expression
        )
        await self._schedules.update(schedule)
        return schedule

    async def delete(self, schedule_id: UUID) -> None:
        await self.get(schedule_id)
        await self._schedules.delete(schedule_id)

    async def mark_run(self, schedule_id: UUID) -> None:
        """Called after a scheduled workflow execution completes."""
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

        await self._schedules.update(schedule)

    def _compute_next_run(
        self,
        from_dt: datetime,
        frequency: ScheduleFrequency,
        cron_expression: str | None = None,
    ) -> datetime:
        if frequency is ScheduleFrequency.ONCE:
            return from_dt + timedelta(seconds=10)
        elif frequency is ScheduleFrequency.HOURLY:
            return from_dt + timedelta(hours=1)
        elif frequency is ScheduleFrequency.DAILY:
            return from_dt + timedelta(days=1)
        elif frequency is ScheduleFrequency.WEEKLY:
            return from_dt + timedelta(weeks=1)
        return from_dt + timedelta(hours=1)
