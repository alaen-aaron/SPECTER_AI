"""SQLAlchemy implementation of `EvidenceRepository` (SRS §2.9)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as SqlAsyncSession

from app.domain.entities import Evidence
from app.domain.value_objects import EvidenceType
from app.infrastructure.db.models.evidence import EvidenceModel


def _to_entity(row: EvidenceModel) -> Evidence:
    return Evidence(
        id=row.id,
        finding_id=row.finding_id,
        evidence_type=EvidenceType(row.evidence_type),
        storage_pointer=row.storage_pointer,
        content_hash=row.content_hash,
        collected_by=row.collected_by,
        collected_at=row.collected_at,
        filename=row.filename,
        file_size=row.file_size,
        created_at=row.created_at,
    )


class SqlAlchemyEvidenceRepository:
    def __init__(self, session: SqlAsyncSession) -> None:
        self._session = session

    async def add(self, evidence: Evidence) -> None:
        model = EvidenceModel(
            id=evidence.id,
            finding_id=evidence.finding_id,
            evidence_type=evidence.evidence_type.value,
            storage_pointer=evidence.storage_pointer,
            content_hash=evidence.content_hash,
            collected_by=evidence.collected_by,
            collected_at=evidence.collected_at,
            filename=evidence.filename,
            file_size=evidence.file_size,
        )
        self._session.add(model)
        await self._session.flush()

    async def get(self, evidence_id: UUID) -> Evidence | None:
        row = await self._session.get(EvidenceModel, evidence_id)
        return _to_entity(row) if row else None

    async def list_for_finding(self, finding_id: UUID) -> list[Evidence]:
        stmt = (
            select(EvidenceModel)
            .where(EvidenceModel.finding_id == finding_id)
            .order_by(EvidenceModel.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return [_to_entity(row) for row in result.scalars().all()]

    async def list_for_project(self, project_id: UUID) -> list[Evidence]:
        from app.infrastructure.db.models.finding import FindingModel

        stmt = (
            select(EvidenceModel)
            .join(FindingModel, FindingModel.id == EvidenceModel.finding_id)
            .where(FindingModel.project_id == project_id)
            .order_by(EvidenceModel.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return [_to_entity(row) for row in result.scalars().all()]
