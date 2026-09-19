"""SQLAlchemy 2.0 ORM model: durable event outbox (M7.5 Phase 4-A).

Append-only observation substrate: every row is a versioned lifecycle
event inserted inside the transaction that made the state change it
describes. Phase 4-A writes only — delivery status columns belong to a
later phase and are intentionally absent so this subscription never
leaks into the execution path.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.infrastructure.db.session import Base


class EventOutboxModel(Base):
    """One durable lifecycle event (campaign.run.*, M7.5 Phase 4-A).

    Observation decoupling (deliberate deviation from the full-Phase-4-B
    draft in `MILESTONE_3_ARCHITECTURE_M75_PHASE4.md` §8.1, which carries
    delivery columns): entity refs are plain indexed UUID columns, NOT
    foreign keys. An append-only observation trail must never block or be
    blocked by the lifecycle of its subjects — a project/run may be
    deleted independently and the event history survives untouched, and
    the writer already guarantees reference integrity by always deriving
    `organization_id` from a live project row. A later phase adds
    delivery columns, never referential coupling.
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

    __table_args__ = (
        Index("idx_event_outbox_org_time", "organization_id", "created_at"),
        Index("idx_event_outbox_project", "project_id"),
        Index("idx_event_outbox_run", "autonomous_run_id"),
        Index("idx_event_outbox_type", "event_type"),
    )