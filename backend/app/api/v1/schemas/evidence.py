"""Pydantic v2 request/response schemas for evidence endpoints."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.domain.value_objects import EvidenceType


class EvidenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    finding_id: UUID
    evidence_type: EvidenceType
    storage_pointer: str
    content_hash: str
    collected_by: UUID
    collected_at: datetime
    filename: str | None
    file_size: int | None
    created_at: datetime | None


class PaginatedEvidenceResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[EvidenceResponse]
