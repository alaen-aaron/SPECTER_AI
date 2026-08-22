"""
Domain exception -> RFC 7807 problem+json mapping (SRS §6.1, §6.3).

This is the ONLY place in the codebase that knows domain exceptions map
to HTTP status codes — `domain/` and `application/` raise plain
exceptions with zero awareness of HTTP. Keeping the mapping here (not
scattered across routers with try/except) means adding a new domain
exception never requires touching more than one file to wire up its
response shape.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.domain.exceptions import (
    ActionNotExecutableError,
    ActionRejectedByValidatorError,
    AIContextMemoryNotFoundError,
    AssetNotFoundError,
    AuthorizationRecordNotFoundError,
    DomainError,
    EmailAlreadyRegisteredError,
    EvidenceAttachmentError,
    EvidenceNotFoundError,
    FindingNotFoundError,
    GraphEdgeNotFoundError,
    GraphNodeNotFoundError,
    InactiveUserError,
    InsufficientPermissionError,
    InvalidCredentialsError,
    InvalidPluginConfigError,
    InvalidProjectStateTransitionError,
    InvalidRefreshTokenError,
    InvalidScheduleConfigError,
    InvalidTargetValueError,
    LLMProviderError,
    NoActiveAuthorizationError,
    NotAnOrganizationMemberError,
    NotAProjectMemberError,
    OrganizationNotFoundError,
    OutOfScopeTargetError,
    PlannedActionExpiredError,
    PlannedActionNotApprovableError,
    PlannedActionNotFoundError,
    PluginNotFoundError,
    ProjectMemberAlreadyExistsError,
    ProjectNotActiveError,
    ProjectNotAuthorizedError,
    ProjectNotFoundError,
    PromptTemplateInactiveError,
    PromptTemplateNotFoundError,
    ReportAlreadyFinalizedError,
    ReportNotFoundError,
    RiskScoreAlreadyExistsError,
    RiskScoreNotFoundError,
    ScanNotCancellableError,
    ScanNotFoundError,
    ScheduleNotFoundError,
    TargetNotFoundError,
    WorkflowEmptyError,
    WorkflowExecutionNotCancellableError,
    WorkflowExecutionNotFoundError,
    WorkflowHasCyclesError,
    WorkflowNotExecutableError,
    WorkflowNotFoundError,
    WorkflowStepDependencyError,
)

_PROBLEM_BASE_URL = "https://specter.ai/errors"

# (status_code, url-safe type slug). Order doesn't matter — lookup is by
# exact exception type via `type(exc)`, matching the most specific
# raised class rather than walking an MRO chain.
_EXCEPTION_MAP: dict[type[DomainError], tuple[int, str]] = {
    EmailAlreadyRegisteredError: (409, "email-already-registered"),
    InvalidCredentialsError: (401, "invalid-credentials"),
    InactiveUserError: (403, "inactive-user"),
    InvalidRefreshTokenError: (401, "invalid-refresh-token"),
    InsufficientPermissionError: (403, "insufficient-permission"),
    NotAnOrganizationMemberError: (403, "not-an-organization-member"),
    NotAProjectMemberError: (403, "not-a-project-member"),
    ProjectMemberAlreadyExistsError: (409, "project-member-already-exists"),
    OrganizationNotFoundError: (404, "organization-not-found"),
    ProjectNotFoundError: (404, "project-not-found"),
    TargetNotFoundError: (404, "target-not-found"),
    AuthorizationRecordNotFoundError: (404, "authorization-record-not-found"),
    InvalidProjectStateTransitionError: (422, "invalid-project-state-transition"),
    ProjectNotAuthorizedError: (422, "project-not-authorized"),
    InvalidTargetValueError: (422, "invalid-target-value"),
    OutOfScopeTargetError: (422, "out-of-scope-target"),
    NoActiveAuthorizationError: (422, "no-active-authorization"),
    ProjectNotActiveError: (422, "project-not-active"),
    ScanNotFoundError: (404, "scan-not-found"),
    ScanNotCancellableError: (409, "scan-not-cancellable"),
    PluginNotFoundError: (404, "plugin-not-found"),
    InvalidPluginConfigError: (422, "invalid-plugin-config"),
    AssetNotFoundError: (404, "asset-not-found"),
    FindingNotFoundError: (404, "finding-not-found"),
    EvidenceNotFoundError: (404, "evidence-not-found"),
    EvidenceAttachmentError: (422, "evidence-attachment-error"),
    ReportNotFoundError: (404, "report-not-found"),
    ReportAlreadyFinalizedError: (409, "report-already-finalized"),
    GraphNodeNotFoundError: (404, "graph-node-not-found"),
    GraphEdgeNotFoundError: (404, "graph-edge-not-found"),
    WorkflowNotFoundError: (404, "workflow-not-found"),
    WorkflowEmptyError: (422, "workflow-empty"),
    WorkflowHasCyclesError: (422, "workflow-has-cycles"),
    WorkflowNotExecutableError: (409, "workflow-not-executable"),
    WorkflowStepDependencyError: (422, "workflow-step-dependency-error"),
    WorkflowExecutionNotFoundError: (404, "workflow-execution-not-found"),
    WorkflowExecutionNotCancellableError: (409, "workflow-execution-not-cancellable"),
    ScheduleNotFoundError: (404, "schedule-not-found"),
    InvalidScheduleConfigError: (422, "invalid-schedule-config"),
    # AI Decision Engine (Phase 4)
    PlannedActionNotFoundError: (404, "planned-action-not-found"),
    PlannedActionNotApprovableError: (409, "planned-action-not-approvable"),
    PlannedActionExpiredError: (410, "planned-action-expired"),
    # AI planning & controlled execution (M7.2)
    ActionNotExecutableError: (409, "action-not-executable"),
    ActionRejectedByValidatorError: (422, "action-rejected-by-validator"),
    RiskScoreNotFoundError: (404, "risk-score-not-found"),
    RiskScoreAlreadyExistsError: (409, "risk-score-already-exists"),
    PromptTemplateNotFoundError: (404, "prompt-template-not-found"),
    PromptTemplateInactiveError: (404, "prompt-template-inactive"),
    LLMProviderError: (502, "llm-provider-error"),
    AIContextMemoryNotFoundError: (404, "ai-context-memory-not-found"),
}

_DEFAULT_STATUS_AND_SLUG = (400, "domain-error")


def _problem_response(exc: DomainError, request: Request) -> JSONResponse:
    status_code, slug = _EXCEPTION_MAP.get(type(exc), _DEFAULT_STATUS_AND_SLUG)
    body = {
        "type": f"{_PROBLEM_BASE_URL}/{slug}",
        "title": type(exc).__name__,
        "status": status_code,
        "detail": str(exc),
        "instance": str(request.url),
    }
    return JSONResponse(status_code=status_code, content=body)


def register_error_handlers(app: FastAPI) -> None:
    """Call once at app startup (see `app/main.py`)."""

    @app.exception_handler(DomainError)
    async def _handle_domain_error(request: Request, exc: DomainError) -> JSONResponse:
        return _problem_response(exc, request)
