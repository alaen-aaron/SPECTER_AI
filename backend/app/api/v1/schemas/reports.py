"""Pydantic v2 request/response schemas for report endpoints."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CreateReportRequest(BaseModel):
    title: str = Field(min_length=1)


class ReportResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    project_id: UUID
    title: str
    status: str
    created_at: datetime | None


class ReportVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    report_id: UUID
    version_number: int
    file_pointer: str
    is_redacted: bool
    generated_by: UUID
    generated_at: datetime
    created_at: datetime | None
