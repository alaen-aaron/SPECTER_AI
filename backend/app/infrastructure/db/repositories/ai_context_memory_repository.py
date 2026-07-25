"""SQLAlchemy implementation of `AIContextMemoryRepository` (SRS §8)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as SqlAsyncSession

from app.domain.entities import AIContextMemory
from app.infrastructure.db.models.ai_engine import AIContextMemoryModel


def _to_entity(row: AIContextMemoryModel) -> AIContextMemory:
    return AIContextMemory(
        id=row.id,
        project_id=row.project_id,
        memory_type=row.memory_type,
        content=row.content,
        metadata=row.context_metadata or {},
        created_at=row.created_at,
    )


class SqlAlchemyAIContextMemoryRepository:
    def __init__(self, session: SqlAsyncSession) -> None:
        self._session = session

    async def add(self, memory: AIContextMemory) -> None:
        model = AIContextMemoryModel(
            id=memory.id,
            project_id=memory.project_id,
            memory_type=memory.memory_type,
            content=memory.content,
            context_metadata=memory.metadata,
        )
        self._session.add(model)
        await self._session.flush()

    async def list_for_project(self, project_id: UUID) -> list[AIContextMemory]:
        stmt = (
            select(AIContextMemoryModel)
            .where(AIContextMemoryModel.project_id == project_id)
            .order_by(AIContextMemoryModel.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return [_to_entity(row) for row in result.scalars().all()]

    async def list_for_project_by_type(
        self, project_id: UUID, memory_type: str
    ) -> list[AIContextMemory]:
        stmt = (
            select(AIContextMemoryModel)
            .where(AIContextMemoryModel.project_id == project_id)
            .where(AIContextMemoryModel.memory_type == memory_type)
            .order_by(AIContextMemoryModel.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return [_to_entity(row) for row in result.scalars().all()]

    async def delete(self, memory_id: UUID) -> None:
        row = await self._session.get(AIContextMemoryModel, memory_id)
        if row is not None:
            await self._session.delete(row)
            await self._session.flush()
