"""
Fixtures for repository-layer integration tests.

Unlike `tests/unit/` and `tests/api/` (which run entirely against
in-memory fakes and need no external services), these tests exercise
the actual SQLAlchemy repository implementations against a real
Postgres — verifying things fakes cannot: column types (CITEXT
case-insensitivity, JSONB round-tripping), cascade deletes, unique
constraints, and actual SQL correctness.

If `DATABASE_URL` isn't reachable (e.g. running `pytest` on a laptop
without `docker compose up postgres`), every test in this package is
skipped rather than erroring — these are opt-in integration tests, not
part of the default fast unit-test loop.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings


def _database_reachable() -> bool:
    """
    Uses a throwaway engine, not the process-wide cached `get_engine()`
    singleton — this check runs inside its own short-lived event loop
    (via `asyncio.run`), and asyncpg connections are bound to the loop
    that created them. Touching the shared, `lru_cache`d engine here
    would poison its connection pool for every subsequent test's event
    loop (pytest-asyncio's own), causing a confusing "Event loop is
    closed" failure far from this function.
    """

    async def _check() -> bool:
        engine = create_async_engine(str(get_settings().DATABASE_URL))
        try:
            async with engine.connect():
                return True
        except Exception:  # noqa: BLE001 - any connectivity failure means "skip"
            return False
        finally:
            await engine.dispose()

    return asyncio.run(_check())


requires_postgres = pytest.mark.skipif(
    not _database_reachable(),
    reason="No reachable Postgres for DATABASE_URL — run `docker compose up postgres` "
    "(or a local instance) to enable repository integration tests.",
)


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """
    A real, transactional session that's rolled back at the end of
    every test — each test starts from a clean slate without needing
    to TRUNCATE tables manually.

    Deliberately builds its OWN engine/sessionmaker rather than reusing
    the process-wide cached `get_engine()`/`get_session_factory()`
    singletons: asyncpg connections are bound to the event loop that
    created them, and pytest-asyncio gives each test function a fresh
    loop, so sharing the cached engine across tests raises "Event loop
    is closed" the moment a second test tries to reuse a pooled
    connection from the first test's already-closed loop.
    """
    engine = create_async_engine(str(get_settings().DATABASE_URL))
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    async with session_factory() as session:
        yield session
        await session.rollback()

    await engine.dispose()
