"""Pydantic v2 schemas for AI Decision Engine endpoints (Phase 4, SRS §8)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

# --- Planned Actions (SRS §8.4) --------------------------------------------


class PlannedActionCreate(BaseModel):
    """Request to create a planned action (typically from AI Planner)."""

    project_id: UUID
    action_type: str = Field(..., max_length=50)
    title: str = Field(..., max_length=500)
    description: str
    justification: str
    plugin: str | None = None
    target_ids: list[UUID] = Field(default_factory=list)
    plugin_config: dict[str, object] = Field(default_factory=dict)


class PlannedActionResponse(BaseModel):
    """Response for a planned action."""

    id: UUID
    project_id: UUID
    action_type: str
    title: str
    description: str
    justification: str
    plugin: str | None = None
    target_ids: list[UUID] = Field(default_factory=list)
    plugin_config: dict[str, object] = Field(default_factory=dict)
    status: str
    approved_by: UUID | None = None
    approved_at: datetime | None = None
    rejection_reason: str | None = None
    expires_at: datetime | None = None
    created_by: UUID | None = None
    created_at: datetime | None = None
    # --- M7.2 ---
    objective: str | None = None
    expected_value: str | None = None
    risk_level: str | None = None
    scan_id: UUID | None = None

    model_config = {"from_attributes": True}


class PlannedActionApprove(BaseModel):
    """Request to approve a planned action."""

    reason: str = ""


class PlannedActionReject(BaseModel):
    """Request to reject a planned action."""

    reason: str = ""


# --- M7.2: AI planning & controlled execution -------------------------------


class PlanRequest(BaseModel):
    """Request an AI planning session (plan -> validate -> return)."""

    objective: str = Field(default="", max_length=2000)
    max_actions: int = Field(default=3, ge=1, le=5)


class ValidationCheckResponse(BaseModel):
    """One deterministic validation check and its outcome."""

    name: str
    passed: bool
    detail: str = ""


class ProposalValidationResponse(BaseModel):
    """Deterministic validation result for one AI-proposed action."""

    accepted: bool
    checks: list[ValidationCheckResponse] = Field(default_factory=list)
    runner_mode: str


class ProposedActionResponse(BaseModel):
    """A planner proposal paired with its validation result."""

    action: PlannedActionResponse
    validation: ProposalValidationResponse
    persisted: bool


class PlanResponse(BaseModel):
    """Result of a planning session (never auto-executes)."""

    proposals: list[ProposedActionResponse]
    skipped_duplicates: int
    ungrounded: int
    stopped_because: str
    context_summary: dict[str, object]
    runner_mode: str


class ExecutePlannedActionResponse(BaseModel):
    """Result of executing an APPROVED planned action via ScanService."""

    action: PlannedActionResponse
    scan_id: UUID
    scan_status: str


# --- Risk Scores (SRS FR-7.3) ----------------------------------------------


class RiskScoreResponse(BaseModel):
    """Response for a risk score."""

    id: UUID
    finding_id: UUID
    base_score: float
    exposure_modifier: float
    effective_score: float
    ai_rationale: str | None = None
    review_status: str
    source: str
    computed_at: datetime | None = None

    model_config = {"from_attributes": True}


class RiskScoreCreate(BaseModel):
    """Request to compute a risk score for a finding."""

    finding_id: UUID
    exposure_modifier: float = 0.0


# --- Explainer (SRS FR-7.4) -------------------------------------------------


class ExplainResponse(BaseModel):
    """Response for a finding explanation."""

    finding_id: UUID
    explanation: str
    why_it_matters: str
    how_to_fix: str
    review_status: str


# --- Reporter (SRS FR-7.5) --------------------------------------------------


class ExecutiveSummaryResponse(BaseModel):
    """Response for an AI-drafted executive summary."""

    summary: str
    review_status: str
    finding_count: int
    severity_breakdown: dict[str, int]
    recommendation: str | None = None


class FindingNarrativeResponse(BaseModel):
    """Response for an AI-drafted finding narrative."""

    finding_id: UUID
    overview: str
    impact: str
    technical_details: str
    remediation: str
    review_status: str


# --- Analyzer (SRS FR-7.2) --------------------------------------------------


class CorrelationResultResponse(BaseModel):
    """Response for correlation analysis results."""

    total_findings: int
    unique_findings: int
    correlations_found: int
    duplicates_merged: int


class FindingCorrelationResponse(BaseModel):
    """Response for a finding's correlations."""

    finding_id: UUID
    title: str
    severity: str
    shared_tool_results: int


# --- Context Memory (SRS §8) ------------------------------------------------


class ContextMemoryCreate(BaseModel):
    """Request to add a context memory entry."""

    memory_type: str = Field(..., max_length=50)
    content: str
    metadata: dict[str, object] = Field(default_factory=dict)


class ContextMemoryResponse(BaseModel):
    """Response for a context memory entry."""

    id: UUID
    project_id: UUID
    memory_type: str
    content: str
    metadata: dict[str, object] = Field(default_factory=dict)
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


# --- Prompt Templates (SRS §8.2) -------------------------------------------


class PromptTemplateCreate(BaseModel):
    """Request to create a prompt template."""

    name: str = Field(..., max_length=100)
    purpose: str
    template_text: str
    required_variables: list[str] = Field(default_factory=list)
    expected_output_schema: dict[str, object] = Field(default_factory=dict)
    version: int = 1


class PromptTemplateResponse(BaseModel):
    """Response for a prompt template."""

    id: UUID
    name: str
    version: int
    purpose: str
    template_text: str
    required_variables: list[str] = Field(default_factory=list)
    expected_output_schema: dict[str, object] = Field(default_factory=dict)
    is_active: bool
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class PromptTemplateRenderRequest(BaseModel):
    """Request to render a prompt template with variables."""

    variables: dict[str, str] = Field(default_factory=dict)


class PromptTemplateRenderResponse(BaseModel):
    """Response for a rendered prompt template."""

    rendered_text: str
    template_id: UUID
    name: str
    version: int
