"""SQLAlchemy implementation of `OutboxEventRepository` (M7.5 Phase 4-A, 4-B1)."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession as SqlAsyncSession

from app.domain.entities import OutboxEvent
from app.domain.exceptions import OutboxTransitionError
from app.infrastructure.db.models.event_outbox import EventOutboxModel


def _outbox_model_to_entity(row: EventOutboxModel) -> OutboxEvent:
    return OutboxEvent(
        id=row.event_id,
        event_type=row.event_type,
        schema_version=row.schema_version,
        occurred_at=row.occurred_at,
        payload=row.payload,
        organization_id=row.organization_id,
        project_id=row.project_id,
        schedule_id=row.schedule_id,
        autonomous_run_id=row.autonomous_run_id,
        created_at=row.created_at,
        scan_id=row.scan_id,
        specversion=row.specversion,
        available_after=row.available_after,
        status=row.status,
        attempts=row.attempts,
        max_attempts=row.max_attempts,
        last_error=row.last_error,
        next_retry_at=row.next_retry_at,
        delivered_at=row.delivered_at,
    )


class SqlAlchemyOutboxEventRepository:
    """Satisfies `app.domain.repositories.OutboxEventRepository` structurally.

    Never commits: the caller's transaction owns the commit so the event
    row is atomic with the domain state change it describes (Phase 4-A),
    and so a claimed batch's ``delivering`` status — which grants
    ownership of the rows to the claiming worker — becomes durable
    before any other worker can observe it (Phase 4-B1). ``add`` flushes
    only, and the delivery operations flush only.
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

    async def claim_next_batch(
        self,
        now: datetime,
        limit: int = 50,
        lease: timedelta | None = None,
    ) -> list[OutboxEvent]:
        """Phase 4-B1: claim the oldest due ``pending`` batch (fire-lock).

        Rows are selected oldest-first by (``available_after``,
        ``event_id``) under `FOR UPDATE SKIP LOCKED`, then transitioned
        to ``delivering`` with ``attempts`` bumped. ``next_retry_at`` is
        set to ``now + lease`` (the lease watermark) when a lease is
        supplied so a crashed claimer's rows are recoverable; when no
        lease is given it is ``NULL`` (no recovery guarantee). The claim
        lives or dies with the caller's transaction — commit advances the
        rows, rollback leaves them ``pending`` for the next claimer.
        Mirrors `SqlAlchemyScheduleRepository.claim_due`.
        """
        stmt = (
            select(EventOutboxModel)
            .where(EventOutboxModel.status == "pending")
            .where(EventOutboxModel.available_after <= now)
            .where(
                or_(
                    EventOutboxModel.next_retry_at.is_(None),
                    EventOutboxModel.next_retry_at <= now,
                )
            )
            .order_by(EventOutboxModel.available_after, EventOutboxModel.event_id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())
        if not rows:
            return []
        lease_expiry = now + lease if lease is not None else None
        for row in rows:
            row.status = "delivering"
            row.attempts += 1
            row.next_retry_at = lease_expiry
        await self._session.flush()
        return [_outbox_model_to_entity(row) for row in rows]

    async def requeue_expired(self, now: datetime) -> list[OutboxEvent]:
        """Phase 4-B1: revive stale ``delivering`` leases.

        Rows whose lease watermark (``next_retry_at``) has passed are
        claimed under `FOR UPDATE SKIP LOCKED` and returned to
        ``pending`` with the watermark cleared, so the next
        ``claim_next_batch`` (or a retry ``mark_failed`` backoff) can
        select them again. A crashed claimer's batch is therefore only
        lost while the lease is live; ``pending``, ``delivered`` and
        ``dead_letter`` rows are never touched.
        """
        stmt = (
            select(EventOutboxModel)
            .where(EventOutboxModel.status == "delivering")
            .where(EventOutboxModel.next_retry_at.is_not(None))
            .where(EventOutboxModel.next_retry_at <= now)
            .order_by(EventOutboxModel.next_retry_at, EventOutboxModel.event_id)
            .with_for_update(skip_locked=True)
        )
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())
        for row in rows:
            row.status = "pending"
            row.next_retry_at = None
        await self._session.flush()
        return [_outbox_model_to_entity(row) for row in rows]

    async def mark_delivered(self, event_id: UUID) -> None:
        """Phase 4-B1: settle a claimed row as delivered.

        Only a ``delivering`` row may be delivered. On a lost race (the
        row is no longer ``delivering`` — e.g. it was requeued and
        re-claimed elsewhere) an `OutboxTransitionError` is raised with
        the row's current status, and nothing is written.
        """
        stmt = (
            update(EventOutboxModel)
            .where(EventOutboxModel.event_id == event_id)
            .where(EventOutboxModel.status == "delivering")
            .values(status="delivered", delivered_at=func.now(), next_retry_at=None)
        )
        result = await self._session.execute(stmt)
        if result.rowcount == 0:
            raise OutboxTransitionError(
                event_id,
                await self._current_status_or_unknown(event_id),
                "mark_delivered",
            )
        await self._session.flush()

    async def mark_failed(
        self,
        event_id: UUID,
        error: str,
        retry_at: datetime | None,
    ) -> None:
        """Phase 4-B1: settle a claimed row as failed.

        Only a ``delivering`` row may be marked failed. When ``retry_at``
        is supplied the row returns to ``pending`` with that value as its
        retry-backoff gate; otherwise it dead-letters (its queue
        exhausted). ``last_error`` is always persisted. On a lost race an
        `OutboxTransitionError` is raised and nothing is written.
        """
        stmt = (
            update(EventOutboxModel)
            .where(EventOutboxModel.event_id == event_id)
            .where(EventOutboxModel.status == "delivering")
            .values(
                status="dead_letter" if retry_at is None else "pending",
                last_error=error,
                next_retry_at=retry_at,
            )
        )
        result = await self._session.execute(stmt)
        if result.rowcount == 0:
            raise OutboxTransitionError(
                event_id,
                await self._current_status_or_unknown(event_id),
                "mark_failed",
            )
        await self._session.flush()

    async def _current_status_or_unknown(self, event_id: UUID) -> str:
        stmt = select(EventOutboxModel.status).where(
            EventOutboxModel.event_id == event_id
        )
        result = await self._session.execute(stmt)
        value = result.scalar_one_or_none()
        return "missing" if value is None else value


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