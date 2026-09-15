"""Pydantic v2 request/response schemas for workflow endpoints (Phase 2/3)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.value_objects import (
    ScanStatus,
    ScheduleFrequency,
    ScheduleKind,
    WorkflowStatus,
)

# --- Workflow ----------------------------------------------------------------


class CreateWorkflowRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255, examples=["Full Recon"])
    description: str | None = Field(default=None, examples=["Subfinder → httpx → nmap → nuclei"])


class UpdateWorkflowRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None


class WorkflowResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    project_id: UUID
    name: str
    description: str | None
    status: WorkflowStatus
    created_by: UUID | None
    created_at: datetime | None
    updated_at: datetime | None


class WorkflowListResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[WorkflowResponse]


# --- WorkflowStep ------------------------------------------------------------


class CreateWorkflowStepRequest(BaseModel):
    plugin: str = Field(examples=["nmap"])
    name: str = Field(min_length=1, max_length=255, examples=["Port Scan"])
    plugin_config: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[UUID] = Field(default_factory=list)
    condition: dict[str, Any] | None = None
    timeout_seconds: int = Field(default=120, ge=1, le=3600)
    max_retries: int = Field(default=0, ge=0, le=5)
    order: int = Field(default=0, ge=0)


class UpdateWorkflowStepRequest(BaseModel):
    plugin: str | None = None
    name: str | None = Field(default=None, min_length=1, max_length=255)
    plugin_config: dict[str, Any] | None = None
    depends_on: list[UUID] | None = None
    condition: dict[str, Any] | None = None
    timeout_seconds: int | None = Field(default=None, ge=1, le=3600)
    max_retries: int | None = Field(default=None, ge=0, le=5)
    order: int | None = Field(default=None, ge=0)


class WorkflowStepResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    workflow_id: UUID
    step_type: str
    plugin: str
    name: str
    plugin_config: dict[str, Any]
    depends_on: list[UUID]
    condition: dict[str, Any] | None
    timeout_seconds: int
    max_retries: int
    order: int


class WorkflowStepListResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[WorkflowStepResponse]


# --- WorkflowExecution -------------------------------------------------------


class ExecuteWorkflowRequest(BaseModel):
    pass


class WorkflowExecutionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    workflow_id: UUID
    project_id: UUID
    initiated_by: UUID
    status: ScanStatus
    step_results: dict[str, Any]
    created_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    error_message: str | None


class WorkflowExecutionListResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[WorkflowExecutionResponse]


# --- Schedule ----------------------------------------------------------------


class CampaignScheduleConfigRequest(BaseModel):
    """Mirrors the interactive `CreateAutonomousRunRequest` bounds so a
    scheduled campaign can never request more autonomy than an interactive
    one allows."""

    objective: str = Field(default="", max_length=10_000)
    max_actions: int = Field(default=20, ge=1, le=50)
    max_runtime_seconds: int = Field(default=1800, ge=60, le=7200)


class CreateScheduleRequest(BaseModel):
    """Create a schedule for a workflow (default) or an autonomous campaign.

    For workflow schedules ``workflow_id`` is required; for campaign
    schedules ``kind`` must be ``campaign`` and a ``campaign`` payload
    must be supplied while ``workflow_id`` is omitted (the two kinds are
    mutually exclusive, enforced at creation time by `ScheduleService`).
    """

    workflow_id: UUID | None = None
    frequency: ScheduleFrequency
    cron_expression: str | None = Field(default=None, max_length=100)
    kind: ScheduleKind = ScheduleKind.WORKFLOW
    campaign: CampaignScheduleConfigRequest | None = None
    # M7.5 Phase 1: optional hard-stop for the schedule's lifetime.
    expires_at: datetime | None = None


class ScheduleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    @model_validator(mode="before")
    @classmethod
    def _flatten_campaign_config(cls, data: object) -> object:
        cc = (
            data.get("campaign_config")
            if isinstance(data, dict)
            else getattr(data, "campaign_config", None)
        )
        if cc is not None and hasattr(cc, "to_dict"):
            if isinstance(data, dict):
                data["campaign_config"] = cc.to_dict()
                return data
            dc_fields = getattr(data, "__dataclass_fields__", None)
            if dc_fields is not None:
                snapshot = {name: getattr(data, name) for name in dc_fields}
                snapshot["campaign_config"] = cc.to_dict()
                return snapshot
            snapshot = data.__dict__.copy() if hasattr(data, "__dict__") else {}
            snapshot["campaign_config"] = cc.to_dict()
            return snapshot
        return data

    id: UUID
    workflow_id: UUID | None
    project_id: UUID
    kind: ScheduleKind
    frequency: ScheduleFrequency
    cron_expression: str | None
    is_active: bool
    last_run_at: datetime | None
    next_run_at: datetime | None
    expires_at: datetime | None
    created_by: UUID | None
    created_at: datetime | None
    updated_at: datetime | None
    campaign_config: dict[str, Any] | None = None


class ScheduleListResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[ScheduleResponse]
