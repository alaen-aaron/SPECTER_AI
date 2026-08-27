"""Pydantic v2 request/response schemas for autonomous orchestration (M7.4)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.value_objects import ActionCategory, AutonomousRunStatus


class CreateAutonomousRunRequest(BaseModel):
    objective: str = Field(default="", examples=["Reconnaissance scan of Juice Shop"])
    max_actions: int = Field(default=20, ge=1, le=50)
    max_runtime_seconds: int = Field(default=1800, ge=60, le=7200)


class AutonomousRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    project_id: UUID
    initiated_by: UUID
    status: AutonomousRunStatus
    objective: str
    max_actions: int
    max_runtime_seconds: int
    current_cycle: int
    actions_completed: int
    approval_policy: str
    started_at: datetime | None
    completed_at: datetime | None
    last_heartbeat_at: datetime | None
    error_message: str | None
    result_summary: dict[str, Any]
    created_at: datetime | None


class PaginatedAutonomousRunResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[AutonomousRunResponse]
    next_cursor: datetime | None = None


class AutonomousRunActionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    run_id: UUID
    project_id: UUID
    cycle: int
    action_type: str
    plugin: str | None
    title: str
    description: str
    justification: str
    plugin_config: dict[str, Any]
    target_ids: list[UUID]
    category: ActionCategory
    status: str
    approved_by: UUID | None
    approved_at: datetime | None
    rejection_reason: str | None
    scan_id: UUID | None
    result_summary: dict[str, Any]
    created_at: datetime | None


class ApproveActionRequest(BaseModel):
    reason: str = Field(default="")


class RejectActionRequest(BaseModel):
    reason: str = Field(min_length=1)


class TransitionRunRequest(BaseModel):
    """Request body for state transitions (start_planning, plan_complete, etc.)."""

    pass
