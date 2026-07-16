"""Tests for GET /api/v1/health."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_returns_200(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_health_response_shape(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health")
    body = response.json()

    assert body["app_name"] == "SPECTER_AI"
    assert body["status"] in {"ok", "degraded"}
    assert isinstance(body["components"], list)
    assert any(c["name"] == "database" for c in body["components"])
