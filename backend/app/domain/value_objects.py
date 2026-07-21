"""
Domain value objects and enumerations.

Pure Python, zero framework imports — these are shared vocabulary used
by entities, repository interfaces, and application services alike.
"""

from __future__ import annotations

from enum import Enum


class OrganizationRole(str, Enum):
    """
    Role scoped to a single Organization (SRS §5.2 `organization_members`).

    Deliberately a smaller vocabulary than ProjectRole — organization
    membership is about tenancy administration, not engagement work.
    """

    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class ProjectRole(str, Enum):
    """Role scoped to a single Project (SRS §2.1, FR-1.4)."""

    OWNER = "owner"
    ADMIN = "admin"
    LEAD_TESTER = "lead_tester"
    TESTER = "tester"
    READ_ONLY = "read_only"
    CLIENT_VIEWER = "client_viewer"


# Project roles allowed to perform destructive/administrative actions,
# used by permission dependencies as a convenience grouping.
PROJECT_ADMIN_ROLES = frozenset({ProjectRole.OWNER, ProjectRole.ADMIN})
ORGANIZATION_ADMIN_ROLES = frozenset({OrganizationRole.OWNER, OrganizationRole.ADMIN})


class ProjectState(str, Enum):
    """Project lifecycle state machine (SRS §2.2, FR-2.2)."""

    DRAFT = "draft"
    AUTHORIZED = "authorized"
    ACTIVE = "active"
    REPORTING = "reporting"
    CLOSED = "closed"
    ARCHIVED = "archived"


# Valid forward transitions. The workflow engine (and the API layer)
# must reject any transition not present here — this is the state
# machine's single source of truth (SRS FR-2.2/FR-2.3).
VALID_PROJECT_TRANSITIONS: dict[ProjectState, frozenset[ProjectState]] = {
    ProjectState.DRAFT: frozenset({ProjectState.AUTHORIZED}),
    ProjectState.AUTHORIZED: frozenset({ProjectState.ACTIVE, ProjectState.DRAFT}),
    ProjectState.ACTIVE: frozenset({ProjectState.REPORTING}),
    ProjectState.REPORTING: frozenset({ProjectState.CLOSED, ProjectState.ACTIVE}),
    ProjectState.CLOSED: frozenset({ProjectState.ARCHIVED}),
    ProjectState.ARCHIVED: frozenset(),
}


class TargetType(str, Enum):
    """SRS §2.3, FR-3.1."""

    IP = "ip"
    CIDR = "cidr"
    DOMAIN = "domain"
    URL = "url"


class AuthorizationStatus(str, Enum):
    """Status of an AuthorizationRecord (Milestone 2 addition, per SRS §16.3)."""

    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"


class InvitationStatus(str, Enum):
    """Status of an OrganizationInvitation (schema-only per Milestone 2 scope)."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    REVOKED = "revoked"
    EXPIRED = "expired"


class ScanStatus(str, Enum):
    """Scan lifecycle state (Milestone 3, SRS §2.6/§13)."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Terminal states a scan can never leave, and states from which
# cancellation is still meaningful. Used by ScanService to reject
# invalid transitions the same way VALID_PROJECT_TRANSITIONS does for
# projects — one source of truth, not scattered if/else checks.
SCAN_TERMINAL_STATUSES = frozenset({ScanStatus.COMPLETED, ScanStatus.FAILED, ScanStatus.CANCELLED})
SCAN_CANCELLABLE_STATUSES = frozenset({ScanStatus.QUEUED, ScanStatus.RUNNING})


class AssetType(str, Enum):
    """Asset classification (SRS §2.3 FR-3.2)."""

    HOST = "host"
    SUBDOMAIN = "subdomain"
    SERVICE = "service"
    TECHNOLOGY = "technology"
    CREDENTIAL = "credential"


class Severity(str, Enum):
    """Finding severity (SRS §5.2 findings table)."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FindingStatus(str, Enum):
    """Finding lifecycle state."""

    OPEN = "open"
    CONFIRMED = "confirmed"
    FALSE_POSITIVE = "false_positive"
    REMEDIATED = "remediated"
