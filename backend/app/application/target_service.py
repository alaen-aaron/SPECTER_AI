"""Target use-case services (SRS §2.3, FR-3.1)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.domain.entities import Target
from app.domain.exceptions import TargetNotFoundError
from app.domain.repositories import TargetRepository
from app.domain.target_validation import validate_target_value
from app.domain.value_objects import TargetType


class TargetService:
    def __init__(self, target_repository: TargetRepository) -> None:
        self._targets = target_repository

    async def create(self, project_id: UUID, value: str, target_type: TargetType) -> Target:
        """Raises `InvalidTargetValueError` (from domain layer) if `value`
        is malformed for `target_type` — validated before persistence."""
        validate_target_value(value, target_type)

        now = datetime.now(UTC)
        target = Target(
            id=uuid4(),
            project_id=project_id,
            value=value.strip(),
            target_type=target_type,
            in_scope=True,
            created_at=now,
            updated_at=now,
        )
        await self._targets.add(target)
        return target

    async def get(self, target_id: UUID) -> Target:
        target = await self._targets.get_by_id(target_id)
        if target is None:
            raise TargetNotFoundError(target_id)
        return target

    async def list_for_project(self, project_id: UUID) -> list[Target]:
        return await self._targets.list_for_project(project_id)

    async def update(
        self,
        target_id: UUID,
        *,
        value: str | None = None,
        target_type: TargetType | None = None,
        in_scope: bool | None = None,
    ) -> Target:
        target = await self.get(target_id)

        effective_type = target_type or target.target_type
        effective_value = value if value is not None else target.value
        if value is not None or target_type is not None:
            validate_target_value(effective_value, effective_type)

        target.value = effective_value
        target.target_type = effective_type
        if in_scope is not None:
            target.in_scope = in_scope

        await self._targets.update(target)
        return target

    async def soft_delete(self, target_id: UUID) -> None:
        await self.get(target_id)
        await self._targets.soft_delete(target_id)
