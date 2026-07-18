"""SQLAlchemy implementation of `AuditLogRepository` (SRS §16.5)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as SqlAsyncSession

from app.domain.entities import AuditLogEntry
from app.infrastructure.db.models.audit_log import AuditLogModel


def _entry_to_entity(row: AuditLogModel) -> AuditLogEntry:
    return AuditLogEntry(
        id=row.id,
        organization_id=row.organization_id,
        actor_id=row.actor_id,
        action=row.action,
        target_type=row.target_type,
        target_id=row.target_id,
        ip_address=str(row.ip_address) if row.ip_address else None,
        created_at=row.created_at,
        before_state=dict(row.before_state or {}),
        after_state=dict(row.after_state or {}),
    )


class SqlAlchemyAuditLogRepository:
    """Satisfies `app.domain.repositories.AuditLogRepository` structurally."""

    def __init__(self, session: SqlAsyncSession) -> None:
        self._session = session

    async def add(self, entry: AuditLogEntry) -> None:
        model = AuditLogModel(
            id=entry.id,
            organization_id=entry.organization_id,
            actor_id=entry.actor_id,
            action=entry.action,
            target_type=entry.target_type,
            target_id=entry.target_id,
            before_state=entry.before_state or None,
            after_state=entry.after_state or None,
            ip_address=entry.ip_address,
        )
        self._session.add(model)
        await self._session.flush()

    async def list_for_organization(self, organization_id: UUID) -> list[AuditLogEntry]:
        stmt = (
            select(AuditLogModel)
            .where(AuditLogModel.organization_id == organization_id)
            .order_by(AuditLogModel.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return [_entry_to_entity(row) for row in result.scalars().all()]
