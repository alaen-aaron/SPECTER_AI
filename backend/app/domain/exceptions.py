"""
Domain exceptions.

These carry business meaning only — no HTTP status codes, no framework
imports. The API layer's exception handlers (`api/v1/error_handlers.py`)
are responsible for mapping each of these to the right RFC 7807
response. Keeping that mapping at the API boundary is what lets
`domain/`/`application/` stay framework-free.
"""

from __future__ import annotations

from uuid import UUID


class DomainError(Exception):
    """Base class for all domain-layer errors."""


# --- Auth ------------------------------------------------------------------


class EmailAlreadyRegisteredError(DomainError):
    def __init__(self, email: str) -> None:
        self.email = email
        super().__init__(f"An account with email '{email}' already exists.")


class InvalidCredentialsError(DomainError):
    def __init__(self) -> None:
        super().__init__("Invalid email or password.")


class InactiveUserError(DomainError):
    def __init__(self, user_id: UUID) -> None:
        self.user_id = user_id
        super().__init__(f"User {user_id} is inactive.")


class InvalidRefreshTokenError(DomainError):
    def __init__(self) -> None:
        super().__init__("Refresh token is invalid, expired, or has been revoked.")


# --- Authorization / RBAC ---------------------------------------------------


class InsufficientPermissionError(DomainError):
    def __init__(self, required_roles: tuple[str, ...]) -> None:
        self.required_roles = required_roles
        super().__init__(f"Requires one of roles: {', '.join(required_roles)}")


class NotAnOrganizationMemberError(DomainError):
    def __init__(self, organization_id: UUID) -> None:
        self.organization_id = organization_id
        super().__init__(f"User is not a member of organization {organization_id}.")


class NotAProjectMemberError(DomainError):
    def __init__(self, project_id: UUID) -> None:
        super().__init__(f"User is not a member of project {project_id}")
        self.project_id = project_id


class ProjectMemberAlreadyExistsError(DomainError):
    def __init__(self, project_id: UUID, user_id: UUID) -> None:
        super().__init__(
            f"User {user_id} is already a member of project {project_id}"
        )
        self.project_id = project_id
        self.user_id = user_id


# --- Not found ---------------------------------------------------------------


class OrganizationNotFoundError(DomainError):
    def __init__(self, organization_id: UUID) -> None:
        self.organization_id = organization_id
        super().__init__(f"Organization {organization_id} not found.")


class ProjectNotFoundError(DomainError):
    def __init__(self, project_id: UUID) -> None:
        self.project_id = project_id
        super().__init__(f"Project {project_id} not found.")


class TargetNotFoundError(DomainError):
    def __init__(self, target_id: UUID) -> None:
        self.target_id = target_id
        super().__init__(f"Target {target_id} not found.")


class AuthorizationRecordNotFoundError(DomainError):
    def __init__(self, project_id: UUID) -> None:
        self.project_id = project_id
        super().__init__(f"No authorization record found for project {project_id}.")


# --- Project lifecycle -------------------------------------------------------


class InvalidProjectStateTransitionError(DomainError):
    def __init__(self, current: str, requested: str) -> None:
        self.current = current
        self.requested = requested
        super().__init__(f"Cannot transition project from '{current}' to '{requested}'.")


class ProjectNotAuthorizedError(DomainError):
    """Raised when a project attempts to become Active without a valid AuthorizationRecord."""

    def __init__(self, project_id: UUID) -> None:
        self.project_id = project_id
        super().__init__(
            f"Project {project_id} cannot become Active without an attached, "
            "currently-valid authorization record (SRS FR-2.3)."
        )


# --- Target validation ---------------------------------------------------------


class InvalidTargetValueError(DomainError):
    def __init__(self, value: str, target_type: str) -> None:
        self.value = value
        self.target_type = target_type
        super().__init__(f"'{value}' is not a valid {target_type}.")


# --- Scope Guard (SRS §16.3) ---------------------------------------------------


class OutOfScopeTargetError(DomainError):
    """
    Raised when one or more targets are not covered by an active
    authorization record. This is the exception the API layer maps to
    the exact `422 out-of-scope-target` problem+json shape from SRS §6.3.
    """

    def __init__(self, target_ids: tuple[UUID, ...]) -> None:
        self.target_ids = target_ids
        joined = ", ".join(str(t) for t in target_ids)
        super().__init__(f"Target(s) outside authorized scope: {joined}")


class NoActiveAuthorizationError(DomainError):
    def __init__(self, project_id: UUID) -> None:
        self.project_id = project_id
        super().__init__(f"Project {project_id} has no currently-active authorization record.")


class ProjectNotActiveError(DomainError):
    def __init__(self, project_id: UUID, current_state: str) -> None:
        self.project_id = project_id
        self.current_state = current_state
        super().__init__(
            f"Project {project_id} is not Active (current state: {current_state}); "
            "scan execution is only permitted for Active projects."
        )


