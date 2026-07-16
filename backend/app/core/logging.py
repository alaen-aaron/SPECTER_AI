"""
Logging configuration.

Sets up ``structlog`` bound to stdlib ``logging``, so every log emitted
by our code, uvicorn, and SQLAlchemy flows through one pipeline and one
output format. Production emits single-line JSON (for OTLP/log-collector
ingestion per SRS §12.4); local/dev emits a human-readable console
renderer.

This module is imported once, at application startup, via
``configure_logging()``. Nothing else in the codebase should call
``logging.basicConfig`` directly.
"""

from __future__ import annotations

import logging
import sys
from typing import cast

import structlog

from app.core.config import AppEnvironment, Settings


def configure_logging(settings: Settings) -> None:
    """Configure stdlib logging + structlog for the current process."""
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if settings.APP_ENV in (AppEnvironment.LOCAL, AppEnvironment.TEST):
        renderer: structlog.types.Processor = structlog.dev.ConsoleRenderer()
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=shared_processors,
    )

    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        handler.setFormatter(formatter)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger for the given module name."""
    return cast(structlog.stdlib.BoundLogger, structlog.get_logger(name))
