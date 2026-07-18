"""Unit tests for `app.application.auth_service`, using in-memory fakes."""

from __future__ import annotations

import pytest

from app.application.auth_service import (
    LoginService,
    LogoutAllService,
    LogoutService,
    RefreshTokenService,
    RegisterUserService,
)
from app.domain.exceptions import (
    EmailAlreadyRegisteredError,
    InactiveUserError,
    InvalidCredentialsError,
    InvalidRefreshTokenError,
)
from app.infrastructure.security.jwt import create_access_token, decode_access_token
from app.infrastructure.security.password_hasher import hash_password, verify_password
from app.infrastructure.security.tokens import generate_refresh_token, hash_refresh_token
from tests.fakes import FakeSessionRepository, FakeUserRepository


@pytest.fixture
def settings():
    from app.core.config import get_settings

    return get_settings()


@pytest.fixture
def user_repo() -> FakeUserRepository:
    return FakeUserRepository()


@pytest.fixture
def session_repo() -> FakeSessionRepository:
    return FakeSessionRepository()


def _make_login_service(user_repo, session_repo, settings) -> LoginService:
    return LoginService(
        user_repository=user_repo,
        session_repository=session_repo,
        verify_password=verify_password,
        create_access_token=lambda uid: create_access_token(uid, settings),
        generate_refresh_token=generate_refresh_token,
        hash_refresh_token=hash_refresh_token,
    )


@pytest.mark.asyncio
async def test_register_creates_user_with_hashed_password(user_repo):
    service = RegisterUserService(user_repo, hash_password)
    user = await service.execute("alice@example.com", "hunter22", "Alice")

    assert user.email == "alice@example.com"
    assert user.password_hash != "hunter22"
    assert verify_password("hunter22", user.password_hash)


@pytest.mark.asyncio
async def test_register_rejects_duplicate_email(user_repo):
    service = RegisterUserService(user_repo, hash_password)
    await service.execute("alice@example.com", "hunter22", "Alice")

    with pytest.raises(EmailAlreadyRegisteredError):
        await service.execute("alice@example.com", "different-pw", "Alice Two")


@pytest.mark.asyncio
async def test_login_succeeds_with_correct_credentials(user_repo, session_repo, settings):
    await RegisterUserService(user_repo, hash_password).execute(
        "bob@example.com", "correct-horse", "Bob"
    )
    login = _make_login_service(user_repo, session_repo, settings)

    result = await login.execute("bob@example.com", "correct-horse")

    assert result.user.email == "bob@example.com"
    payload = decode_access_token(result.access_token, settings)
    assert payload.sub == str(result.user.id)


@pytest.mark.asyncio
async def test_login_rejects_wrong_password(user_repo, session_repo, settings):
    await RegisterUserService(user_repo, hash_password).execute(
        "bob@example.com", "correct-horse", "Bob"
    )
    login = _make_login_service(user_repo, session_repo, settings)

    with pytest.raises(InvalidCredentialsError):
        await login.execute("bob@example.com", "wrong-password")


@pytest.mark.asyncio
async def test_login_rejects_unknown_email(user_repo, session_repo, settings):
    login = _make_login_service(user_repo, session_repo, settings)
    with pytest.raises(InvalidCredentialsError):
        await login.execute("nobody@example.com", "whatever")


@pytest.mark.asyncio
async def test_login_rejects_inactive_user(user_repo, session_repo, settings):
    user = await RegisterUserService(user_repo, hash_password).execute(
        "carol@example.com", "pw123456", "Carol"
    )
    user.is_active = False  # simulate a deactivated account

    login = _make_login_service(user_repo, session_repo, settings)
    with pytest.raises(InactiveUserError):
        await login.execute("carol@example.com", "pw123456")


@pytest.mark.asyncio
async def test_refresh_rotates_token_and_revokes_old_session(user_repo, session_repo, settings):
    await RegisterUserService(user_repo, hash_password).execute(
        "dave@example.com", "pw123456", "Dave"
    )
    login = _make_login_service(user_repo, session_repo, settings)
    login_result = await login.execute("dave@example.com", "pw123456")

    refresh_service = RefreshTokenService(
        user_repository=user_repo,
        session_repository=session_repo,
        create_access_token=lambda uid: create_access_token(uid, settings),
        generate_refresh_token=generate_refresh_token,
        hash_refresh_token=hash_refresh_token,
    )

    refreshed = await refresh_service.execute(login_result.refresh_token)
    assert refreshed.refresh_token != login_result.refresh_token

    # The OLD refresh token must now be rejected (session revoked on rotation).
    with pytest.raises(InvalidRefreshTokenError):
        await refresh_service.execute(login_result.refresh_token)

    # The NEW refresh token must still work.
    second_refresh = await refresh_service.execute(refreshed.refresh_token)
    assert second_refresh.access_token


@pytest.mark.asyncio
async def test_refresh_rejects_unknown_token(user_repo, session_repo, settings):
    refresh_service = RefreshTokenService(
        user_repository=user_repo,
        session_repository=session_repo,
        create_access_token=lambda uid: create_access_token(uid, settings),
        generate_refresh_token=generate_refresh_token,
        hash_refresh_token=hash_refresh_token,
    )
    with pytest.raises(InvalidRefreshTokenError):
        await refresh_service.execute("totally-made-up-token")


@pytest.mark.asyncio
async def test_logout_revokes_session_so_refresh_then_fails(user_repo, session_repo, settings):
    await RegisterUserService(user_repo, hash_password).execute(
        "erin@example.com", "pw123456", "Erin"
    )
    login = _make_login_service(user_repo, session_repo, settings)
    login_result = await login.execute("erin@example.com", "pw123456")

    logout_service = LogoutService(session_repo, hash_refresh_token)
    await logout_service.execute(login_result.refresh_token)

    refresh_service = RefreshTokenService(
        user_repository=user_repo,
        session_repository=session_repo,
        create_access_token=lambda uid: create_access_token(uid, settings),
        generate_refresh_token=generate_refresh_token,
        hash_refresh_token=hash_refresh_token,
    )
    with pytest.raises(InvalidRefreshTokenError):
        await refresh_service.execute(login_result.refresh_token)


@pytest.mark.asyncio
async def test_logout_all_revokes_every_session_for_user(user_repo, session_repo, settings):
    user = await RegisterUserService(user_repo, hash_password).execute(
        "frank@example.com", "pw123456", "Frank"
    )
    login = _make_login_service(user_repo, session_repo, settings)
    first = await login.execute("frank@example.com", "pw123456")
    second = await login.execute("frank@example.com", "pw123456")

    await LogoutAllService(session_repo).execute(user.id)

    refresh_service = RefreshTokenService(
        user_repository=user_repo,
        session_repository=session_repo,
        create_access_token=lambda uid: create_access_token(uid, settings),
        generate_refresh_token=generate_refresh_token,
        hash_refresh_token=hash_refresh_token,
    )
    with pytest.raises(InvalidRefreshTokenError):
        await refresh_service.execute(first.refresh_token)
    with pytest.raises(InvalidRefreshTokenError):
        await refresh_service.execute(second.refresh_token)
