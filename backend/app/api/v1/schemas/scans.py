"""Pydantic v2 request/response schemas for scan endpoints (Milestone 3)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.value_objects import ScanStatus


class CreateScanRequest(BaseModel):
    plugin: str = Field(examples=["nmap"])
    plugin_config: dict[str, Any] = Field(
        default_factory=dict,
        examples=[{"target": "10.10.10.5", "ports": "1-1000", "arguments": ["-sV"]}],
    )
    target_ids: list[UUID] = Field(min_length=1)


class ScanResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    project_id: UUID
    initiated_by: UUID
    plugin: str
    status: ScanStatus
    target_ids: list[UUID]
    plugin_config: dict[str, Any]
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    logs_path: str | None
    artifacts_path: str | None
    exit_code: int | None
    error_message: str | None


class PaginatedScanResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[ScanResponse]
    next_cursor: datetime | None = None
