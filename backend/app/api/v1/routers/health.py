"""
Health check endpoint.

Deliberately dependency-light: it reports process liveness plus best-
effort connectivity checks for database, Redis, and the plugin registry.
It must never raise — a health endpoint that fails to answer is worse
than one that reports `unhealthy`.

Milestone 5.5 Phase 4: added Redis, plugin registry, and normalizer
registry health checks.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.v1.schemas.health import ComponentStatus, HealthResponse
from app.core.config import Settings, get_settings
from app.infrastructure.db.session import check_database_connectivity

router = APIRouter(tags=["health"])


async def _check_redis() -> bool:
    """Best-effort Redis PING.  Returns False on any failure."""
    try:
        import redis.asyncio as aioredis  # type: ignore[import-untyped]

        settings = get_settings()
        async with aioredis.from_url(str(settings.REDIS_URL), socket_timeout=2) as client:
            return await client.ping()  # type: ignore[no-any-return]
    except Exception:  # noqa: BLE001 - health check must never raise
        return False


def _check_plugins() -> bool:
    """Check that at least one plugin is registered."""
    try:
        from app.plugins.registry import registry

        return len(registry.list()) > 0
    except Exception:  # noqa: BLE001
        return False


def _check_normalizers() -> bool:
    """Check that at least one normalizer is registered."""
    try:
        from app.plugins.normalizer_registry import normalizer_registry

        return len(normalizer_registry.list()) > 0
    except Exception:  # noqa: BLE001
        return False


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
    redis_healthy = await _check_redis()
    plugins_healthy = _check_plugins()
    normalizers_healthy = _check_normalizers()

    components = [
        ComponentStatus(name="database", healthy=db_healthy),
        ComponentStatus(name="redis", healthy=redis_healthy),
        ComponentStatus(name="plugins", healthy=plugins_healthy),
        ComponentStatus(name="normalizers", healthy=normalizers_healthy),
    ]
    overall_status = "ok" if all(c.healthy for c in components) else "degraded"

    return HealthResponse(
        status=overall_status,
        app_name=settings.APP_NAME,
        environment=settings.APP_ENV.value,
        components=components,
    )
