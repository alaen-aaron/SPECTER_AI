"""Evidence use-case service (SRS §2.9)."""

from __future__ import annotations

from uuid import UUID

from app.domain.entities import Evidence
from app.domain.exceptions import (
    EvidenceAttachmentError,
    EvidenceNotFoundError,
    FindingNotFoundError,
)
from app.domain.repositories import EvidenceRepository, FindingRepository
from app.domain.value_objects import EvidenceType
from app.infrastructure.storage.evidence_store import EvidenceStore


class EvidenceService:
    def __init__(
        self,
        evidence_repository: EvidenceRepository,
        evidence_store: EvidenceStore,
        finding_repository: FindingRepository,
    ) -> None:
        self._evidence_repo = evidence_repository
        self._store = evidence_store
        self._finding_repo = finding_repository

    async def attach(
        self,
        finding_id: UUID,
        content: bytes,
        evidence_type: EvidenceType,
        filename: str | None,
        collected_by: UUID,
    ) -> Evidence:
        finding = await self._finding_repo.get(finding_id)
        if finding is None:
            raise FindingNotFoundError(finding_id)

        if not content:
            raise EvidenceAttachmentError("Empty content not allowed.")

        evidence = self._store.store(
            finding_id=finding_id,
            content=content,
            evidence_type=evidence_type.value,
            filename=filename,
            collected_by=collected_by,
        )
        await self._evidence_repo.add(evidence)
        return evidence

    async def get(self, evidence_id: UUID) -> Evidence:
        evidence = await self._evidence_repo.get(evidence_id)
        if evidence is None:
            raise EvidenceNotFoundError(evidence_id)
        return evidence

    async def list_for_finding(self, finding_id: UUID) -> list[Evidence]:
        return await self._evidence_repo.list_for_finding(finding_id)

    async def get_content(self, evidence_id: UUID) -> bytes | None:
        evidence = await self.get(evidence_id)
        return self._store.retrieve(evidence.content_hash, evidence.finding_id)
