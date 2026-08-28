"""SQLAlchemy implementation of `AutonomousRunActionRepository` (M7.4)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as SqlAsyncSession

from app.domain.entities import AutonomousRunAction
from app.domain.value_objects import ActionCategory
from app.infrastructure.db.models.autonomous import AutonomousRunActionModel


def _to_entity(row: AutonomousRunActionModel) -> AutonomousRunAction:
    return AutonomousRunAction(
        id=row.id,
        run_id=row.run_id,
        project_id=row.project_id,
        cycle=row.cycle,
        action_type=row.action_type,
        plugin=row.plugin,
        title=row.title,
        description=row.description,
        justification=row.justification,
        plugin_config=row.plugin_config or {},
        target_ids=[UUID(str(u)) for u in (row.target_ids or []) if u],
        category=ActionCategory(row.category),
        status=row.status,
        approved_by=row.approved_by,
        approved_at=row.approved_at,
        approval_mode=row.approval_mode,
        planned_action_id=row.planned_action_id,
        rejection_reason=row.rejection_reason,
        scan_id=row.scan_id,
        result_summary=row.result_summary or {},
        created_at=row.created_at,
    )


class SqlAlchemyAutonomousRunActionRepository:
    def __init__(self, session: SqlAsyncSession) -> None:
        self._session = session

    async def create(self, action: AutonomousRunAction) -> None:
        model = AutonomousRunActionModel(
            id=action.id,
            run_id=action.run_id,
            project_id=action.project_id,
            cycle=action.cycle,
            action_type=action.action_type,
            plugin=action.plugin,
            title=action.title,
            description=action.description,
            justification=action.justification,
            plugin_config=action.plugin_config,
            target_ids=[str(u) for u in action.target_ids],
            category=action.category.value,
            status=action.status,
            approved_by=action.approved_by,
            approved_at=action.approved_at,
            approval_mode=action.approval_mode,
            planned_action_id=action.planned_action_id,
            rejection_reason=action.rejection_reason,
            scan_id=action.scan_id,
            result_summary=action.result_summary,
        )
        self._session.add(model)
        await self._session.flush()

    async def get(self, action_id: UUID) -> AutonomousRunAction | None:
        row = await self._session.get(AutonomousRunActionModel, action_id)
        return _to_entity(row) if row else None

    async def list_for_run(
        self,
        run_id: UUID,
        status: str | None = None,
    ) -> list[AutonomousRunAction]:
        stmt = select(AutonomousRunActionModel).where(
            AutonomousRunActionModel.run_id == run_id
        )
        if status is not None:
            stmt = stmt.where(AutonomousRunActionModel.status == status)
        stmt = stmt.order_by(AutonomousRunActionModel.cycle, AutonomousRunActionModel.created_at)
        result = await self._session.execute(stmt)
        return [_to_entity(row) for row in result.scalars().all()]

    async def update(self, action: AutonomousRunAction) -> None:
        row = await self._session.get(AutonomousRunActionModel, action.id)
        if row is None:
            return
        row.status = action.status
        row.approved_by = action.approved_by
        row.approved_at = action.approved_at
        row.approval_mode = action.approval_mode
        row.planned_action_id = action.planned_action_id
        row.rejection_reason = action.rejection_reason
        row.scan_id = action.scan_id
        row.result_summary = action.result_summary
        await self._session.flush()

    async def get_last_action_fingerprint(self, run_id: UUID) -> str | None:
        """Return a fingerprint of the most recent action for loop detection."""
        stmt = (
            select(AutonomousRunActionModel)
            .where(AutonomousRunActionModel.run_id == run_id)
            .order_by(
                AutonomousRunActionModel.cycle.desc(),
                AutonomousRunActionModel.created_at.desc(),
            )
            .limit(1)
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return f"{row.action_type}:{row.plugin}:{row.target_ids}:{row.status}"
