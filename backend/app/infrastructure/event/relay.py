"""Outbox relay (M7.5 Phase 4-B2).

Runs the §13 claim loop for the durable event outbox: requeue stale
leases, claim the next due batch and COMMIT the claim, deliver each
event OUTSIDE any transaction (dry-run by default in Phase 4-B2), then
settle every event — ``mark_delivered`` on success, ``mark_failed`` with
``retry_at=None`` (the Phase 4-B2 terminal dead-letter; the §20
backoff/DLQ/metrics machinery is Phase 4-B3) on delivery error — inside
ONE final transaction.

Deliberately free of any framework dependency: Celery Beat calls
``run_outbox_relay`` (via the gated ``specter.outbox_relay`` task,
§13-e) and injects the session factory, so the loop stays unit-testable
and reusable by a non-Celery supervisor.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities import OutboxEvent
from app.domain.exceptions import OutboxTransitionError
from app.infrastructure.db.repositories.event_outbox_repository import (
    SqlAlchemyOutboxEventRepository,
)

logger = structlog.get_logger(__name__)

DEFAULT_LEASE = timedelta(minutes=5)

# A transport for one event. Phase 4-B4 wires real HTTP/webhook delivery
# behind this signature; until then the dry-run default is the delivery.
DeliveryFn = Callable[[OutboxEvent], Awaitable[None]]

# How the relay opens the short-lived transactions of the §13 loop.
SessionFactory = Callable[[], AsyncSession]


@dataclass
class OutboxRelayResult:
    """Summary of one relay pass (Phase 4-B2).

    ``claimed`` is the batch size from `claim_next_batch`;
    ``delivered``/``dead_lettered`` are the settled outcomes of that
    claimed batch; ``lost_race`` counts claimed rows that were settled by
    someone else first (a requeue + re-claim elsewhere) and are therefore
    left untouched. The requeue count is surfaced via structured logs,
    not this struct, so the result stays the worker-visible outcome.
    """

    claimed: int = 0
    delivered: int = 0
    dead_lettered: int = 0
    lost_race: int = 0


async def _dry_run_deliver(event: OutboxEvent) -> None:
    """Phase 4-B2 placeholder delivery: log a structured trace, persist nothing.

    Deliberately never raises, so every claimed event reaches a terminal
    settlement in this phase.
    """
    logger.info(
        "outbox_relay_dry_run_delivered",
        event_id=str(event.id),
        event_type=event.event_type,
        schema_version=event.schema_version,
        occurred_at=event.occurred_at.isoformat(),
    )


async def run_outbox_relay(
    *,
    session_factory: SessionFactory,
    now: datetime | None = None,
    limit: int = 50,
    lease: timedelta = DEFAULT_LEASE,
    deliver: DeliveryFn = _dry_run_deliver,
) -> OutboxRelayResult:
    """Run one pass of the §13 relay loop.

    Transaction flow (three transactions, per §13 / §20 — an external
    delivery is never performed while holding a row lock):

      1. Require expired leases back to ``pending``, then claim the next
         due batch (``FOR UPDATE SKIP LOCKED``) and COMMIT. A crash here
         still leaves the batch claimed only until its lease expires, and
         anything before the claim commit simply stays ``pending``.
      2. Deliver every claimed event OUTSIDE any transaction, so a slow
         or hanging transport never blocks the claim commit (or another
         worker's ``requeue_expired``). A raising delivery dead-letters
         that single event; it never aborts the pass.
      3. Settle every event in ONE final transaction: ``mark_delivered``
         on success, ``mark_failed(retry_at=None)`` on delivery error.
         A lost claim race raises `OutboxTransitionError` for that row
         (requeued and re-claimed elsewhere) — it is skipped and the
         other worker owns it.
    """
    resolved_now = now or datetime.now(UTC)

    # --- Transaction 1: requeue + claim (fires the lease). ---
    async with session_factory() as session:
        repository = SqlAlchemyOutboxEventRepository(session)
        requeued = await repository.requeue_expired(resolved_now)
        if requeued:
            logger.info(
                "outbox_relay_requeued",
                count=len(requeued),
                lease_seconds=lease.total_seconds(),
            )
        events = await repository.claim_next_batch(
            resolved_now,
            limit=limit,
            lease=lease,
        )
        await session.commit()

    result = OutboxRelayResult(claimed=len(events))
    if not events:
        return result

    # --- Transaction-free delivery pass. ---
    delivery_errors: dict[UUID, str] = {}
    for event in events:
        try:
            await deliver(event)
        except Exception as exc:  # noqa: BLE001 - one bad event never blocks the pass
            delivery_errors[event.id] = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "outbox_relay_delivery_failed",
                event_id=str(event.id),
                event_type=event.event_type,
                error=delivery_errors[event.id],
            )

    # --- Transaction 2: settle the whole claimed batch at once. ---
    async with session_factory() as session:
        repository = SqlAlchemyOutboxEventRepository(session)
        for event in events:
            try:
                error = delivery_errors.get(event.id)
                if error is None:
                    await repository.mark_delivered(event.id)
                    result.delivered += 1
                else:
                    # Phase 4-B2: no retry/backoff policy yet — the event is
                    # dead-lettered terminally (Phase 4-B3 adds it).
                    await repository.mark_failed(event.id, error=error, retry_at=None)
                    result.dead_lettered += 1
            except OutboxTransitionError:
                # Requeued and re-claimed elsewhere while we were delivering;
                # that worker owns it now. Leave the row untouched.
                result.lost_race += 1
        await session.commit()

    logger.info(
        "outbox_relay_completed",
        claimed=result.claimed,
        delivered=result.delivered,
        dead_lettered=result.dead_lettered,
        lost_race=result.lost_race,
    )
    return result