"""SQLAlchemy implementation of `AutonomousRunRepository` (M7.4)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession as SqlAsyncSession

from app.domain.entities import AutonomousRun
from app.domain.value_objects import AutonomousRunStatus
from app.infrastructure.db.models.autonomous import (
    AutonomousRunActionModel,
    AutonomousRunModel,
)


def _to_entity(row: AutonomousRunModel) -> AutonomousRun:
    return AutonomousRun(
        id=row.id,
        project_id=row.project_id,
        initiated_by=row.initiated_by,
        status=AutonomousRunStatus(row.status),
        objective=row.objective,
        max_actions=row.max_actions,
        max_runtime_seconds=row.max_runtime_seconds,
        current_cycle=row.current_cycle,
        actions_completed=row.actions_completed,
        approval_policy=row.approval_policy,
        started_at=row.started_at,
        completed_at=row.completed_at,
        last_heartbeat_at=row.last_heartbeat_at,
        error_message=row.error_message,
        result_summary=row.result_summary or {},
        created_at=row.created_at,
    )


class SqlAlchemyAutonomousRunRepository:
    def __init__(self, session: SqlAsyncSession) -> None:
        self._session = session

    async def create(self, run: AutonomousRun) -> None:
        model = AutonomousRunModel(
            id=run.id,
            project_id=run.project_id,
            initiated_by=run.initiated_by,
            status=run.status.value,
            objective=run.objective,
            max_actions=run.max_actions,
            max_runtime_seconds=run.max_runtime_seconds,
            current_cycle=run.current_cycle,
            actions_completed=run.actions_completed,
            approval_policy=run.approval_policy,
            started_at=run.started_at,
            completed_at=run.completed_at,
            last_heartbeat_at=run.last_heartbeat_at,
            error_message=run.error_message,
            result_summary=run.result_summary,
        )
        self._session.add(model)
        await self._session.flush()

    async def get(self, run_id: UUID) -> AutonomousRun | None:
        row = await self._session.get(AutonomousRunModel, run_id)
        return _to_entity(row) if row else None

    async def list_for_project(
        self,
        project_id: UUID,
        status: AutonomousRunStatus | None = None,
        limit: int = 20,
        cursor: datetime | None = None,
    ) -> list[AutonomousRun]:
        stmt = select(AutonomousRunModel).where(
            AutonomousRunModel.project_id == project_id
        )
        if status is not None:
            stmt = stmt.where(AutonomousRunModel.status == status.value)
        if cursor is not None:
            stmt = stmt.where(AutonomousRunModel.created_at < cursor)
        stmt = stmt.order_by(AutonomousRunModel.created_at.desc()).limit(limit)
        result = await self._session.execute(stmt)
        return [_to_entity(row) for row in result.scalars().all()]

    async def get_active_for_project(self, project_id: UUID) -> AutonomousRun | None:
        terminal_statuses = {s.value for s in AutonomousRunStatus} - {
            AutonomousRunStatus.COMPLETED.value,
            AutonomousRunStatus.CANCELLED.value,
            AutonomousRunStatus.FAILED.value,
        }
        stmt = (
            select(AutonomousRunModel)
            .where(
                AutonomousRunModel.project_id == project_id,
                AutonomousRunModel.status.in_(terminal_statuses),
            )
            .order_by(AutonomousRunModel.created_at.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        return _to_entity(row) if row else None

    async def update(self, run: AutonomousRun) -> None:
        row = await self._session.get(AutonomousRunModel, run.id)
        if row is None:
            return
        row.status = run.status
        row.objective = run.objective
        row.current_cycle = run.current_cycle
        row.actions_completed = run.actions_completed
        row.started_at = run.started_at
        row.completed_at = run.completed_at
        row.last_heartbeat_at = run.last_heartbeat_at
        row.error_message = run.error_message
        row.result_summary = run.result_summary
        await self._session.flush()

    async def count_actions(self, run_id: UUID) -> int:
        stmt = select(func.count()).where(AutonomousRunActionModel.run_id == run_id)
        result = await self._session.execute(stmt)
        return result.scalar_one()
