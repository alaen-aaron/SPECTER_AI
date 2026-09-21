"""
M7.5 Phase 4-B1 — outbox claim/lease delivery machinery (real Postgres).

Verifies the durable claim/lease infrastructure the fakes cannot model:
that Phase 4-A written rows (no delivery columns) become claimable via
migration server defaults; that `claim_next_batch` fire-locks the oldest
due `pending` batch (lease watermark, attempt accounting, ordering,
`FOR UPDATE SKIP LOCKED`); that `requeue_expired` revives stale
`delivering` leases; that `mark_delivered`/`mark_failed` settle only
`delivering` rows (raising `OutboxTransitionError` on a lost race); and
that every operation is flush-only — nothing is durable until the
caller's transaction commits.

Skipped automatically when DATABASE_URL is unreachable (see
`tests/integration/conftest.py`).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import get_settings
from app.domain.entities import OutboxEvent
from app.domain.exceptions import OutboxTransitionError
from app.domain.value_objects import OutboxEventType
from app.infrastructure.db.models.event_outbox import EventOutboxModel
from app.infrastructure.db.repositories.event_outbox_repository import (
    SqlAlchemyOutboxEventRepository,
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
    last_error: str | None = None,
    delivered_at: datetime | None = None,
    organization_id: UUID | None = None,
    project_id: UUID | None = None,
    autonomous_run_id: UUID | None = None,
) -> UUID:
    """Insert one outbox row with explicit (caller-controlled) delivery
    fields inside the caller's open transaction."""
    row = EventOutboxModel(
        event_id=event_id or uuid4(),
        event_type=event_type,
        schema_version=1,
        organization_id=organization_id,
        project_id=project_id,
        schedule_id=None,
        autonomous_run_id=autonomous_run_id,
        occurred_at=now,
        payload={"tag": tag},
        created_at=now,
        scan_id=None,
        specversion="1.0",
        available_after=available_after if available_after is not None else now,
        status=status,
        attempts=attempts,
        max_attempts=10,
        last_error=last_error,
        next_retry_at=next_retry_at,
        delivered_at=delivered_at,
    )
    db_session.add(row)
    await db_session.flush()
    return row.event_id


async def _get_row(db_session: AsyncSession, event_id: UUID) -> EventOutboxModel:
    result = await db_session.execute(
        select(EventOutboxModel).where(EventOutboxModel.event_id == event_id)
    )
    return result.scalars().one()


@pytest.fixture(autouse=True)
def _clean_event_outbox() -> None:
    """Give every test an empty event_outbox table.

    Each test inserts only the rows it asserts against, so any leftover
    committed rows (e.g. from an earlier test that committed before
    rolling back its session) would pollute exact-count and `== [stale]`
    assertions. Deleting all rows at the start of every test keeps the
    4-B1 suite hermetic without touching the shared db_session fixture.
    """

    async def _clean() -> None:
        engine = create_async_engine(str(get_settings().DATABASE_URL))
        try:
            async with engine.begin() as conn:
                await conn.execute(delete(EventOutboxModel))
        finally:
            await engine.dispose()

    asyncio.run(_clean())


@pytest.mark.asyncio
async def test_a_legacy_rows_get_server_defaults_and_remain_claimable(
    db_session: AsyncSession,
) -> None:
    """A Phase 4-A written row (raw insert, no delivery columns) gets the
    migration's server defaults and is immediately claimable — additive."""
    legacy_id = uuid4()
    await db_session.execute(
        text("""
            INSERT INTO event_outbox
                (event_id, event_type, schema_version, occurred_at, payload)
            VALUES
                (:event_id, :event_type, :schema_version, :occurred_at,
                 CAST(:payload AS jsonb))
            """),
        {
            "event_id": legacy_id,
            "event_type": OutboxEventType.CAMPAIGN_RUN_STARTED.value,
            "schema_version": 1,
            "occurred_at": datetime.now(UTC),
            "payload": json.dumps({"tag": "legacy"}),
        },
    )
    await db_session.flush()
    now = datetime.now(UTC)

    row = await _get_row(db_session, legacy_id)
    assert row.status == "pending"
    assert row.attempts == 0
    assert row.max_attempts == 10
    assert row.specversion == "1.0"
    assert row.available_after is not None
    assert row.next_retry_at is None
    assert row.delivered_at is None

    repo = SqlAlchemyOutboxEventRepository(db_session)
    claimed = await repo.claim_next_batch(now, limit=10, lease=timedelta(minutes=5))
    assert [e.id for e in claimed] == [legacy_id]


