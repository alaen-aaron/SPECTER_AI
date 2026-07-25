"""SQLAlchemy implementation of `PlannedActionRepository` (SRS §8.4)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as SqlAsyncSession

from app.domain.entities import PlannedAction
from app.domain.value_objects import PlannedActionStatus
from app.infrastructure.db.models.ai_engine import PlannedActionModel


def _to_entity(row: PlannedActionModel) -> PlannedAction:
    return PlannedAction(
        id=row.id,
        project_id=row.project_id,
        action_type=row.action_type,
        title=row.title,
        description=row.description,
        justification=row.justification,
        plugin=row.plugin,
        target_ids=[UUID(str(u)) for u in (row.target_ids or []) if u],
        plugin_config=row.plugin_config or {},
        status=PlannedActionStatus(row.status),
        approved_by=row.approved_by,
        approved_at=row.approved_at,
        rejection_reason=row.rejection_reason,
        expires_at=row.expires_at,
        created_by=row.created_by,
        created_at=row.created_at,
    )


class SqlAlchemyPlannedActionRepository:
    def __init__(self, session: SqlAsyncSession) -> None:
        self._session = session

    async def create(self, action: PlannedAction) -> None:
        model = PlannedActionModel(
            id=action.id,
            project_id=action.project_id,
            action_type=action.action_type,
            title=action.title,
            description=action.description,
            justification=action.justification,
            plugin=action.plugin,
            target_ids=action.target_ids,
            plugin_config=action.plugin_config,
            status=action.status.value,
            approved_by=action.approved_by,
            approved_at=action.approved_at,
            rejection_reason=action.rejection_reason,
            expires_at=action.expires_at,
            created_by=action.created_by,
        )
        self._session.add(model)
        await self._session.flush()

    async def get(self, action_id: UUID) -> PlannedAction | None:
        row = await self._session.get(PlannedActionModel, action_id)
        return _to_entity(row) if row else None

    async def list_for_project(
        self,
        project_id: UUID,
        status: PlannedActionStatus | None = None,
        limit: int = 20,
        cursor: datetime | None = None,
    ) -> list[PlannedAction]:
        stmt = (
            select(PlannedActionModel)
            .where(PlannedActionModel.project_id == project_id)
        )
        if status is not None:
            stmt = stmt.where(PlannedActionModel.status == status.value)
        if cursor is not None:
            stmt = stmt.where(PlannedActionModel.created_at < cursor)
        stmt = stmt.order_by(PlannedActionModel.created_at.desc()).limit(limit)
        result = await self._session.execute(stmt)
        return [_to_entity(row) for row in result.scalars().all()]

    async def update(self, action: PlannedAction) -> None:
        row = await self._session.get(PlannedActionModel, action.id)
        if row is None:
            return
        row.status = action.status.value
        row.approved_by = action.approved_by
        row.approved_at = action.approved_at
        row.rejection_reason = action.rejection_reason
        await self._session.flush()
