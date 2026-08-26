"""SQLAlchemy 2.0 ORM model: AssetObservation (M7.3 Phase 2).

Append-only provenance record answering "why does SPECTER believe this
asset exists?": one row per (ToolResult, Asset) pairing, created
idempotently by the asset upsert path. Structured metadata only — never
raw stdout, never secrets.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.infrastructure.db.session import Base


class AssetObservationModel(Base):
    __tablename__ = "asset_observations"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    tool_result_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tool_results.id", ondelete="CASCADE"),
        nullable=False,
    )
    scan_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("scans.id", ondelete="CASCADE"), nullable=False
    )
    plugin: Mapped[str] = mapped_column(String(100), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
    details: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict
    )

    __table_args__ = (
        # Idempotency: reprocessing the same ToolResult against the same
        # asset must never produce a second observation.
        UniqueConstraint(
            "tool_result_id",
            "asset_id",
            name="uq_asset_observation_tool_result_asset",
        ),
        Index("idx_asset_observations_asset_time", "asset_id", "observed_at"),
        Index("idx_asset_observations_tool_result", "tool_result_id"),
        Index("idx_asset_observations_project", "project_id"),
    )