@pytest.mark.asyncio
async def test_b_claim_transitions_pending_to_delivering_and_bumps_attempts(
    db_session: AsyncSession,
) -> None:
    """Claim flips due `pending` rows to `delivering` and bumps `attempts`."""
    now = datetime.now(UTC)
    id1 = await _insert_event(db_session, tag="one", now=now)
    id2 = await _insert_event(db_session, tag="two", now=now)

    repo = SqlAlchemyOutboxEventRepository(db_session)
    claimed = await repo.claim_next_batch(now, limit=10, lease=timedelta(minutes=5))
    assert len(claimed) == 2
    claimed_ids = {e.id for e in claimed}
    assert claimed_ids == {id1, id2}
    for entity in claimed:
        assert entity.status == "delivering"
        assert entity.attempts == 1
        assert entity.next_retry_at == now + timedelta(minutes=5)
    assert (await _get_row(db_session, id1)).status == "delivering"
    assert (await _get_row(db_session, id2)).status == "delivering"
    assert (await _get_row(db_session, id1)).attempts == 1


@pytest.mark.asyncio
async def test_c_claim_excludes_future_available_after(
    db_session: AsyncSession,
) -> None:
    """Rows whose `available_after` is in the future are not claimable yet."""
    now = datetime.now(UTC)
    due = await _insert_event(db_session, tag="due", now=now)
    not_yet = await _insert_event(
        db_session,
        tag="future",
        now=now,
        available_after=now + timedelta(hours=1),
    )

    repo = SqlAlchemyOutboxEventRepository(db_session)
    claimed = await repo.claim_next_batch(now, limit=10)
    assert [e.id for e in claimed] == [due]
    assert (await _get_row(db_session, not_yet)).status == "pending"


@pytest.mark.asyncio
async def test_d_claim_excludes_future_retry_backoff_gate(
    db_session: AsyncSession,
) -> None:
    """A `pending` row in retry backoff (future `next_retry_at`) is excluded
    until its gate passes; an expired gate row is claimable."""
    now = datetime.now(UTC)
    gated = await _insert_event(
        db_session,
        tag="gated",
        now=now,
        next_retry_at=now + timedelta(hours=1),
    )
    expired = await _insert_event(
        db_session,
        tag="expired",
        now=now,
        next_retry_at=now - timedelta(minutes=1),
    )

    repo = SqlAlchemyOutboxEventRepository(db_session)
    claimed = await repo.claim_next_batch(now, limit=10)
    assert [e.id for e in claimed] == [expired]
    assert (await _get_row(db_session, gated)).status == "pending"


@pytest.mark.asyncio
async def test_e_claim_excludes_delivered_and_dead_letter(
    db_session: AsyncSession,
) -> None:
    """Settled rows (`delivered`/`dead_letter`) are never claimed again."""
    now = datetime.now(UTC)
    await _insert_event(db_session, tag="delivered", now=now, status="delivered")
    await _insert_event(db_session, tag="dead", now=now, status="dead_letter")

    repo = SqlAlchemyOutboxEventRepository(db_session)
    claimed = await repo.claim_next_batch(now, limit=10)
    assert claimed == []


