"""SQLAlchemy implementation of `TargetRepository`."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession as SqlAsyncSession

from app.domain.entities import Target
from app.domain.value_objects import TargetType
from app.infrastructure.db.models.target import TargetModel


def _target_to_entity(row: TargetModel) -> Target:
    return Target(
        id=row.id,
        project_id=row.project_id,
        value=row.value,
        target_type=TargetType(row.target_type),
        in_scope=row.in_scope,
        created_at=row.created_at,
        updated_at=row.updated_at,
        deleted_at=row.deleted_at,
    )


class SqlAlchemyTargetRepository:
    """Satisfies `app.domain.repositories.TargetRepository` structurally."""

    def __init__(self, session: SqlAsyncSession) -> None:
        self._session = session

    async def get_by_id(self, target_id: UUID) -> Target | None:
        row = await self._session.get(TargetModel, target_id)
        if row is None or row.deleted_at is not None:
            return None
        return _target_to_entity(row)

    async def list_for_project(self, project_id: UUID) -> list[Target]:
        stmt = select(TargetModel).where(
            TargetModel.project_id == project_id,
            TargetModel.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return [_target_to_entity(row) for row in result.scalars().all()]

    async def add(self, target: Target) -> None:
        model = TargetModel(
            id=target.id,
            project_id=target.project_id,
            value=target.value,
            target_type=target.target_type.value,
            in_scope=target.in_scope,
        )
        self._session.add(model)
        await self._session.flush()

    async def update(self, target: Target) -> None:
        stmt = (
            update(TargetModel)
            .where(TargetModel.id == target.id)
            .values(
                value=target.value,
                target_type=target.target_type.value,
                in_scope=target.in_scope,
                updated_at=datetime.now(UTC),
            )
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def soft_delete(self, target_id: UUID) -> None:
        stmt = (
            update(TargetModel)
            .where(TargetModel.id == target_id)
            .values(deleted_at=datetime.now(UTC))
        )
        await self._session.execute(stmt)
        await self._session.flush()
