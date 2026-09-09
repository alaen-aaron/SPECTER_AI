"""SQLAlchemy 2.0 ORM model: Scan (Milestone 3)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.domain.value_objects import ScanStatus
from app.infrastructure.db.session import Base


class ScanModel(Base):
    __tablename__ = "scans"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    initiated_by: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    plugin: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[ScanStatus] = mapped_column(
        String(20), nullable=False, default=ScanStatus.QUEUED
    )
    target_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    plugin_config: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)

    logs_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifacts_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # M7.4 Phase 4: failure classification (transport | tool | domain),
    # set by the ExecutionEngine when the scan fails. Null for scans that
    # never failed. Drives the autonomous retry policy.
    failure_kind: Mapped[str | None] = mapped_column(String(30), nullable=True)

    __table_args__ = (
        Index("idx_scans_project", "project_id"),
        Index("idx_scans_status", "status"),
    )
