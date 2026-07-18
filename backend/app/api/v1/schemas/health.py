"""Pydantic v2 request/response schemas for GET /api/v1/health."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ComponentStatus(BaseModel):
    """Health status of a single backing service (e.g. database)."""

    model_config = ConfigDict(frozen=True)

    name: str
    healthy: bool


class HealthResponse(BaseModel):
    """Response body for GET /api/v1/health."""

    model_config = ConfigDict(frozen=True)

    status: str
    app_name: str
    environment: str
    components: list[ComponentStatus]
