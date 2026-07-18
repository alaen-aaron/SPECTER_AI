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
from datetime import datetime
from functools import lru_cache

from sqlalchemy import DateTime
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import Settings, get_settings


class Base(DeclarativeBase):
    """
    Declarative base shared by every ORM model in the application.

    `type_annotation_map` makes every bare `Mapped[datetime]` column
    map to a timezone-aware `TIMESTAMPTZ` by default, matching SRS §5.2
    (every DDL example uses `TIMESTAMPTZ`) and matching the domain
    layer, which always produces `datetime.now(timezone.utc)` — never
    naive datetimes. Without this, SQLAlchemy's default is a naive
    `TIMESTAMP`, which raises at the driver level the moment a
    timezone-aware Python datetime is bound to it.
    """

    type_annotation_map = {datetime: DateTime(timezone=True)}


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

    Implements a request-scoped unit-of-work: commits automatically if
    the request handler completes without raising, and rolls back if it
    does. Repositories call `flush()` (to populate generated ids/
    defaults for use later in the same request) but never `commit()`
    themselves — `domain`/`application` stay storage-transaction-agnostic,
    and the transaction boundary lives at the one place that actually
    knows whether the whole request succeeded: this dependency.
    """
    session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


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