@pytest.mark.asyncio
async def test_f_claim_orders_by_available_after_then_event_id(
    db_session: AsyncSession,
) -> None:
    """Oldest-first ordering: `available_after` ascending, ties broken by
    `event_id` (deterministic FIFO for a single worker + orderly takeover)."""
    now = datetime.now(UTC)
    earliest = await _insert_event(
        db_session,
        tag="earliest",
        now=now,
        available_after=now - timedelta(minutes=5),
    )
    low = uuid.UUID(int=0x11)
    high = uuid.UUID(int=0x22)
    tied_first = await _insert_event(
        db_session,
        tag="tied-a",
        now=now,
        event_id=low,
        available_after=now - timedelta(minutes=1),
    )
    tied_second = await _insert_event(
        db_session,
        tag="tied-b",
        now=now,
        event_id=high,
        available_after=now - timedelta(minutes=1),
    )

    repo = SqlAlchemyOutboxEventRepository(db_session)
    claimed = await repo.claim_next_batch(now, limit=10)
    assert [e.id for e in claimed] == [earliest, tied_first, tied_second]


@pytest.mark.asyncio
async def test_g_claim_without_lease_leaves_no_watermark(
    db_session: AsyncSession,
) -> None:
    """A claim without a lease sets `next_retry_at` to NULL — no recovery
    guarantee (no lease, so the row is never requeued)."""
    now = datetime.now(UTC)
    event_id = await _insert_event(db_session, tag="no-lease", now=now)

    repo = SqlAlchemyOutboxEventRepository(db_session)
    claimed = await repo.claim_next_batch(now, limit=10)
    assert [e.id for e in claimed] == [event_id]
    assert claimed[0].next_retry_at is None
    assert (await _get_row(db_session, event_id)).next_retry_at is None


@pytest.mark.asyncio
async def test_h_requeue_expired_revives_stale_delivering_rows(
    db_session: AsyncSession,
) -> None:
    """A `delivering` row whose lease watermark passed returns to `pending`
    with the watermark cleared; a live lease is untouched."""
    now = datetime.now(UTC)
    stale = await _insert_event(
        db_session,
        tag="stale",
        now=now,
        status="delivering",
        attempts=2,
        next_retry_at=now - timedelta(minutes=1),
    )
    live = await _insert_event(
        db_session,
        tag="live",
        now=now,
        status="delivering",
        attempts=1,
        next_retry_at=now + timedelta(minutes=5),
    )

    repo = SqlAlchemyOutboxEventRepository(db_session)
    requeued = await repo.requeue_expired(now)
    assert [e.id for e in requeued] == [stale]

    stale_row = await _get_row(db_session, stale)
    assert stale_row.status == "pending"
    assert stale_row.next_retry_at is None
    assert stale_row.attempts == 2

    live_row = await _get_row(db_session, live)
    assert live_row.status == "delivering"
    assert live_row.next_retry_at == now + timedelta(minutes=5)


@pytest.mark.asyncio
async def test_i_requeue_expired_ignores_non_delivering_rows(
    db_session: AsyncSession,
) -> None:
    """Only `delivering` rows are requeued — `pending` (retry backoff),
    `delivered`, and `dead_letter` rows are never touched."""
    now = datetime.now(UTC)
    await _insert_event(
        db_session,
        tag="pending",
        now=now,
        status="pending",
        next_retry_at=now - timedelta(hours=1),
    )
    await _insert_event(
        db_session,
        tag="delivered",
        now=now,
        status="delivered",
        next_retry_at=now - timedelta(hours=1),
    )
    await _insert_event(
        db_session,
        tag="dead",
        now=now,
        status="dead_letter",
        next_retry_at=now - timedelta(hours=1),
    )

    repo = SqlAlchemyOutboxEventRepository(db_session)
    assert await repo.requeue_expired(now) == []


@pytest.mark.asyncio
async def test_j_mark_delivered_settles_claimed_row(
    db_session: AsyncSession,
) -> None:
    """Marking a `delivering` row delivered records completion and clears
    the lease watermark."""
    now = datetime.now(UTC)
    event_id = await _insert_event(db_session, tag="ok", now=now)

    repo = SqlAlchemyOutboxEventRepository(db_session)
    await repo.claim_next_batch(now, limit=10, lease=timedelta(minutes=5))
    await repo.mark_delivered(event_id)

    row = await _get_row(db_session, event_id)
    assert row.status == "delivered"
    assert row.delivered_at is not None
    assert row.next_retry_at is None
    assert row.last_error is None
    assert row.attempts == 1


