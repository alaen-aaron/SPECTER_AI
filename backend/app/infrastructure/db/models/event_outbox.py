"""SQLAlchemy 2.0 ORM model: durable event outbox (M7.5 Phase 4-A, 4-B1).

Append-only observation substrate: every row is a versioned lifecycle
event inserted inside the transaction that made the state change it
describes. Phase 4-A writes events only. Phase 4-B1 adds the claim/lease
machinery (status, attempt accounting, backoff/lease watermarks) as
server-defaulted columns so 4-A rows stay claimable without producer
changes. No consumer, dispatcher, or HTTP delivery exists yet — nothing
in the runtime writes delivery transitions in this phase.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.infrastructure.db.session import Base


class EventOutboxModel(Base):
    """One durable lifecycle event (campaign.run.*, M7.5 Phase 4-A, 4-B1).

    Observation decoupling (deliberate deviation from the full-Phase-4-B
    draft in `MILESTONE_3_ARCHITECTURE_M75_PHASE4.md` §8.1): entity refs
    are plain indexed UUID columns, NOT foreign keys. An append-only
    observation trail must never block or be blocked by the lifecycle of
    its subjects — a project/run may be deleted independently and the
    event history survives untouched, and the writer already guarantees
    reference integrity by always deriving `organization_id` from a live
    project row.

    Delivery columns (4-B1):

    - `status` one of `pending` / `delivering` / `delivered` /
      `dead_letter`; server-defaulted to `pending` so 4-A written rows
      are immediately claimable.
    - `available_after` is the earliest claim time (default `now()` — a
      row is claimable the moment its transaction commits).
    - `next_retry_at` doubles as the lease watermark: claimers set it to
      `now() + lease`; `requeue_expired` revives `delivering` rows whose
      watermark passed, `pending` rows treat it as a retry backoff gate,
      and `delivered/dead_letter` rows keep it `NULL`.
    - `attempts` / `max_attempts` are claim accounting (each claim bumps
      `attempts`; the consumer later dead-letters at `max_attempts`).
    - `last_error` preserves the most recent failure for diagnostics;
      `delivered_at` records completion and stays `NULL` unless the event
      was actually delivered.
    - `scan_id`/`specversion` are optional provenance metadata; `scan_id`
      is a plain indexed UUID, never a foreign key.
    """

    __tablename__ = "event_outbox"

    event_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    autonomous_run_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    occurred_at: Mapped[datetime] = mapped_column(nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    scan_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    specversion: Mapped[str] = mapped_column(
        String(5), nullable=False, default="1.0", server_default="1.0"
    )
    available_after: Mapped[datetime] = mapped_column(
        server_default=func.now(), default=func.now, nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=10, server_default="10"
    )
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(nullable=True)

    __table_args__ = (
        Index("idx_event_outbox_org_time", "organization_id", "created_at"),
        Index("idx_event_outbox_project", "project_id"),
        Index("idx_event_outbox_run", "autonomous_run_id"),
        Index("idx_event_outbox_type", "event_type"),
        Index(
            "idx_event_outbox_delivery",
            "status",
            "available_after",
            "next_retry_at",
            postgresql_where=text("status IN ('pending', 'delivering')"),
        ),
    )