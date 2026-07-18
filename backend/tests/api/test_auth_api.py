"""
API-layer tests for `/auth/*` endpoints.

These drive real HTTP requests through the FastAPI app (via
`ASGITransport`, no real network) but replace every repository-backed
dependency with the in-memory fakes from `tests.fakes` via
`app.dependency_overrides`. This proves the routing, request/response
schema validation, and error-handler wiring all work correctly, without
requiring a live Postgres — the DB-backed behavior itself is already
covered by `tests/unit/test_auth_service.py` (service layer, against
fakes) and the manually-verified end-to-end run against real Postgres.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.v1.deps import (
    get_audit_log_repository,
    get_login_service,
    get_logout_all_service,
    get_logout_service,
    get_refresh_service,
    get_register_service,
    get_user_repository,
)
from app.application.auth_service import (
    LoginService,
    LogoutAllService,
    LogoutService,
    RefreshTokenService,
    RegisterUserService,
)
from app.core.config import get_settings
from app.infrastructure.security.jwt import create_access_token
from app.infrastructure.security.password_hasher import hash_password, verify_password
from app.infrastructure.security.tokens import generate_refresh_token, hash_refresh_token
from app.main import create_app
from tests.fakes import FakeAuditLogRepository, FakeSessionRepository, FakeUserRepository


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    app = create_app()
    settings = get_settings()

    user_repo = FakeUserRepository()
    session_repo = FakeSessionRepository()
    audit_repo = FakeAuditLogRepository()

    app.dependency_overrides[get_register_service] = lambda: RegisterUserService(
        user_repo, hash_password
    )
    app.dependency_overrides[get_login_service] = lambda: LoginService(
        user_repository=user_repo,
        session_repository=session_repo,
        verify_password=verify_password,
        create_access_token=lambda uid: create_access_token(uid, settings),
        generate_refresh_token=generate_refresh_token,
        hash_refresh_token=hash_refresh_token,
    )
    app.dependency_overrides[get_refresh_service] = lambda: RefreshTokenService(
        user_repository=user_repo,
        session_repository=session_repo,
        create_access_token=lambda uid: create_access_token(uid, settings),
        generate_refresh_token=generate_refresh_token,
        hash_refresh_token=hash_refresh_token,
    )
    app.dependency_overrides[get_logout_service] = lambda: LogoutService(
        session_repo, hash_refresh_token
    )
    app.dependency_overrides[get_logout_all_service] = lambda: LogoutAllService(session_repo)
    app.dependency_overrides[get_audit_log_repository] = lambda: audit_repo
    app.dependency_overrides[get_user_repository] = lambda: user_repo

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_register_returns_201_and_user_body(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "alice@example.com",
            "password": "correct-horse-battery",
            "full_name": "Alice",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "alice@example.com"
    assert "password" not in body
    assert "password_hash" not in body


@pytest.mark.asyncio
async def test_register_duplicate_email_returns_409(client: AsyncClient) -> None:
    payload = {"email": "bob@example.com", "password": "correct-horse-battery", "full_name": "Bob"}
    await client.post("/api/v1/auth/register", json=payload)
    response = await client.post("/api/v1/auth/register", json=payload)

    assert response.status_code == 409
    body = response.json()
    assert body["type"] == "https://specter.ai/errors/email-already-registered"


@pytest.mark.asyncio
async def test_register_rejects_short_password(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": "carol@example.com", "password": "short", "full_name": "Carol"},
    )
    assert response.status_code == 422  # Pydantic min_length validation


@pytest.mark.asyncio
async def test_login_then_me_roundtrip(client: AsyncClient) -> None:
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "dave@example.com",
            "password": "correct-horse-battery",
            "full_name": "Dave",
        },
    )
    login_response = await client.post(
        "/api/v1/auth/login",
        json={"email": "dave@example.com", "password": "correct-horse-battery"},
    )
    assert login_response.status_code == 200
    tokens = login_response.json()
    assert "access_token" in tokens
    assert "refresh_token" in tokens

    me_response = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert me_response.status_code == 200
    assert me_response.json()["email"] == "dave@example.com"


@pytest.mark.asyncio
async def test_login_wrong_password_returns_401(client: AsyncClient) -> None:
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "erin@example.com",
            "password": "correct-horse-battery",
            "full_name": "Erin",
        },
    )
    response = await client.post(
        "/api/v1/auth/login", json={"email": "erin@example.com", "password": "wrong-password"}
    )
    assert response.status_code == 401
    assert response.json()["type"] == "https://specter.ai/errors/invalid-credentials"


@pytest.mark.asyncio
async def test_me_without_token_returns_401(client: AsyncClient) -> None:
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_me_with_garbage_token_returns_401(client: AsyncClient) -> None:
    response = await client.get(
        "/api/v1/auth/me", headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_refresh_rotates_and_old_token_then_fails(client: AsyncClient) -> None:
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "frank@example.com",
            "password": "correct-horse-battery",
            "full_name": "Frank",
        },
    )
    login_response = await client.post(
        "/api/v1/auth/login",
        json={"email": "frank@example.com", "password": "correct-horse-battery"},
    )
    old_refresh = login_response.json()["refresh_token"]

    refresh_response = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": old_refresh}
    )
    assert refresh_response.status_code == 200
    assert refresh_response.json()["refresh_token"] != old_refresh

    replay_response = await client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert replay_response.status_code == 401


@pytest.mark.asyncio
async def test_logout_then_refresh_fails(client: AsyncClient) -> None:
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "grace@example.com",
            "password": "correct-horse-battery",
            "full_name": "Grace",
        },
    )
    login_response = await client.post(
        "/api/v1/auth/login",
        json={"email": "grace@example.com", "password": "correct-horse-battery"},
    )
    refresh_token = login_response.json()["refresh_token"]

    logout_response = await client.post(
        "/api/v1/auth/logout", json={"refresh_token": refresh_token}
    )
    assert logout_response.status_code == 204

    refresh_response = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": refresh_token}
    )
    assert refresh_response.status_code == 401
