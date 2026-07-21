"""Pydantic v2 request/response schemas for finding endpoints."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.domain.value_objects import FindingStatus, Severity


class FindingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    project_id: UUID
    title: str
    severity: Severity
    status: FindingStatus
    description: str | None
    asset_id: UUID | None
    cvss_score: float | None
    dedup_key: str
    tool_result_ids: list[UUID]
    created_at: datetime | None


class UpdateFindingStatusRequest(BaseModel):
    status: FindingStatus


class PaginatedFindingResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[FindingResponse]
    next_cursor: datetime | None = None
