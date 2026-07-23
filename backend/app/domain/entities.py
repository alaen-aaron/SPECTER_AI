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
    SCAN_CANCELLABLE_STATUSES,
    SCAN_TERMINAL_STATUSES,
    VALID_PROJECT_TRANSITIONS,
    AssetType,
    AuthorizationStatus,
    EvidenceType,
    FindingStatus,
    GraphEdgeType,
    GraphNodeType,
    InvitationStatus,
    OrganizationRole,
    ProjectRole,
    ProjectState,
    ReportStatus,
    ScanStatus,
    ScheduleFrequency,
    Severity,
    TargetType,
    WorkflowStatus,
    WorkflowStepType,
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


@dataclass(slots=True)
class Scan:
    """
    A single scan execution (Milestone 3). One Scan = one plugin
    invocation against one or more targets within one project.

    `target_ids` and `plugin_config` aren't in the milestone spec's
    minimal field list but are required to actually know what to run —
    added as the "additional fields" the spec explicitly allows for.
    """

    id: UUID
    project_id: UUID
    initiated_by: UUID
    plugin: str
    status: ScanStatus
    target_ids: list[UUID]
    plugin_config: dict[str, object]
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    logs_path: str | None = None
    artifacts_path: str | None = None
    exit_code: int | None = None
    error_message: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in SCAN_TERMINAL_STATUSES

    @property
    def is_cancellable(self) -> bool:
        return self.status in SCAN_CANCELLABLE_STATUSES


@dataclass(slots=True)
class ToolResult:
    """
    Normalized output from a single plugin invocation against a single
    target (Milestone 4A). One Scan may produce multiple ToolResults
    if it targets multiple hosts, but in the current architecture each
    scan invokes one plugin once, so typically one ToolResult per scan.
    """

    id: UUID
    scan_id: UUID
    plugin: str
    target: str
    normalized_payload: dict[str, object]
    raw_output_path: str | None = None
    created_at: datetime | None = None


@dataclass(slots=True)
class Asset:
    """
    A deduplicated discovered entity — host, subdomain, service,
    technology, or credential — tracked per-project with first/last
    seen timestamps and source scan lineage (SRS §2.3 FR-3.2).
    """

    id: UUID
    project_id: UUID
    asset_type: AssetType
    value: str
    first_seen: datetime
    last_seen: datetime
    in_scope: bool = True
    source_scan_id: UUID | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    created_at: datetime | None = None


@dataclass(slots=True)
class Finding:
    """
    A correlated, deduplicated finding derived from one or more
    ToolResults (SRS §5.2). Findings are tool-agnostic — the
    correlation engine merges multiple tool outputs into a single
    Finding via dedup_key.
    """

    id: UUID
    project_id: UUID
    title: str
    severity: Severity
    status: FindingStatus = FindingStatus.OPEN
    description: str | None = None
    asset_id: UUID | None = None
    cvss_score: float | None = None
    dedup_key: str = ""
    tool_result_ids: list[UUID] = field(default_factory=list)
    created_at: datetime | None = None


@dataclass(slots=True)
class Evidence:
    """An immutable evidence artifact attached to a finding (SRS §2.9).
    Content-addressed: storage path is derived from content hash."""

    id: UUID
    finding_id: UUID
    evidence_type: EvidenceType
    storage_pointer: str
    content_hash: str
    collected_by: UUID
    collected_at: datetime
    filename: str | None = None
    file_size: int | None = None
    created_at: datetime | None = None


@dataclass(slots=True)
class Report:
    id: UUID
    project_id: UUID
    title: str
    status: ReportStatus = ReportStatus.DRAFT
    created_at: datetime | None = None

    @property
    def is_final(self) -> bool:
        return self.status is ReportStatus.FINAL


@dataclass(slots=True)
class ReportVersion:
    id: UUID
    report_id: UUID
    version_number: int
    file_pointer: str
    is_redacted: bool
    generated_by: UUID
    generated_at: datetime
    created_at: datetime | None = None


@dataclass(slots=True)
class GraphNode:
    """A node in the project-scoped Knowledge Graph (SRS §15A)."""

    id: UUID
    project_id: UUID
    node_type: GraphNodeType
    source_table: str
    source_id: UUID
    label: str
    properties: dict[str, object] = field(default_factory=dict)
    created_at: datetime | None = None


@dataclass(slots=True)
class GraphEdge:
    """A directed, typed edge in the Knowledge Graph (SRS §15A)."""

    id: UUID
    project_id: UUID
    from_node_id: UUID
    to_node_id: UUID
    relationship_type: GraphEdgeType
    weight: float = 1.0
    properties: dict[str, object] = field(default_factory=dict)
    created_at: datetime | None = None


# --- Workflow (Phase 2/3) -------------------------------------------------


@dataclass(slots=True)
class Workflow:
    """
    A named, ordered chain of steps executed within a project.

    A Workflow is a DAG: each step declares its dependencies (step_ids
    it must wait for), and the executor topologically sorts before
    running. Cycles are rejected at validation time.
    """

    id: UUID
    project_id: UUID
    name: str
    description: str | None
    status: WorkflowStatus = WorkflowStatus.DRAFT
    created_by: UUID | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def is_executable(self) -> bool:
        return self.status is WorkflowStatus.ACTIVE


@dataclass(slots=True)
class WorkflowStep:
    """
    A single action within a Workflow DAG.

    Each step targets one plugin and optionally declares:
    - `depends_on`: list of WorkflowStep IDs that must complete first
    - `condition`: optional gate that must evaluate to True before this
      step runs (e.g., "only if previous step found open ports")
    """

    id: UUID
    workflow_id: UUID
    step_type: WorkflowStepType
    plugin: str
    name: str
    plugin_config: dict[str, object] = field(default_factory=dict)
    depends_on: list[UUID] = field(default_factory=list)
    condition: dict[str, object] | None = None
    timeout_seconds: int = 120
    max_retries: int = 0
    order: int = 0

    @property
    def has_condition(self) -> bool:
        return self.condition is not None


@dataclass(slots=True)
class WorkflowExecution:
    """
    A single run of a Workflow — analogous to a Scan being a single
    run of a plugin. Tracks overall status and start/end times.
    """

    id: UUID
    workflow_id: UUID
    project_id: UUID
    initiated_by: UUID
    status: ScanStatus = ScanStatus.QUEUED
    step_results: dict[str, dict[str, object]] = field(default_factory=dict)
    created_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in SCAN_TERMINAL_STATUSES

    @property
    def is_cancellable(self) -> bool:
        return self.status in SCAN_CANCELLABLE_STATUSES


@dataclass(slots=True)
class Schedule:
    """A recurring or one-shot trigger for a Workflow (Celery Beat)."""

    id: UUID
    workflow_id: UUID
    project_id: UUID
    frequency: ScheduleFrequency
    cron_expression: str | None = None
    is_active: bool = True
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
    created_by: UUID | None = None
    created_at: datetime | None = None
