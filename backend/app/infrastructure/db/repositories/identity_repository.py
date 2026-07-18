"""
SQLAlchemy implementations of `UserRepository`/`SessionRepository`.

Each method maps between `UserModel`/`SessionModel` (ORM) and
`User`/`Session` (domain entities) — the application layer never sees
a SQLAlchemy row.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession as SqlAsyncSession

from app.domain.entities import Session as SessionEntity
from app.domain.entities import User
from app.infrastructure.db.models.identity import SessionModel, UserModel


def _user_to_entity(row: UserModel) -> User:
    return User(
        id=row.id,
        email=str(row.email),
        password_hash=row.password_hash,
        full_name=row.full_name,
        is_active=row.is_active,
        created_at=row.created_at,
    )


def _session_to_entity(row: SessionModel) -> SessionEntity:
    return SessionEntity(
        id=row.id,
        user_id=row.user_id,
        refresh_token_hash=row.refresh_token_hash,
        user_agent=row.user_agent,
        ip_address=str(row.ip_address) if row.ip_address else None,
        expires_at=row.expires_at,
        created_at=row.created_at,
        revoked_at=row.revoked_at,
    )


class SqlAlchemyUserRepository:
    """Satisfies `app.domain.repositories.UserRepository` structurally."""

    def __init__(self, session: SqlAsyncSession) -> None:
        self._session = session

    async def get_by_id(self, user_id: UUID) -> User | None:
        row = await self._session.get(UserModel, user_id)
        return _user_to_entity(row) if row else None

    async def get_by_email(self, email: str) -> User | None:
        stmt = select(UserModel).where(UserModel.email == email)
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        return _user_to_entity(row) if row else None

    async def add(self, user: User) -> None:
        model = UserModel(
            id=user.id,
            email=user.email,
            password_hash=user.password_hash,
            full_name=user.full_name,
            is_active=user.is_active,
        )
        self._session.add(model)
        await self._session.flush()


class SqlAlchemySessionRepository:
    """Satisfies `app.domain.repositories.SessionRepository` structurally."""

    def __init__(self, session: SqlAsyncSession) -> None:
        self._session = session

    async def add(self, session: SessionEntity) -> None:
        model = SessionModel(
            id=session.id,
            user_id=session.user_id,
            refresh_token_hash=session.refresh_token_hash,
            user_agent=session.user_agent,
            ip_address=session.ip_address,
            expires_at=session.expires_at,
        )
        self._session.add(model)
        await self._session.flush()

    async def get_by_id(self, session_id: UUID) -> SessionEntity | None:
        row = await self._session.get(SessionModel, session_id)
        return _session_to_entity(row) if row else None

    async def get_by_token_hash(self, token_hash: str) -> SessionEntity | None:
        stmt = select(SessionModel).where(SessionModel.refresh_token_hash == token_hash)
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        return _session_to_entity(row) if row else None

    async def revoke(self, session_id: UUID) -> None:
        stmt = (
            update(SessionModel)
            .where(SessionModel.id == session_id)
            .values(revoked_at=datetime.now(UTC))
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def revoke_all_for_user(self, user_id: UUID) -> None:
        stmt = (
            update(SessionModel)
            .where(SessionModel.user_id == user_id, SessionModel.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        await self._session.execute(stmt)
        await self._session.flush()
