"""
M7.5 Phase 4-B2 — outbox relay claim loop (real Postgres).

Verifies `run_outbox_relay` end-to-end against the real repository
(what the fakes cannot model): one pass of the §13 loop (a) requeues
expired `delivering` leases, claims the oldest due `pending` batch and
COMMITS the claim, (b) delivers every claimed event OUTSIDE any
transaction (the Phase 4-B2 dry-run default, or an injected transport),
(c) settles the whole batch in ONE final transaction —
`mark_delivered` on success, `mark_failed(retry_at=None)` (the Phase
4-B2 terminal dead-letter) on delivery error — and (d) returns the
worker-visible `OutboxRelayResult` summary.

The relay opens and commits its own short-lived sessions (mirroring the
`specter.outbox_relay` Celery task), so unlike the 4-B1 suite these
tests COMMIT their inserted rows before invoking the loop and read
post-pass state through fresh sessions.

Skipped automatically when DATABASE_URL is unreachable (see
`tests/integration/conftest.py`).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.domain.entities import OutboxEvent
from app.domain.value_objects import OutboxEventType
from app.infrastructure.db.models.event_outbox import EventOutboxModel
from app.infrastructure.event.relay import (
    OutboxRelayResult,
    SessionFactory,
    run_outbox_relay,
)
from tests.integration.conftest import requires_postgres

pytestmark = requires_postgres


async def _insert_event(
    db_session: AsyncSession,
    *,
    tag: str,
    now: datetime,
    event_id: UUID | None = None,
    event_type: str = OutboxEventType.CAMPAIGN_RUN_STARTED.value,
    status: str = "pending",
    attempts: int = 0,
    available_after: datetime | None = None,
    next_retry_at: datetime | None = None,
) -> UUID:
    """Insert one outbox row inside the caller's open transaction."""
    row = EventOutboxModel(
        event_id=event_id or uuid4(),
        event_type=event_type,
        schema_version=1,
        organization_id=None,
        project_id=None,
        schedule_id=None,
        autonomous_run_id=None,
        occurred_at=now,
        payload={"tag": tag},
        created_at=now,
        scan_id=None,
        specversion="1.0",
        available_after=available_after if available_after is not None else now,
        status=status,
        attempts=attempts,
        max_attempts=10,
        last_error=None,
        next_retry_at=next_retry_at,
        delivered_at=None,
    )
    db_session.add(row)
    await db_session.flush()
    return row.event_id


async def _fetch_row(session_factory: SessionFactory, event_id: UUID) -> EventOutboxModel:
    """Read a row through a fresh relay-owned session (`db_session` may
    hold stale identity-map state after its own commit followed by the
    relay's committed writes)."""
    async with session_factory() as session:
        result = await session.execute(
            select(EventOutboxModel).where(EventOutboxModel.event_id == event_id)
        )
        return result.scalars().one()


@pytest.fixture(autouse=True)
def _clean_event_outbox() -> None:
    """Give every test an empty event_outbox table.

    The relay COMMITS its passes and tests commit their seeded rows, so
    leftover rows would pollute exact-count and batch assertions across
    tests. Deleting all rows at the start of every test keeps the 4-B2
    suite hermetic without touching the shared db_session fixture.
    """

    async def _clean() -> None:
        engine = create_async_engine(str(get_settings().DATABASE_URL))
        try:
            async with engine.begin() as conn:
                await conn.execute(delete(EventOutboxModel))
        finally:
            await engine.dispose()

    asyncio.run(_clean())


@pytest_asyncio.fixture
async def session_factory() -> AsyncIterator[SessionFactory]:
    """A callable that opens the relay's own short-lived §13 sessions.

    `run_outbox_relay` creates and commits its requeue+claim and settle
    transactions through these sessions, so the shared `db_session`
    fixture (single transaction, rolled back) cannot drive it. A fresh
    engine pool matches what `specter.outbox_relay` passes in
    production.
    """
    engine = create_async_engine(str(get_settings().DATABASE_URL))
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_claims_delivers_and_settles_due_batch(
    db_session: AsyncSession,
    session_factory: SessionFactory,
) -> None:
    """A due `pending` batch is claimed, dry-run delivered, and settled
    delivered in one pass; the lease watermark is cleared."""
    now = datetime.now(UTC)
    id1 = await _insert_event(db_session, tag="one", now=now)
    id2 = await _insert_event(db_session, tag="two", now=now)
    await db_session.commit()

    result = await run_outbox_relay(session_factory=session_factory, now=now)

    assert isinstance(result, OutboxRelayResult)
    assert result.claimed == 2
    assert result.delivered == 2
    assert result.dead_lettered == 0
    assert result.lost_race == 0

    for event_id in (id1, id2):
        row = await _fetch_row(session_factory, event_id)
        assert row.status == "delivered"
        assert row.delivered_at is not None
        assert row.attempts == 1
        assert row.next_retry_at is None
        assert row.last_error is None