# --- Scans (Milestone 3) -----------------------------------------------------


class ScanNotFoundError(DomainError):
    def __init__(self, scan_id: UUID) -> None:
        self.scan_id = scan_id
        super().__init__(f"Scan {scan_id} not found.")


class ScanNotCancellableError(DomainError):
    def __init__(self, scan_id: UUID, current_status: str) -> None:
        self.scan_id = scan_id
        self.current_status = current_status
        super().__init__(
            f"Scan {scan_id} cannot be cancelled from status '{current_status}' "
            "(only 'queued' or 'running' scans can be cancelled)."
        )


class PluginNotFoundError(DomainError):
    def __init__(self, plugin_name: str) -> None:
        self.plugin_name = plugin_name
        super().__init__(f"No registered plugin named '{plugin_name}'.")


class InvalidPluginConfigError(DomainError):
    def __init__(self, plugin_name: str, reason: str) -> None:
        self.plugin_name = plugin_name
        self.reason = reason
        super().__init__(f"Invalid configuration for plugin '{plugin_name}': {reason}")


# --- Assets & Findings (Milestone 4A) ---------------------------------------


class AssetNotFoundError(DomainError):
    def __init__(self, asset_id: UUID) -> None:
        self.asset_id = asset_id
        super().__init__(f"Asset {asset_id} not found.")


class FindingNotFoundError(DomainError):
    def __init__(self, finding_id: UUID) -> None:
        self.finding_id = finding_id
        super().__init__(f"Finding {finding_id} not found.")


class EvidenceNotFoundError(DomainError):
    def __init__(self, evidence_id: UUID) -> None:
        self.evidence_id = evidence_id
        super().__init__(f"Evidence {evidence_id} not found.")


class EvidenceAttachmentError(DomainError):
    def __init__(self, reason: str) -> None:
        super().__init__(f"Failed to attach evidence: {reason}")


# --- Reports (Milestone 5) ---------------------------------------------------


class ReportNotFoundError(DomainError):
    def __init__(self, report_id: UUID) -> None:
        self.report_id = report_id
        super().__init__(f"Report {report_id} not found.")


class ReportAlreadyFinalizedError(DomainError):
    def __init__(self, report_id: UUID) -> None:
        self.report_id = report_id
        super().__init__(f"Report {report_id} is already finalized.")


# --- Knowledge Graph (Milestone 5) -------------------------------------------


class GraphNodeNotFoundError(DomainError):
    def __init__(self, node_id: UUID) -> None:
        self.node_id = node_id
        super().__init__(f"Graph node {node_id} not found.")


class GraphEdgeNotFoundError(DomainError):
    def __init__(self, edge_id: UUID) -> None:
        self.edge_id = edge_id
        super().__init__(f"Graph edge {edge_id} not found.")


# --- Workflows (Phase 2/3) ------------------------------------------------


class WorkflowNotFoundError(DomainError):
    def __init__(self, workflow_id: UUID) -> None:
        self.workflow_id = workflow_id
        super().__init__(f"Workflow {workflow_id} not found.")


class WorkflowEmptyError(DomainError):
    def __init__(self, workflow_id: UUID) -> None:
        self.workflow_id = workflow_id
        super().__init__(
            f"Cannot activate workflow {workflow_id}: workflow has no steps."
        )


class WorkflowHasCyclesError(DomainError):
    def __init__(self, cycle_path: str) -> None:
        self.cycle_path = cycle_path
        super().__init__(f"Workflow contains a dependency cycle: {cycle_path}")


class WorkflowNotExecutableError(DomainError):
    def __init__(self, workflow_id: UUID, status: str) -> None:
        self.workflow_id = workflow_id
        self.status = status
        super().__init__(
            f"Workflow {workflow_id} cannot be executed from status '{status}'; "
            "must be 'active'."
        )


class WorkflowStepDependencyError(DomainError):
    def __init__(self, step_id: UUID, missing_dep: UUID) -> None:
        self.step_id = step_id
        self.missing_dep = missing_dep
        super().__init__(
            f"Step {step_id} depends on unknown step {missing_dep}."
        )


class WorkflowExecutionNotFoundError(DomainError):
    def __init__(self, execution_id: UUID) -> None:
        self.execution_id = execution_id
        super().__init__(f"Workflow execution {execution_id} not found.")


class WorkflowExecutionNotCancellableError(DomainError):
    def __init__(self, execution_id: UUID, current_status: str) -> None:
        self.execution_id = execution_id
        self.current_status = current_status
        super().__init__(
            f"Workflow execution {execution_id} cannot be cancelled from "
            f"status '{current_status}'."
        )