@pytest.mark.asyncio
async def test_k_mark_delivered_rejects_non_delivering_status(
    db_session: AsyncSession,
) -> None:
    """A lost race (row no longer `delivering`) raises `OutboxTransitionError`
    reporting the on-write status, and nothing is written."""
    now = datetime.now(UTC)
    unclaimed = await _insert_event(db_session, tag="pending", now=now)
    missing = uuid4()

    repo = SqlAlchemyOutboxEventRepository(db_session)
    with pytest.raises(OutboxTransitionError) as exc_info:
        await repo.mark_delivered(unclaimed)
    assert exc_info.value.event_id == unclaimed
    assert exc_info.value.current_status == "pending"
    assert exc_info.value.operation == "mark_delivered"
    assert "cannot be 'mark_delivered' from status 'pending'" in str(exc_info.value)
    assert (await _get_row(db_session, unclaimed)).status == "pending"

    with pytest.raises(OutboxTransitionError) as exc_info:
        await repo.mark_delivered(missing)
    assert exc_info.value.current_status == "missing"


@pytest.mark.asyncio
async def test_l_mark_failed_with_retry_requeues_to_pending_backoff(
    db_session: AsyncSession,
) -> None:
    """`mark_failed` with `retry_at` returns the row to `pending` gated by
    that value (retry backoff) and persists `last_error`."""
    now = datetime.now(UTC)
    event_id = await _insert_event(db_session, tag="retry", now=now)
    retry_at = now + timedelta(minutes=2)

    repo = SqlAlchemyOutboxEventRepository(db_session)
    await repo.claim_next_batch(now, limit=10, lease=timedelta(minutes=5))
    await repo.mark_failed(event_id, error="transient", retry_at=retry_at)

    row = await _get_row(db_session, event_id)
    assert row.status == "pending"
    assert row.last_error == "transient"
    assert row.next_retry_at == retry_at
    assert row.delivered_at is None
    assert row.attempts == 1


@pytest.mark.asyncio
async def test_m_mark_failed_without_retry_dead_letters(
    db_session: AsyncSession,
) -> None:
    """`mark_failed` with `retry_at=None` dead-letters the row (queue
    exhausted) and clears the watermark."""
    now = datetime.now(UTC)
    event_id = await _insert_event(db_session, tag="dead", now=now)

    repo = SqlAlchemyOutboxEventRepository(db_session)
    await repo.claim_next_batch(now, limit=10, lease=timedelta(minutes=5))
    await repo.mark_failed(event_id, error="exhausted", retry_at=None)

    row = await _get_row(db_session, event_id)
    assert row.status == "dead_letter"
    assert row.last_error == "exhausted"
    assert row.next_retry_at is None
    assert row.delivered_at is None


@pytest.mark.asyncio
async def test_n_mark_failed_rejects_non_delivering_status(
    db_session: AsyncSession,
) -> None:
    """A lost race (row no longer `delivering`) raises `OutboxTransitionError`
    and nothing is written."""
    now = datetime.now(UTC)
    unclaimed = await _insert_event(db_session, tag="pending", now=now)
    missing = uuid4()

    repo = SqlAlchemyOutboxEventRepository(db_session)
    with pytest.raises(OutboxTransitionError) as exc_info:
        await repo.mark_failed(unclaimed, error="boom", retry_at=None)
    assert exc_info.value.current_status == "pending"
    assert exc_info.value.operation == "mark_failed"
    assert (await _get_row(db_session, unclaimed)).last_error is None

    with pytest.raises(OutboxTransitionError) as exc_info:
        await repo.mark_failed(missing, error="boom", retry_at=None)
    assert exc_info.value.current_status == "missing"


