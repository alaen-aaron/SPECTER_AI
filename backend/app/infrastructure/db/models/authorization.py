"""
SQLAlchemy 2.0 ORM model: AuthorizationRecord (SRS §5.2
`authorization_records`, extended per Milestone 2 scope with `client`,
`status`, `scope_notes`, and `evidence` fields called out in the
Milestone 2 requirements).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.domain.value_objects import AuthorizationStatus
from app.infrastructure.db.session import Base


class AuthorizationRecordModel(Base):
    __tablename__ = "authorization_records"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    client_name: Mapped[str] = mapped_column(String(255), nullable=False)
    document_reference: Mapped[str] = mapped_column(Text, nullable=False)
    authorized_from: Mapped[date] = mapped_column(Date, nullable=False)
    authorized_to: Mapped[date] = mapped_column(Date, nullable=False)
    allowed_targets: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    approved_by: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    status: Mapped[AuthorizationStatus] = mapped_column(
        String(20), nullable=False, default=AuthorizationStatus.ACTIVE
    )
    scope_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_pointer: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    __table_args__ = (Index("idx_authorization_records_project", "project_id"),)
