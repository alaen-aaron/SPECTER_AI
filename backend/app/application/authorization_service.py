"""
Authorization Record use-case service (SRS §16.3, Milestone 2 scope).

This is the artifact the Scope Guard (`scope_guard_service.py`) and the
Project state machine (`project_service.transition_state`) both
depend on. Creating one does not, by itself, move a project to Active
— the tester must still explicitly request that transition, which then
checks this record's validity.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from app.domain.entities import AuthorizationRecord
from app.domain.exceptions import AuthorizationRecordNotFoundError
from app.domain.repositories import AuthorizationRecordRepository
from app.domain.value_objects import AuthorizationStatus


class AuthorizationRecordService:
    def __init__(self, authorization_repository: AuthorizationRecordRepository) -> None:
        self._authorizations = authorization_repository

    async def create(
        self,
        project_id: UUID,
        client_name: str,
        document_reference: str,
        authorized_from: date,
        authorized_to: date,
        allowed_targets: list[str],
        approved_by: UUID,
        scope_notes: str | None = None,
        evidence_pointer: str | None = None,
    ) -> AuthorizationRecord:
        record = AuthorizationRecord(
            id=uuid4(),
            project_id=project_id,
            client_name=client_name,
            document_reference=document_reference,
            authorized_from=authorized_from,
            authorized_to=authorized_to,
            allowed_targets=allowed_targets,
            approved_by=approved_by,
            status=AuthorizationStatus.ACTIVE,
            scope_notes=scope_notes,
            evidence_pointer=evidence_pointer,
            created_at=datetime.now(UTC),
        )
        await self._authorizations.add(record)
        return record

    async def get(self, record_id: UUID) -> AuthorizationRecord:
        record = await self._authorizations.get_by_id(record_id)
        if record is None:
            raise AuthorizationRecordNotFoundError(record_id)
        return record

    async def list_for_project(self, project_id: UUID) -> list[AuthorizationRecord]:
        return await self._authorizations.list_for_project(project_id)

    async def get_active_for_project(self, project_id: UUID) -> AuthorizationRecord | None:
        """Returns the currently-valid record for `project_id`, or None."""
        now = datetime.now(UTC)
        record = await self._authorizations.get_active_for_project(project_id, now)
        if record is not None and record.is_active_on(now.date()):
            return record
        return None
