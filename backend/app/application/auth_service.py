"""
Authentication use-case services (SRS §2.1, §16.1).

Each class is a single use case (Register / Login / RefreshToken /
Logout), taking its repository dependencies through the constructor
(Dependency Inversion — depends on `domain.repositories` Protocols, not
concrete SQLAlchemy classes). Password hashing and JWT/refresh-token
crypto are injected as plain functions so this module has zero
knowledge of argon2/PyJWT internals.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from app.domain.entities import Session as SessionEntity
from app.domain.entities import User
from app.domain.exceptions import (
    EmailAlreadyRegisteredError,
    InactiveUserError,
    InvalidCredentialsError,
    InvalidRefreshTokenError,
)
from app.domain.repositories import SessionRepository, UserRepository

REFRESH_TOKEN_TTL_DAYS = 30

HashPasswordFn = Callable[[str], str]
VerifyPasswordFn = Callable[[str, str], bool]
CreateAccessTokenFn = Callable[[UUID], str]
GenerateRefreshTokenFn = Callable[[], str]
HashRefreshTokenFn = Callable[[str], str]


@dataclass(frozen=True, slots=True)
class LoginResult:
    user: User
    access_token: str
    refresh_token: str


@dataclass(frozen=True, slots=True)
class RefreshResult:
    user: User
    access_token: str
    refresh_token: str


class RegisterUserService:
    """
    Creates a new user account.

    The frozen SRS's API table (§6.2) doesn't list a public
    self-registration endpoint, but one is required to bootstrap any
    user at all. `POST /auth/register` in the API layer is a Milestone-2
    addition made for exactly that reason — see its docstring for the
    same note.
    """

    def __init__(self, user_repository: UserRepository, hash_password: HashPasswordFn) -> None:
        self._users = user_repository
        self._hash_password = hash_password

    async def execute(self, email: str, password: str, full_name: str | None) -> User:
        existing = await self._users.get_by_email(email)
        if existing is not None:
            raise EmailAlreadyRegisteredError(email)

        user = User(
            id=uuid4(),
            email=email,
            password_hash=self._hash_password(password),
            full_name=full_name,
            is_active=True,
            created_at=datetime.now(UTC),
        )
        await self._users.add(user)
        return user


class LoginService:
    def __init__(
        self,
        user_repository: UserRepository,
        session_repository: SessionRepository,
        verify_password: VerifyPasswordFn,
        create_access_token: CreateAccessTokenFn,
        generate_refresh_token: GenerateRefreshTokenFn,
        hash_refresh_token: HashRefreshTokenFn,
    ) -> None:
        self._users = user_repository
        self._sessions = session_repository
        self._verify_password = verify_password
        self._create_access_token = create_access_token
        self._generate_refresh_token = generate_refresh_token
        self._hash_refresh_token = hash_refresh_token

    async def execute(
        self,
        email: str,
        password: str,
        *,
        user_agent: str | None = None,
        ip_address: str | None = None,
    ) -> LoginResult:
        user = await self._users.get_by_email(email)
        if user is None or not self._verify_password(password, user.password_hash):
            raise InvalidCredentialsError()
        if not user.is_active:
            raise InactiveUserError(user.id)

        refresh_token = self._generate_refresh_token()
        session = SessionEntity(
            id=uuid4(),
            user_id=user.id,
            refresh_token_hash=self._hash_refresh_token(refresh_token),
            user_agent=user_agent,
            ip_address=ip_address,
            expires_at=datetime.now(UTC) + timedelta(days=REFRESH_TOKEN_TTL_DAYS),
            created_at=datetime.now(UTC),
        )
        await self._sessions.add(session)

        access_token = self._create_access_token(user.id)
        return LoginResult(user=user, access_token=access_token, refresh_token=refresh_token)


class RefreshTokenService:
    """
    Rotates refresh tokens on every use (SRS §16.1): the presented
    token's session is revoked and a brand new session/token pair is
    issued, so a leaked-then-replayed old token is a detectable replay
    (its session will already show as revoked).
    """

    def __init__(
        self,
        user_repository: UserRepository,
        session_repository: SessionRepository,
        create_access_token: CreateAccessTokenFn,
        generate_refresh_token: GenerateRefreshTokenFn,
        hash_refresh_token: HashRefreshTokenFn,
    ) -> None:
        self._users = user_repository
        self._sessions = session_repository
        self._create_access_token = create_access_token
        self._generate_refresh_token = generate_refresh_token
        self._hash_refresh_token = hash_refresh_token

    async def execute(self, presented_refresh_token: str) -> RefreshResult:
        token_hash = self._hash_refresh_token(presented_refresh_token)
        session = await self._sessions.get_by_token_hash(token_hash)

        now = datetime.now(UTC)
        if session is None or not session.is_valid_at(now):
            raise InvalidRefreshTokenError()

        user = await self._users.get_by_id(session.user_id)
        if user is None or not user.is_active:
            raise InvalidRefreshTokenError()

        # Rotate: revoke the presented session, issue a fresh one.
        await self._sessions.revoke(session.id)

        new_refresh_token = self._generate_refresh_token()
        new_session = SessionEntity(
            id=uuid4(),
            user_id=user.id,
            refresh_token_hash=self._hash_refresh_token(new_refresh_token),
            user_agent=session.user_agent,
            ip_address=session.ip_address,
            expires_at=now + timedelta(days=REFRESH_TOKEN_TTL_DAYS),
            created_at=now,
        )
        await self._sessions.add(new_session)

        access_token = self._create_access_token(user.id)
        return RefreshResult(user=user, access_token=access_token, refresh_token=new_refresh_token)


class LogoutService:
    """Revokes a single session (the one behind the presented refresh token)."""

    def __init__(
        self, session_repository: SessionRepository, hash_refresh_token: HashRefreshTokenFn
    ) -> None:
        self._sessions = session_repository
        self._hash_refresh_token = hash_refresh_token

    async def execute(self, presented_refresh_token: str) -> None:
        token_hash = self._hash_refresh_token(presented_refresh_token)
        session = await self._sessions.get_by_token_hash(token_hash)
        if session is not None:
            await self._sessions.revoke(session.id)


class LogoutAllService:
    """Revokes every session for a user ("log out all devices", SRS §16.1)."""

    def __init__(self, session_repository: SessionRepository) -> None:
        self._sessions = session_repository

    async def execute(self, user_id: UUID) -> None:
        await self._sessions.revoke_all_for_user(user_id)
