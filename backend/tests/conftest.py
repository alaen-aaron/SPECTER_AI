"""
Shared pytest fixtures.

Milestone 1 only needs an HTTP client against the app factory. Database
fixtures (test transaction rollback, etc.) are added in Milestone 2 once
models exist.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import create_app


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """An HTTPX async client bound directly to the ASGI app (no network)."""
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> AsyncIterator[None]:
    """Ensure each test sees a fresh Settings instance if env vars change."""
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