@pytest.mark.asyncio
async def test_b_requeues_stale_lease_then_claims_and_delivers(
    db_session: AsyncSession,
    session_factory: SessionFactory,
) -> None:
    """An expired `delivering` lease is requeued to `pending` (attempts
    kept), then claimed so the pass delivers it — attempt accounting
    shows requeue(2) + claim-bump(3)."""
    now = datetime.now(UTC)
    stale_id = await _insert_event(
        db_session,
        tag="stale",
        now=now,
        status="delivering",
        attempts=2,
        next_retry_at=now - timedelta(minutes=1),
    )
    await db_session.commit()

    result = await run_outbox_relay(session_factory=session_factory, now=now)

    assert result.claimed == 1
    assert result.delivered == 1
    assert result.dead_lettered == 0
    assert result.lost_race == 0

    row = await _fetch_row(session_factory, stale_id)
    assert row.status == "delivered"
    assert row.attempts == 3
    assert row.delivered_at is not None
    assert row.next_retry_at is None


@pytest.mark.asyncio
async def test_c_leaves_non_due_rows_untouched(
    db_session: AsyncSession,
    session_factory: SessionFactory,
) -> None:
    """Rows gated by a future `available_after` or carrying a live
    `delivering` lease are neither requeued nor claimed — the pass is a
    no-op for them."""
    now = datetime.now(UTC)
    future_id = await _insert_event(
        db_session,
        tag="future",
        now=now,
        available_after=now + timedelta(hours=1),
    )
    live_id = await _insert_event(
        db_session,
        tag="live",
        now=now,
        status="delivering",
        attempts=1,
        next_retry_at=now + timedelta(minutes=5),
    )
    await db_session.commit()

    result = await run_outbox_relay(session_factory=session_factory, now=now)

    assert result.claimed == 0
    assert result.delivered == 0
    assert result.dead_lettered == 0
    assert result.lost_race == 0

    future_row = await _fetch_row(session_factory, future_id)
    assert future_row.status == "pending"
    assert future_row.attempts == 0

    live_row = await _fetch_row(session_factory, live_id)
    assert live_row.status == "delivering"
    assert live_row.attempts == 1
    assert live_row.next_retry_at == now + timedelta(minutes=5)


@pytest.mark.asyncio
async def test_d_delivery_error_dead_letters_that_event_only(
    db_session: AsyncSession,
    session_factory: SessionFactory,
) -> None:
    """A raising transport dead-letters only the failing event
    (`mark_failed` with `retry_at=None`) while the rest of the batch is
    still delivered — one bad event never aborts the pass."""
    now = datetime.now(UTC)
    ok_id = await _insert_event(db_session, tag="ok", now=now)
    fail_id = await _insert_event(db_session, tag="fail", now=now)
    await db_session.commit()

    delivered_ids: list[UUID] = []

    async def _transport(event: OutboxEvent) -> None:
        if event.payload["tag"] == "fail":
            raise RuntimeError("boom")
        delivered_ids.append(event.id)

    result = await run_outbox_relay(
        session_factory=session_factory,
        now=now,
        deliver=_transport,
    )

    assert result.claimed == 2
    assert result.delivered == 1
    assert result.dead_lettered == 1
    assert result.lost_race == 0
    assert delivered_ids == [ok_id]

    ok_row = await _fetch_row(session_factory, ok_id)
    assert ok_row.status == "delivered"
    assert ok_row.delivered_at is not None
    assert ok_row.last_error is None

    fail_row = await _fetch_row(session_factory, fail_id)
    assert fail_row.status == "dead_letter"
    assert fail_row.next_retry_at is None
    assert fail_row.delivered_at is None
    assert fail_row.last_error is not None
    assert "RuntimeError: boom" in fail_row.last_error


@pytest.mark.asyncio
async def test_e_limit_gates_the_claimed_batch(
    db_session: AsyncSession,
    session_factory: SessionFactory,
) -> None:
    """`limit` bounds the batch: exactly two rows are delivered and the
    surplus stays `pending` awaiting the next pass."""
    now = datetime.now(UTC)
    for i in range(3):
        await _insert_event(db_session, tag=f"n{i}", now=now)
    await db_session.commit()

    result = await run_outbox_relay(session_factory=session_factory, now=now, limit=2)

    assert result.claimed == 2
    assert result.delivered == 2
    assert result.dead_lettered == 0

    async with session_factory() as session:
        rows = (await session.execute(select(EventOutboxModel))).scalars().all()
    assert len(rows) == 3
    assert len([r for r in rows if r.status == "delivered"]) == 2
    assert len([r for r in rows if r.status == "pending"]) == 1