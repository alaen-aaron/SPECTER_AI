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


class EvidenceType(str, Enum):
    """Evidence classification (SRS §2.9)."""

    SCREENSHOT = "screenshot"
    RAW_LOG = "raw_log"
    SESSION_RECORDING = "session_recording"
    REQUEST_RESPONSE = "request_response"


class ReportStatus(str, Enum):
    """Report lifecycle (SRS §2.11 FR-11.2)."""

    DRAFT = "draft"
    FINAL = "final"


class GraphNodeType(str, Enum):
    """Knowledge Graph node types (SRS §15A.2)."""

    ASSET = "asset"
    FINDING = "finding"
    CREDENTIAL = "credential"
    TECHNOLOGY = "technology"
    EVIDENCE = "evidence"


class GraphEdgeType(str, Enum):
    """Knowledge Graph edge types (SRS §15A.2)."""

    HOSTS = "hosts"
    RUNS = "runs"
    EXPOSES = "exposes"
    VULNERABLE_TO = "vulnerable_to"
    AUTHENTICATES_AS = "authenticates_as"
    DERIVED_FROM = "derived_from"
    COMMUNICATES_WITH = "communicates_with"
    EVIDENCED_BY = "evidenced_by"
    # M7.3 Phase 3: evidence-backed technology usage, created only from
    # real HTTPX/WhatWeb observations on a resolved service.
    USES = "uses"


class WorkflowStatus(str, Enum):
    """Workflow lifecycle state."""

    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


WORKFLOW_TERMINAL_STATUSES = frozenset({WorkflowStatus.ARCHIVED})


class WorkflowStepType(str, Enum):
    """Type of action a workflow step performs."""

    SCAN = "scan"
    CORRELATE = "correlate"


class ConditionOperator(str, Enum):
    """Operators for conditional step execution."""

    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    EXISTS = "exists"
    GREATER_THAN = "greater_than"
    LESS_THAN = "less_than"


class ScheduleFrequency(str, Enum):
    """How often a scheduled workflow runs."""

    ONCE = "once"
    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"


class ScheduleStatus(str, Enum):
    """Schedule lifecycle state."""

    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


# --- AI Decision Engine (Phase 4, SRS §8) -----------------------------------


class PlannedActionStatus(str, Enum):
    """Lifecycle state for a planner-suggested action (SRS §8.4)."""

    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"
    EXPIRED = "expired"


class AIOutputReviewStatus(str, Enum):
    """Human-review state for any AI-generated content (SRS FR-7.6)."""

    AI_DRAFTED = "ai_drafted"
    HUMAN_REVIEWED = "human_reviewed"
    HUMAN_APPROVED = "human_approved"


class RiskScoreSource(str, Enum):
    """Whether a risk score is purely computed or has AI rationale layered on top."""

    COMPUTED = "computed"
    AI_RATIONALE = "ai_rationale"


# --- Autonomous Orchestration (M7.4) ----------------------------------------


class AutonomousRunStatus(str, Enum):
    """Lifecycle state for an autonomous scan run (M7.4)."""

    CREATED = "created"
    PLANNING = "planning"
    AWAITING_APPROVAL = "awaiting_approval"
    EXECUTING = "executing"
    OBSERVING = "observing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


# Valid forward transitions — single source of truth for the state machine.
VALID_AUTONOMOUS_TRANSITIONS: dict[
    AutonomousRunStatus, frozenset[AutonomousRunStatus]
] = {
    AutonomousRunStatus.CREATED: frozenset(
        {AutonomousRunStatus.PLANNING, AutonomousRunStatus.FAILED, AutonomousRunStatus.CANCELLED}
    ),
    AutonomousRunStatus.PLANNING: frozenset(
        {
            AutonomousRunStatus.AWAITING_APPROVAL,
            # M7.4 Phase 2 (additive): PLANNING may advance directly to
            # EXECUTING (all decisions were auto-approved by policy for
            # the bounded cycle) or COMPLETED (planner exhausted / budget
            # spent with nothing left to execute).
            AutonomousRunStatus.EXECUTING,
            AutonomousRunStatus.COMPLETED,
            AutonomousRunStatus.FAILED,
            AutonomousRunStatus.CANCELLED,
        }
    ),
    AutonomousRunStatus.AWAITING_APPROVAL: frozenset(
        {AutonomousRunStatus.EXECUTING, AutonomousRunStatus.CANCELLED}
    ),
    AutonomousRunStatus.EXECUTING: frozenset(
        {
            AutonomousRunStatus.OBSERVING,
            AutonomousRunStatus.COMPLETED,
            AutonomousRunStatus.FAILED,
            AutonomousRunStatus.CANCELLED,
        }
    ),
    AutonomousRunStatus.OBSERVING: frozenset(
        {
            AutonomousRunStatus.PLANNING,
            AutonomousRunStatus.COMPLETED,
            AutonomousRunStatus.FAILED,
            AutonomousRunStatus.CANCELLED,
        }
    ),
    AutonomousRunStatus.COMPLETED: frozenset(),
    AutonomousRunStatus.CANCELLED: frozenset(),
    AutonomousRunStatus.FAILED: frozenset(),
}

AUTONOMOUS_TERMINAL_STATUSES = frozenset({
    AutonomousRunStatus.COMPLETED,
    AutonomousRunStatus.CANCELLED,
    AutonomousRunStatus.FAILED,
})


class ActionCategory(str, Enum):
    """Action risk classification for the autonomous approval policy (M7.4).

    Semantics follow the M7.4 task spec:
    - CATEGORY_0: blocked — never executed autonomously (nor approved).
    - CATEGORY_1: requires explicit human approval; the autonomous run
      pauses and the action awaits a manual decision via the existing API.
    - CATEGORY_2: eligible for controlled autonomous execution under a
      bounded cycle — approved by policy (approval_mode=AUTO_POLICY),
      attributed to the initiating user, never fabricated human approval.
    """

    CATEGORY_0 = "category_0"  # Blocked — never autonomously executed
    CATEGORY_1 = "category_1"  # Human approval required
    CATEGORY_2 = "category_2"  # Eligible for controlled auto-execution


class ApprovalMode(str, Enum):
    """How an autonomous action's approval was obtained (M7.4 Phase 2).

    The audit trail must never fabricate manual human approval for a
    policy-driven decision — the mode distinguishes the two explicitly.
    """

    MANUAL = "manual"  # Explicit human approval via the API
    AUTO_POLICY = "auto_policy"  # Granted by bounded-run policy, attributed to initiator
