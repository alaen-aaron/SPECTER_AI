"""SQLAlchemy implementation of `OutboxEventRepository` (M7.5 Phase 4-A)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession as SqlAsyncSession

from app.domain.entities import OutboxEvent
from app.infrastructure.db.models.event_outbox import EventOutboxModel


class SqlAlchemyOutboxEventRepository:
    """Satisfies `app.domain.repositories.OutboxEventRepository` structurally.

    ``add`` never commits — the caller's transaction owns the commit so
    the event row is atomic with the domain state change it describes.
    """

    def __init__(self, session: SqlAsyncSession) -> None:
        self._session = session

    async def add(self, event: OutboxEvent) -> None:
        model = EventOutboxModel(
            event_id=event.id,
            event_type=event.event_type,
            schema_version=event.schema_version,
            organization_id=event.organization_id,
            project_id=event.project_id,
            schedule_id=event.schedule_id,
            autonomous_run_id=event.autonomous_run_id,
            occurred_at=event.occurred_at,
            payload=event.payload,
            **({"created_at": event.created_at} if event.created_at is not None else {}),
        )
        self._session.add(model)
        await self._session.flush()


async def get_outbox_event_by_id(
    session: SqlAsyncSession, event_id: UUID
) -> EventOutboxModel | None:
    """Test/read-back helper: load one row by primary key.

    Explicitly NOT part of the domain repository contract (Phase 4-A
    writes durably and produces no read API); sits at the infrastructure
    layer for targeted verification and future phases.
    """
    from sqlalchemy import select

    stmt = select(EventOutboxModel).where(EventOutboxModel.event_id == event_id)
    result = await session.execute(stmt)
    return result.scalars().first()