"""Pydantic v2 response schemas for asset inventory endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.domain.value_objects import AssetType


class AssetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    project_id: UUID
    asset_type: AssetType
    value: str
    first_seen: datetime
    last_seen: datetime
    in_scope: bool
    source_scan_id: UUID | None
    metadata: dict[str, Any]
    created_at: datetime | None


class PaginatedAssetResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[AssetResponse]
    next_cursor: datetime | None = None