@pytest.mark.asyncio
async def test_o_claim_is_flush_only_and_dies_with_transaction(
    db_session: AsyncSession,
) -> None:
    """A claim is flushed in-session (visible to the claimer) but NOT durable:
    a second connection still sees `pending`, and a rollback leaves the row
    pending for the next claimer."""
    now = datetime.now(UTC)
    event_id = await _insert_event(db_session, tag="txn", now=now)
    await db_session.commit()

    repo = SqlAlchemyOutboxEventRepository(db_session)
    claimed = await repo.claim_next_batch(now, limit=10, lease=timedelta(minutes=5))
    assert [e.id for e in claimed] == [event_id]
    assert (await _get_row(db_session, event_id)).status == "delivering"

    engine = create_async_engine(str(get_settings().DATABASE_URL))
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                select(EventOutboxModel.status).where(EventOutboxModel.event_id == event_id)
            )
            assert result.scalar_one() == "pending"
    finally:
        await engine.dispose()

    await db_session.rollback()

    row = await _get_row(db_session, event_id)
    assert row.status == "pending"
    assert row.attempts == 0
    assert row.next_retry_at is None


@pytest.mark.asyncio
async def test_p_claim_honors_limit(db_session: AsyncSession) -> None:
    """`limit` bounds the batch; the surplus rows stay `pending`."""
    now = datetime.now(UTC)
    ids = [await _insert_event(db_session, tag=f"n{i}", now=now) for i in range(3)]

    repo = SqlAlchemyOutboxEventRepository(db_session)
    claimed = await repo.claim_next_batch(now, limit=2)
    assert len(claimed) == 2

    remaining_id = (set(ids) - {e.id for e in claimed}).pop()
    assert (await _get_row(db_session, remaining_id)).status == "pending"


@pytest.mark.asyncio
async def test_q_concurrent_claim_skips_locked_rows(
    db_session: AsyncSession,
) -> None:
    """Two claimants never double-claim: the second worker, blocked by
    `FOR UPDATE SKIP LOCKED` on the first's uncommitted batch, takes only
    the rows the first did not touch."""
    now = datetime.now(UTC)
    await _insert_event(db_session, tag="c0", now=now)
    await _insert_event(db_session, tag="c1", now=now)
    await _insert_event(db_session, tag="c2", now=now)
    await db_session.commit()

    repo = SqlAlchemyOutboxEventRepository(db_session)
    claimed1 = await repo.claim_next_batch(now, limit=2, lease=timedelta(minutes=5))
    assert len(claimed1) == 2
    claimed1_ids = {e.id for e in claimed1}

    engine = create_async_engine(str(get_settings().DATABASE_URL))
    try:
        async with (
            engine.connect() as conn,
            AsyncSession(bind=conn, expire_on_commit=False) as session2,
        ):
            repo2 = SqlAlchemyOutboxEventRepository(session2)
            claimed2 = await repo2.claim_next_batch(now, limit=10, lease=timedelta(minutes=5))
    finally:
        await engine.dispose()

    claimed2_ids = {e.id for e in claimed2}
    assert len(claimed2) == 1
    assert claimed1_ids.isdisjoint(claimed2_ids)


@pytest.mark.asyncio
async def test_r_claim_returns_fully_mapped_entities(
    db_session: AsyncSession,
) -> None:
    """Returned entities preserve the row: identity, payload (JSONB
    round-trip), refs, provenance, and delivery fields."""
    now = datetime.now(UTC)
    org, proj, run = uuid4(), uuid4(), uuid4()
    event_id = await _insert_event(
        db_session,
        tag="mapped",
        now=now,
        organization_id=org,
        project_id=proj,
        autonomous_run_id=run,
    )

    repo = SqlAlchemyOutboxEventRepository(db_session)
    claimed: list[OutboxEvent] = await repo.claim_next_batch(
        now, limit=10, lease=timedelta(minutes=5)
    )
    assert len(claimed) == 1
    entity = claimed[0]
    assert entity.id == event_id
    assert entity.event_type == OutboxEventType.CAMPAIGN_RUN_STARTED.value
    assert entity.schema_version == 1
    assert entity.occurred_at == now
    assert entity.payload == {"tag": "mapped"}
    assert entity.organization_id == org
    assert entity.project_id == proj
    assert entity.schedule_id is None
    assert entity.autonomous_run_id == run
    assert entity.scan_id is None
    assert entity.specversion == "1.0"
    assert entity.status == "delivering"
    assert entity.attempts == 1
    assert entity.max_attempts == 10
    assert entity.last_error is None
    assert entity.next_retry_at == now + timedelta(minutes=5)
    assert entity.delivered_at is None
