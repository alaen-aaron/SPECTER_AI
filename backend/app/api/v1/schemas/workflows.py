"""Pydantic v2 request/response schemas for workflow endpoints (Phase 2/3)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.value_objects import (
    ScanStatus,
    ScheduleFrequency,
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

class CreateScheduleRequest(BaseModel):
    workflow_id: UUID
    frequency: ScheduleFrequency
    cron_expression: str | None = Field(default=None, max_length=100)


class ScheduleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    workflow_id: UUID
    project_id: UUID
    frequency: ScheduleFrequency
    cron_expression: str | None
    is_active: bool
    last_run_at: datetime | None
    next_run_at: datetime | None
    created_by: UUID | None
    created_at: datetime | None


class ScheduleListResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[ScheduleResponse]
