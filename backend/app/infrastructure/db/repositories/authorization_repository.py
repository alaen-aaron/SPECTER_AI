"""SQLAlchemy implementation of `AuthorizationRecordRepository`."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as SqlAsyncSession

from app.domain.entities import AuthorizationRecord
from app.domain.value_objects import AuthorizationStatus
from app.infrastructure.db.models.authorization import AuthorizationRecordModel


def _record_to_entity(row: AuthorizationRecordModel) -> AuthorizationRecord:
    return AuthorizationRecord(
        id=row.id,
        project_id=row.project_id,
        client_name=row.client_name,
        document_reference=row.document_reference,
        authorized_from=row.authorized_from,
        authorized_to=row.authorized_to,
        allowed_targets=list(row.allowed_targets or []),
        approved_by=row.approved_by,
        status=AuthorizationStatus(row.status),
        scope_notes=row.scope_notes,
        evidence_pointer=row.evidence_pointer,
        created_at=row.created_at,
    )


class SqlAlchemyAuthorizationRecordRepository:
    """Satisfies `app.domain.repositories.AuthorizationRecordRepository` structurally."""

    def __init__(self, session: SqlAsyncSession) -> None:
        self._session = session

    async def get_by_id(self, record_id: UUID) -> AuthorizationRecord | None:
        row = await self._session.get(AuthorizationRecordModel, record_id)
        return _record_to_entity(row) if row else None

    async def get_active_for_project(
        self, project_id: UUID, on_date: datetime
    ) -> AuthorizationRecord | None:
        """
        Returns the record covering `on_date`, if any.

        The final "is this actually valid right now" decision still
        goes through `AuthorizationRecord.is_active_on()` in the domain
        entity, not here — this query is a candidate lookup, not the
        authority on validity, matching SRS §16.3's requirement that
        expired-but-not-yet-flipped records never grant scope.
        """
        stmt = select(AuthorizationRecordModel).where(
            AuthorizationRecordModel.project_id == project_id,
            AuthorizationRecordModel.status == AuthorizationStatus.ACTIVE.value,
            AuthorizationRecordModel.authorized_from <= on_date.date(),
            AuthorizationRecordModel.authorized_to >= on_date.date(),
        )
        result = await self._session.execute(stmt)
        row = result.scalars().first()
        return _record_to_entity(row) if row else None

    async def list_for_project(self, project_id: UUID) -> list[AuthorizationRecord]:
        stmt = select(AuthorizationRecordModel).where(
            AuthorizationRecordModel.project_id == project_id
        )
        result = await self._session.execute(stmt)
        return [_record_to_entity(row) for row in result.scalars().all()]

    async def add(self, record: AuthorizationRecord) -> None:
        model = AuthorizationRecordModel(
            id=record.id,
            project_id=record.project_id,
            client_name=record.client_name,
            document_reference=record.document_reference,
            authorized_from=record.authorized_from,
            authorized_to=record.authorized_to,
            allowed_targets=record.allowed_targets,
            approved_by=record.approved_by,
            status=record.status.value,
            scope_notes=record.scope_notes,
            evidence_pointer=record.evidence_pointer,
        )
        self._session.add(model)
        await self._session.flush()
