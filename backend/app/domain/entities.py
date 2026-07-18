"""
Domain entities.

Plain dataclasses with zero framework imports (no SQLAlchemy, no
Pydantic). These represent the business objects use-case services in
`application/` operate on; `infrastructure/db/models/` maps these to
actual ORM rows, but the two are intentionally decoupled — a domain
entity never knows it might be persisted in Postgres.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from uuid import UUID

from app.domain.value_objects import (
    AuthorizationStatus,
    InvitationStatus,
    OrganizationRole,
    ProjectRole,
    ProjectState,
    TargetType,
)


@dataclass(slots=True)
class User:
    id: UUID
    email: str
    password_hash: str
    full_name: str | None
    is_active: bool
    created_at: datetime


@dataclass(slots=True)
class Organization:
    id: UUID
    name: str
    created_at: datetime
    deleted_at: datetime | None = None

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


@dataclass(slots=True)
class OrganizationMember:
    organization_id: UUID
    user_id: UUID
    role: OrganizationRole
    created_at: datetime


@dataclass(slots=True)
class OrganizationInvitation:
    """Schema-only per Milestone 2 scope — no email delivery, no accept flow yet."""

    id: UUID
    organization_id: UUID
    email: str
    role: OrganizationRole
    invited_by: UUID
    status: InvitationStatus
    created_at: datetime
    expires_at: datetime


@dataclass(slots=True)
class Project:
    id: UUID
    organization_id: UUID
    name: str
    description: str | None
    state: ProjectState
    tags: list[str]
    client_metadata: dict[str, str]
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def can_transition_to(self, new_state: ProjectState) -> bool:
        from app.domain.value_objects import VALID_PROJECT_TRANSITIONS

        return new_state in VALID_PROJECT_TRANSITIONS.get(self.state, frozenset())


@dataclass(slots=True)
class ProjectMember:
    project_id: UUID
    user_id: UUID
    role: ProjectRole
    created_at: datetime


@dataclass(slots=True)
class Target:
    id: UUID
    project_id: UUID
    value: str
    target_type: TargetType
    in_scope: bool
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


@dataclass(slots=True)
class AuthorizationRecord:
    id: UUID
    project_id: UUID
    client_name: str
    document_reference: str
    authorized_from: date
    authorized_to: date
    allowed_targets: list[str]
    approved_by: UUID
    status: AuthorizationStatus
    scope_notes: str | None
    evidence_pointer: str | None
    created_at: datetime

    def is_active_on(self, on_date: date) -> bool:
        """
        Whether this record grants authorization on the given date.

        This is the core temporal check behind Scope Guard (SRS §16.3):
        an authorization record outside its date range or explicitly
        revoked/expired never grants scan permission, regardless of its
        `status` field being stale.
        """
        if self.status != AuthorizationStatus.ACTIVE:
            return False
        return self.authorized_from <= on_date <= self.authorized_to


@dataclass(slots=True)
class Session:
    """A refresh-token session (SRS §16.1)."""

    id: UUID
    user_id: UUID
    refresh_token_hash: str
    user_agent: str | None
    ip_address: str | None
    expires_at: datetime
    created_at: datetime
    revoked_at: datetime | None = None

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    def is_valid_at(self, moment: datetime) -> bool:
        return not self.is_revoked and moment < self.expires_at


@dataclass(slots=True)
class AuditLogEntry:
    """SRS §16.5 — immutable, append-only."""

    id: UUID
    organization_id: UUID | None
    actor_id: UUID | None
    action: str
    target_type: str | None
    target_id: UUID | None
    ip_address: str | None
    created_at: datetime
    before_state: dict[str, object] = field(default_factory=dict)
    after_state: dict[str, object] = field(default_factory=dict)
