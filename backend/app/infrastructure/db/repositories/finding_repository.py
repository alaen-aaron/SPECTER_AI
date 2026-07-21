"""SQLAlchemy implementation of `FindingRepository` (Milestone 4C)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as SqlAsyncSession

from app.domain.entities import Finding
from app.domain.value_objects import FindingStatus, Severity
from app.infrastructure.db.models.finding import FindingModel


def _to_entity(row: FindingModel) -> Finding:
    return Finding(
        id=row.id,
        project_id=row.project_id,
        title=row.title,
        severity=Severity(row.severity),
        status=FindingStatus(row.status),
        description=row.description,
        asset_id=row.asset_id,
        cvss_score=float(row.cvss_score) if row.cvss_score is not None else None,
        dedup_key=row.dedup_key or "",
        created_at=row.created_at,
    )


class SqlAlchemyFindingRepository:
    def __init__(self, session: SqlAsyncSession) -> None:
        self._session = session

    async def add(self, finding: Finding) -> None:
        model = FindingModel(
            id=finding.id,
            project_id=finding.project_id,
            title=finding.title,
            description=finding.description,
            severity=finding.severity.value,
            status=finding.status.value,
            cvss_score=finding.cvss_score,
            dedup_key=finding.dedup_key,
            asset_id=finding.asset_id,
        )
        self._session.add(model)
        await self._session.flush()

    async def get(self, finding_id: UUID) -> Finding | None:
        row = await self._session.get(FindingModel, finding_id)
        return _to_entity(row) if row else None

    async def list_for_project(
        self,
        project_id: UUID,
        severity: Severity | None = None,
        limit: int = 20,
        cursor: datetime | None = None,
    ) -> list[Finding]:
        stmt = select(FindingModel).where(FindingModel.project_id == project_id)
        if severity is not None:
            stmt = stmt.where(FindingModel.severity == severity.value)
        if cursor is not None:
            stmt = stmt.where(FindingModel.created_at < cursor)
        stmt = stmt.order_by(FindingModel.created_at.desc()).limit(limit + 1)
        result = await self._session.execute(stmt)
        return [_to_entity(row) for row in result.scalars().all()]

    async def get_by_dedup_key(
        self, project_id: UUID, dedup_key: str
    ) -> Finding | None:
        stmt = select(FindingModel).where(
            FindingModel.project_id == project_id,
            FindingModel.dedup_key == dedup_key,
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        return _to_entity(row) if row else None

    async def update_status(self, finding_id: UUID, status: FindingStatus) -> None:
        from sqlalchemy import update

        stmt = (
            update(FindingModel)
            .where(FindingModel.id == finding_id)
            .values(status=status.value)
        )
        await self._session.execute(stmt)
        await self._session.flush()
