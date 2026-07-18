"""
FastAPI application entrypoint.

Uses the application-factory pattern (`create_app()`) rather than a
module-level `app` built from side effects, so tests can construct
independent app instances with overridden dependencies.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.error_handlers import register_error_handlers
from app.api.v1.router import api_v1_router
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application startup/shutdown hooks."""
    settings = get_settings()
    configure_logging(settings)
    logger.info("app_startup", app_name=settings.APP_NAME, environment=settings.APP_ENV.value)
    yield
    logger.info("app_shutdown")


def create_app() -> FastAPI:
    """Build and configure the FastAPI application instance."""
    settings: Settings = get_settings()

    app = FastAPI(
        title=settings.APP_NAME,
        description="Autonomous Offensive Security Platform — API",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ALLOW_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_v1_router, prefix=settings.API_V1_PREFIX)
    register_error_handlers(app)

    return app


app = create_app()
