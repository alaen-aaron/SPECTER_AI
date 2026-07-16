"""
Health check endpoint.

Deliberately dependency-light: it reports process liveness plus a best-
effort database connectivity check. It must never raise — a health
endpoint that fails to answer is worse than one that reports `unhealthy`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.v1.schemas import ComponentStatus, HealthResponse
from app.core.config import Settings, get_settings
from app.infrastructure.db.session import check_database_connectivity

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness and dependency health check",
)
async def get_health(
    settings: Settings = Depends(get_settings),
) -> HealthResponse:
    """Return process status plus the health of backing services."""
    db_healthy = await check_database_connectivity()

    components = [ComponentStatus(name="database", healthy=db_healthy)]
    overall_status = "ok" if all(c.healthy for c in components) else "degraded"

    return HealthResponse(
        status=overall_status,
        app_name=settings.APP_NAME,
        environment=settings.APP_ENV.value,
        components=components,
    )