class ScheduleNotFoundError(DomainError):
    def __init__(self, schedule_id: UUID) -> None:
        self.schedule_id = schedule_id
        super().__init__(f"Schedule {schedule_id} not found.")


class InvalidScheduleConfigError(DomainError):
    def __init__(self, reason: str) -> None:
        super().__init__(f"Invalid schedule configuration: {reason}")


# --- AI Decision Engine (Phase 4, SRS §8) -----------------------------------


class PlannedActionNotFoundError(DomainError):
    def __init__(self, action_id: UUID) -> None:
        self.action_id = action_id
        super().__init__(f"Planned action {action_id} not found.")


class PlannedActionNotApprovableError(DomainError):
    def __init__(self, action_id: UUID, status: str) -> None:
        self.action_id = action_id
        self.status = status
        super().__init__(
            f"Planned action {action_id} cannot be approved from status '{status}'; "
            "must be 'pending_review'."
        )


class PlannedActionExpiredError(DomainError):
    def __init__(self, action_id: UUID) -> None:
        self.action_id = action_id
        super().__init__(f"Planned action {action_id} has expired.")


class ActionNotExecutableError(DomainError):
    """M7.2 — an action may only execute from the APPROVED state."""

    def __init__(self, action_id: UUID, status: str) -> None:
        self.action_id = action_id
        self.status = status
        super().__init__(
            f"Planned action {action_id} cannot be executed from status "
            f"'{status}'; requires human approval first (SRS §8.4)."
        )


class ActionRejectedByValidatorError(DomainError):
    """
    M7.2 — deterministic validation rejected an AI-proposed action.

    `reasons` lists every failed check; the action is never executed
    and the proposal is persisted only as an audit/rejection record.
    """

    def __init__(self, action_id: UUID, reasons: list[str]) -> None:
        self.action_id = action_id
        self.reasons = reasons
        super().__init__(
            f"AI-proposed action {action_id} rejected by deterministic "
            f"validation: {'; '.join(reasons)}"
        )


class RiskScoreNotFoundError(DomainError):
    def __init__(self, score_id: UUID) -> None:
        self.score_id = score_id
        super().__init__(f"Risk score {score_id} not found.")


class RiskScoreAlreadyExistsError(DomainError):
    def __init__(self, finding_id: UUID) -> None:
        self.finding_id = finding_id
        super().__init__(f"Risk score already exists for finding {finding_id}.")


class PromptTemplateNotFoundError(DomainError):
    def __init__(self, template_id: UUID) -> None:
        self.template_id = template_id
        super().__init__(f"Prompt template {template_id} not found.")


class PromptTemplateInactiveError(DomainError):
    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"No active prompt template found with name '{name}'.")


class LLMProviderError(DomainError):
    def __init__(self, provider: str, reason: str) -> None:
        self.provider = provider
        self.reason = reason
        super().__init__(f"LLM provider '{provider}' failed: {reason}")


class AIContextMemoryNotFoundError(DomainError):
    def __init__(self, memory_id: UUID) -> None:
        self.memory_id = memory_id
        super().__init__(f"AI context memory {memory_id} not found.")


# --- Autonomous Orchestration (M7.4) ----------------------------------------


class AutonomousRunNotFoundError(DomainError):
    def __init__(self, run_id: UUID) -> None:
        self.run_id = run_id
        super().__init__(f"Autonomous run {run_id} not found.")


class AutonomousRunNotCancellableError(DomainError):
    def __init__(self, run_id: UUID, current_status: str) -> None:
        self.run_id = run_id
        self.current_status = current_status
        super().__init__(
            f"Autonomous run {run_id} cannot be cancelled from status "
            f"'{current_status}'."
        )


class AutonomousRunInvalidTransitionError(DomainError):
    def __init__(self, current_status: str, requested_status: str) -> None:
        self.current_status = current_status
        self.requested_status = requested_status
        super().__init__(
            f"Cannot transition autonomous run from '{current_status}' to "
            f"'{requested_status}'."
        )


class AutonomousRunBudgetExceededError(DomainError):
    def __init__(self, run_id: UUID, budget_type: str) -> None:
        self.run_id = run_id
        self.budget_type = budget_type
        super().__init__(f"Autonomous run {run_id} exceeded {budget_type} budget.")


class AutonomousRunActiveExistsError(DomainError):
    def __init__(self, project_id: UUID) -> None:
        self.project_id = project_id
        super().__init__(
            f"An active autonomous run already exists for project {project_id}."
        )


class AutonomousActionNotApprovableError(DomainError):
    def __init__(self, action_id: UUID, status: str) -> None:
        self.action_id = action_id
        self.status = status
        super().__init__(
            f"Autonomous action {action_id} cannot be approved from status "
            f"'{status}'; must be 'proposed'."
        )
