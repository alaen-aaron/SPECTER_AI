"""
SQLAlchemy 2.0 ORM models: Organization, OrganizationMember,
OrganizationInvitation (SRS §5.2, plus the Milestone-2 invitations
schema-only addition).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.domain.value_objects import InvitationStatus, OrganizationRole
from app.infrastructure.db.session import Base


class OrganizationModel(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True)

    members: Mapped[list[OrganizationMemberModel]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )
    invitations: Mapped[list[OrganizationInvitationModel]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )


class OrganizationMemberModel(Base):
    __tablename__ = "organization_members"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[OrganizationRole] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    organization: Mapped[OrganizationModel] = relationship(back_populates="members")

    __table_args__ = (Index("idx_org_members_user", "user_id"),)


class OrganizationInvitationModel(Base):
    """Schema only per Milestone 2 scope — no email delivery or accept endpoint yet."""

    __tablename__ = "organization_invitations"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(CITEXT, nullable=False)
    role: Mapped[OrganizationRole] = mapped_column(String(50), nullable=False)
    invited_by: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    status: Mapped[InvitationStatus] = mapped_column(
        String(30), nullable=False, default=InvitationStatus.PENDING
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)

    organization: Mapped[OrganizationModel] = relationship(back_populates="invitations")

    __table_args__ = (
        UniqueConstraint("organization_id", "email", "status", name="uq_pending_invite_per_email"),
        Index("idx_org_invitations_org", "organization_id"),
    )
