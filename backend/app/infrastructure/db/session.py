"""
Async SQLAlchemy 2.0 engine and session factory.

This module owns exactly one thing: producing database sessions. No
model definitions, no repository logic, no business rules — those
belong to `infrastructure/db/models/` and `infrastructure/db/repositories/`
starting in Milestone 2, once the SRS §5 schema is implemented.

The `Base` declarative class also lives here since every future model
module will import it; keeping it out of `models/` avoids a circular
import once models start referencing each other.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import Settings, get_settings


class Base(DeclarativeBase):
    """Declarative base shared by every ORM model in the application."""


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    """
    Return the process-wide async engine singleton.

    Cached so we never open more than one connection pool per process.
    Pool sizing is deliberately conservative here; production tuning is
    a Phase 6 (Enterprise Hardening) concern per the SRS roadmap.
    """
    settings: Settings = get_settings()
    return create_async_engine(
        str(settings.DATABASE_URL),
        echo=settings.is_local,
        pool_pre_ping=True,
        future=True,
    )


@lru_cache(maxsize=1)
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the process-wide async session factory singleton."""
    return async_sessionmaker(
        bind=get_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency yielding a request-scoped async session.

    Usage: ``session: AsyncSession = Depends(get_db_session)``. The
    session is closed automatically at the end of the request; callers
    are responsible for committing (use-case services own transaction
    boundaries per SRS §10.1 — this dependency does not auto-commit).
    """
    session_factory = get_session_factory()
    async with session_factory() as session:
        yield session


async def check_database_connectivity() -> bool:
    """
    Lightweight connectivity probe used by the health endpoint.

    Returns True if a trivial query succeeds, False otherwise. Never
    raises — callers should treat this as a boolean health signal.
    """
    from sqlalchemy import text

    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001 - health check must never raise
        return False
