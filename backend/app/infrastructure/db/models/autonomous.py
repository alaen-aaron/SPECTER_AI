"""SQLAlchemy 2.0 ORM models: Autonomous Orchestration (M7.4)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.domain.value_objects import AutonomousRunStatus
from app.infrastructure.db.session import Base


class AutonomousRunModel(Base):
    __tablename__ = "autonomous_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    initiated_by: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    status: Mapped[AutonomousRunStatus] = mapped_column(
        String(30), nullable=False, default=AutonomousRunStatus.CREATED
    )
    objective: Mapped[str] = mapped_column(Text, nullable=False, default="")
    max_actions: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    max_runtime_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=1800)
    current_cycle: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    actions_completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    approval_policy: Mapped[str] = mapped_column(String(50), nullable=False, default="policy_based")
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_summary: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("idx_autonomous_runs_project", "project_id"),
        Index("idx_autonomous_runs_status", "status"),
        Index("idx_autonomous_runs_active_project", "project_id", "status"),
    )


class AutonomousRunActionModel(Base):
    __tablename__ = "autonomous_run_actions"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("autonomous_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    cycle: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    action_type: Mapped[str] = mapped_column(String(100), nullable=False)
    plugin: Mapped[str | None] = mapped_column(String(100), nullable=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    justification: Mapped[str] = mapped_column(Text, nullable=False, default="")
    plugin_config: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    target_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    category: Mapped[str] = mapped_column(String(20), nullable=False, default="category_0")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="proposed")
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(nullable=True)
    approval_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    planned_action_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("planned_actions.id"), nullable=True
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    scan_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("scans.id"), nullable=True
    )
    result_summary: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("idx_autonomous_actions_run", "run_id"),
        Index("idx_autonomous_actions_project", "project_id"),
        Index("idx_autonomous_actions_status", "status"),
    )
