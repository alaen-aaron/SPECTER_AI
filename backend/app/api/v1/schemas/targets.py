"""Pydantic v2 request/response schemas for target endpoints."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.value_objects import TargetType


class CreateTargetRequest(BaseModel):
    value: str = Field(min_length=1, max_length=255, examples=["10.10.10.0/24"])
    target_type: TargetType = Field(examples=[TargetType.CIDR])


class UpdateTargetRequest(BaseModel):
    value: str | None = Field(default=None, min_length=1, max_length=255)
    target_type: TargetType | None = None
    in_scope: bool | None = None


class TargetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    project_id: UUID
    value: str
    target_type: TargetType
    in_scope: bool
    created_at: datetime
    updated_at: datetime
