"""SQLAlchemy 2.0 ORM model: Finding (Milestone 4C)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Column, ForeignKey, Index, Numeric, String, Table, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.infrastructure.db.session import Base

# Association table: which tool_results contributed to a finding.
finding_tool_results = Table(
    "finding_tool_results",
    Base.metadata,
    Column(
        "finding_id",
        PG_UUID(as_uuid=True),
        ForeignKey("findings.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "tool_result_id",
        PG_UUID(as_uuid=True),
        ForeignKey("tool_results.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class FindingModel(Base):
    __tablename__ = "findings"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("assets.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="open")
    cvss_score: Mapped[float | None] = mapped_column(Numeric(3, 1), nullable=True)
    dedup_key: Mapped[str] = mapped_column(Text, nullable=False, default="")

    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("idx_findings_project_severity", "project_id", "severity"),
        Index("idx_findings_dedup", "project_id", "dedup_key", unique=True),
    )
