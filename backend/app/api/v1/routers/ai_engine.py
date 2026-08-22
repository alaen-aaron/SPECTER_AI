"""
AI Decision Engine API endpoints (SRS §8).

Covers Planner suggestions + approval, Analyzer correlation,
Risk Engine scoring, Explainer explanations, Reporter drafts,
Context Memory, and Prompt Library.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.v1.deps import (
    get_ai_reporter_service,
    get_analyzer_service,
    get_context_memory_service,
    get_current_user,
    get_explainer_service,
    get_planner_service,
    get_prompt_library_service,
    get_risk_engine_service,
    get_scan_service,
    require_project_role,
)
from app.api.v1.schemas.ai_engine import (
    ContextMemoryCreate,
    ContextMemoryResponse,
    CorrelationResultResponse,
    ExecutePlannedActionResponse,
    ExecutiveSummaryResponse,
    ExplainResponse,
    FindingNarrativeResponse,
    PlannedActionApprove,
    PlannedActionReject,
    PlannedActionResponse,
    PlanRequest,
    PlanResponse,
    PromptTemplateCreate,
    PromptTemplateRenderRequest,
    PromptTemplateRenderResponse,
    PromptTemplateResponse,
    ProposedActionResponse,
    RiskScoreCreate,
    RiskScoreResponse,
)
from app.application.ai_reporter_service import AIReporterService
from app.application.analyzer_service import AnalyzerService
from app.application.context_memory_service import ContextMemoryService
from app.application.explainer_service import ExplainerService
from app.application.planner_service import PlannerService
from app.application.prompt_library_service import PromptLibraryService
from app.application.risk_engine_service import RiskEngineService
from app.application.scan_service import ScanService
from app.domain.entities import User
from app.domain.value_objects import ProjectRole

router = APIRouter(prefix="/ai", tags=["AI Decision Engine"])

_AI_CAPABLE_PROJECT_ROLES = frozenset({
    ProjectRole.OWNER,
    ProjectRole.ADMIN,
    ProjectRole.LEAD_TESTER,
    ProjectRole.TESTER,
})


# --- Planner (SRS §8.4) ----------------------------------------------------


@router.get(
    "/planner/suggestions",
    response_model=list[PlannedActionResponse],
    summary="List planner suggestions for a project",
)
async def list_planner_suggestions(
    project_id: UUID,
    status: str | None = None,
    current_user: User = Depends(get_current_user),
    _member: object = Depends(require_project_role(*_AI_CAPABLE_PROJECT_ROLES)),
    planner: PlannerService = Depends(get_planner_service),
) -> list[PlannedActionResponse]:
    from app.domain.value_objects import PlannedActionStatus

    status_enum = PlannedActionStatus(status) if status else None
    actions = await planner.list_for_project(project_id, status=status_enum)
    return [PlannedActionResponse.model_validate(a) for a in actions]


@router.post(
    "/planner/suggest",
    response_model=list[PlannedActionResponse],
    summary="Generate planner suggestions for a project",
)
async def suggest_planner_actions(
    project_id: UUID,
    current_user: User = Depends(get_current_user),
    _member: object = Depends(require_project_role(*_AI_CAPABLE_PROJECT_ROLES)),
    planner: PlannerService = Depends(get_planner_service),
) -> list[PlannedActionResponse]:
    actions = await planner.suggest(project_id, created_by=current_user.id)
    return [PlannedActionResponse.model_validate(a) for a in actions]


@router.get(
    "/planner/suggestions/{action_id}",
    response_model=PlannedActionResponse,
    summary="Get a specific planned action",
)
async def get_planner_suggestion(
    action_id: UUID,
    current_user: User = Depends(get_current_user),
    planner: PlannerService = Depends(get_planner_service),
) -> PlannedActionResponse:
    action = await planner.get(action_id)
    return PlannedActionResponse.model_validate(action)


@router.post(
    "/planner/suggestions/{action_id}/approve",
    response_model=PlannedActionResponse,
    summary="Approve a planned action (human-in-the-loop)",
)
async def approve_planner_suggestion(
    action_id: UUID,
    body: PlannedActionApprove,
    current_user: User = Depends(get_current_user),
    _member: object = Depends(require_project_role(*_AI_CAPABLE_PROJECT_ROLES)),
    planner: PlannerService = Depends(get_planner_service),
) -> PlannedActionResponse:
    action = await planner.approve(action_id, approved_by=current_user.id)
    return PlannedActionResponse.model_validate(action)


@router.post(
    "/planner/suggestions/{action_id}/reject",
    response_model=PlannedActionResponse,
    summary="Reject a planned action",
)
async def reject_planner_suggestion(
    action_id: UUID,
    body: PlannedActionReject,
    current_user: User = Depends(get_current_user),
    _member: object = Depends(require_project_role(*_AI_CAPABLE_PROJECT_ROLES)),
    planner: PlannerService = Depends(get_planner_service),
) -> PlannedActionResponse:
    action = await planner.reject(
        action_id, rejected_by=current_user.id, reason=body.reason
    )
    return PlannedActionResponse.model_validate(action)


# --- M7.2: AI planning & controlled execution -------------------------------


def _proposal_response(p: object) -> ProposedActionResponse:
    from app.api.v1.schemas.ai_engine import (
        ProposalValidationResponse,
        ValidationCheckResponse,
    )

    validation = p.validation  # type: ignore[attr-defined]
    return ProposedActionResponse(
        action=PlannedActionResponse.model_validate(p.action),  # type: ignore[attr-defined]
        validation=ProposalValidationResponse(
            accepted=validation.accepted,
            checks=[
                ValidationCheckResponse(name=c.name, passed=c.passed, detail=c.detail)
                for c in validation.checks
            ],
            runner_mode=validation.runner_mode,
        ),
        persisted=p.persisted,  # type: ignore[attr-defined]
    )


@router.post(
    "/planner/plan",
    response_model=PlanResponse,
    summary="M7.2: run an AI planning session (plan -> validate -> return)",
    description=(
        "Builds the project-scoped security context, asks the planner for "
        "structured action proposals, validates each one through the "
        "deterministic ActionProposalValidator (plugin policy, target "
        "ownership, Scope Guard, duplicate prevention, executor "
        "constraints), persists only ACCEPTED proposals as "
        "pending_review, and returns every proposal with its validation "
        "result. Nothing is executed by this endpoint — SRS §8.4 human "
        "approval remains mandatory before any execution."
    ),
)
async def plan_actions(
    project_id: UUID,
    body: PlanRequest,
    current_user: User = Depends(get_current_user),
    _member: object = Depends(require_project_role(*_AI_CAPABLE_PROJECT_ROLES)),
    planner: PlannerService = Depends(get_planner_service),
) -> PlanResponse:
    outcome = await planner.plan(
        project_id=project_id,
        created_by=current_user.id,
        objective=body.objective,
        max_actions=body.max_actions,
    )
    return PlanResponse(
        proposals=[_proposal_response(p) for p in outcome.proposals],
        skipped_duplicates=outcome.skipped_duplicates,
        ungrounded=outcome.ungrounded,
        stopped_because=outcome.stopped_because,
        context_summary=outcome.context_summary,
        runner_mode=outcome.runner_mode,
    )


@router.post(
    "/planner/suggestions/{action_id}/execute",
    response_model=ExecutePlannedActionResponse,
    summary="M7.2: execute an APPROVED planned action (human-gated)",
    description=(
        "The controlled bridge from an approved AI proposal to the "
        "existing execution path. Requires status=approved (SRS §8.4), "
        "re-runs the deterministic validator, then delegates to "
        "ScanService.create — which re-validates Scope Guard and plugin "
        "policy before dispatching through the M7.1 isolated executor. "
        "No LLM output ever reaches Celery directly."
    ),
)
async def execute_planned_action(
    project_id: UUID,
    action_id: UUID,
    current_user: User = Depends(get_current_user),
    _member: object = Depends(require_project_role(*_AI_CAPABLE_PROJECT_ROLES)),
    planner: PlannerService = Depends(get_planner_service),
    scan_service: ScanService = Depends(get_scan_service),
) -> ExecutePlannedActionResponse:
    action, scan = await planner.execute_approved(
        action_id=action_id,
        initiated_by=current_user.id,
        launch_scan=scan_service.create,
        expected_project_id=project_id,
    )
    return ExecutePlannedActionResponse(
        action=PlannedActionResponse.model_validate(action),
        scan_id=scan.id,
        scan_status=scan.status.value,
    )


# --- Analyzer (SRS FR-7.2) --------------------------------------------------


@router.post(
    "/analyzer/correlate",
    response_model=CorrelationResultResponse,
    summary="Run correlation analysis on project findings",
)
async def correlate_findings(
    project_id: UUID,
    current_user: User = Depends(get_current_user),
    _member: object = Depends(require_project_role(*_AI_CAPABLE_PROJECT_ROLES)),
    analyzer: AnalyzerService = Depends(get_analyzer_service),
) -> CorrelationResultResponse:
    result = await analyzer.correlate_findings(project_id)
    return CorrelationResultResponse(**result)


# --- Explainer (SRS FR-7.4) -------------------------------------------------


@router.get(
    "/explain/{finding_id}",
    response_model=ExplainResponse,
    summary="Get AI explanation for a finding",
)
async def explain_finding(
    finding_id: UUID,
    current_user: User = Depends(get_current_user),
    explainer: ExplainerService = Depends(get_explainer_service),
) -> ExplainResponse:
    result = await explainer.explain_finding(finding_id)
    return ExplainResponse(finding_id=finding_id, **result)


# --- Risk Engine (SRS FR-7.3) -----------------------------------------------


@router.post(
    "/risk-scores",
    response_model=RiskScoreResponse,
    summary="Compute a risk score for a finding",
)
async def compute_risk_score(
    body: RiskScoreCreate,
    current_user: User = Depends(get_current_user),
    _member: object = Depends(require_project_role(*_AI_CAPABLE_PROJECT_ROLES)),
    risk_engine: RiskEngineService = Depends(get_risk_engine_service),
) -> RiskScoreResponse:
    score = await risk_engine.compute_risk_score(
        body.finding_id, exposure_modifier=body.exposure_modifier
    )
    return RiskScoreResponse(
        id=score.id,
        finding_id=score.finding_id,
        base_score=score.base_score,
        exposure_modifier=score.exposure_modifier,
        effective_score=score.effective_score,
        ai_rationale=score.ai_rationale,
        review_status=score.review_status.value,
        source=score.source.value,
        computed_at=score.computed_at,
    )


@router.get(
    "/risk-scores/{score_id}",
    response_model=RiskScoreResponse,
    summary="Get a specific risk score",
)
async def get_risk_score(
    score_id: UUID,
    current_user: User = Depends(get_current_user),
    risk_engine: RiskEngineService = Depends(get_risk_engine_service),
) -> RiskScoreResponse:
    score = await risk_engine.get_risk_score(score_id)
    return RiskScoreResponse(
        id=score.id,
        finding_id=score.finding_id,
        base_score=score.base_score,
        exposure_modifier=score.exposure_modifier,
        effective_score=score.effective_score,
        ai_rationale=score.ai_rationale,
        review_status=score.review_status.value,
        source=score.source.value,
        computed_at=score.computed_at,
    )


# --- Reporter (SRS FR-7.5) --------------------------------------------------


@router.get(
    "/reporter/executive-summary",
    response_model=ExecutiveSummaryResponse,
    summary="Get AI-drafted executive summary for a project",
)
async def draft_executive_summary(
    project_id: UUID,
    current_user: User = Depends(get_current_user),
    _member: object = Depends(require_project_role(*_AI_CAPABLE_PROJECT_ROLES)),
    reporter: AIReporterService = Depends(get_ai_reporter_service),
) -> ExecutiveSummaryResponse:
    result = await reporter.draft_executive_summary(project_id)
    return ExecutiveSummaryResponse(**result)


@router.get(
    "/reporter/finding-narrative/{finding_id}",
    response_model=FindingNarrativeResponse,
    summary="Get AI-drafted narrative for a finding",
)
async def draft_finding_narrative(
    finding_id: UUID,
    current_user: User = Depends(get_current_user),
    reporter: AIReporterService = Depends(get_ai_reporter_service),
) -> FindingNarrativeResponse:
    result = await reporter.draft_finding_narrative(finding_id)
    return FindingNarrativeResponse(finding_id=finding_id, **result)


# --- Context Memory (SRS §8) ------------------------------------------------


@router.get(
    "/context-memory",
    response_model=list[ContextMemoryResponse],
    summary="List context memories for a project",
)
async def list_context_memory(
    project_id: UUID,
    memory_type: str | None = None,
    current_user: User = Depends(get_current_user),
    service: ContextMemoryService = Depends(get_context_memory_service),
) -> list[ContextMemoryResponse]:
    if memory_type:
        memories = await service.list_by_type(project_id, memory_type)
    else:
        memories = await service.list_for_project(project_id)
    return [ContextMemoryResponse.model_validate(m) for m in memories]


@router.post(
    "/context-memory",
    response_model=ContextMemoryResponse,
    summary="Add a context memory entry",
)
async def add_context_memory(
    project_id: UUID,
    body: ContextMemoryCreate,
    current_user: User = Depends(get_current_user),
    _member: object = Depends(require_project_role(*_AI_CAPABLE_PROJECT_ROLES)),
    service: ContextMemoryService = Depends(get_context_memory_service),
) -> ContextMemoryResponse:
    memory = await service.add_memory(
        project_id,
        memory_type=body.memory_type,
        content=body.content,
        metadata=body.metadata,
    )
    return ContextMemoryResponse.model_validate(memory)


# --- Prompt Templates (SRS §8.2) -------------------------------------------


@router.get(
    "/prompts",
    response_model=list[PromptTemplateResponse],
    summary="List all prompt templates",
)
async def list_prompt_templates(
    current_user: User = Depends(get_current_user),
    service: PromptLibraryService = Depends(get_prompt_library_service),
) -> list[PromptTemplateResponse]:
    templates = await service.list_all()
    return [PromptTemplateResponse.model_validate(t) for t in templates]


@router.post(
    "/prompts",
    response_model=PromptTemplateResponse,
    summary="Create a prompt template",
)
async def create_prompt_template(
    body: PromptTemplateCreate,
    current_user: User = Depends(get_current_user),
    service: PromptLibraryService = Depends(get_prompt_library_service),
) -> PromptTemplateResponse:
    template = await service.create_template(
        name=body.name,
        purpose=body.purpose,
        template_text=body.template_text,
        required_variables=body.required_variables,
        expected_output_schema=body.expected_output_schema,
        version=body.version,
    )
    return PromptTemplateResponse.model_validate(template)


@router.get(
    "/prompts/{template_id}",
    response_model=PromptTemplateResponse,
    summary="Get a prompt template by ID",
)
async def get_prompt_template(
    template_id: UUID,
    current_user: User = Depends(get_current_user),
    service: PromptLibraryService = Depends(get_prompt_library_service),
) -> PromptTemplateResponse:
    template = await service.get_template(template_id)
    return PromptTemplateResponse.model_validate(template)


@router.post(
    "/prompts/{template_id}/render",
    response_model=PromptTemplateRenderResponse,
    summary="Render a prompt template with variables",
)
async def render_prompt_template(
    template_id: UUID,
    body: PromptTemplateRenderRequest,
    current_user: User = Depends(get_current_user),
    service: PromptLibraryService = Depends(get_prompt_library_service),
) -> PromptTemplateRenderResponse:
    rendered = await service.render_template(template_id, body.variables)
    template = await service.get_template(template_id)
    return PromptTemplateRenderResponse(
        rendered_text=rendered,
        template_id=template.id,
        name=template.name,
        version=template.version,
    )
