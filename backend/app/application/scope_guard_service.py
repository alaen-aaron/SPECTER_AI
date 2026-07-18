"""
Scope Guard (SRS §16.3): the core safety control keeping SPECTER_AI
aligned with "authorized environments only."

Milestone 2 scope note: there is no scan-launch endpoint yet (that is
Phase 2, per the frozen SRS §19). This service is nonetheless fully
implemented now, because every future scan-launch code path — plugin
dispatch in Phase 2, the AI Planner's approved-action execution in
Phase 4 — MUST route through this exact validation, and getting its
contract right now means later milestones consume it rather than
reimplement it. It's exposed today through a clearly-labeled preview
endpoint (`POST /projects/{id}/scope-check`), not the final SRS §6.2
scan-launch route.

Validation performed, in order (matching the Milestone 2 spec exactly):
  1. Project exists
  2. Project is Active
  3. An authorization record exists and is currently active (not
     expired, not revoked, within its date range)
  4. Every requested target belongs to the project
  5. Every requested target is within the authorization record's
     allowed_targets list

No bypasses: every check raises on failure. There is no "warn but
continue" path, and no flag that disables this service in production
(`SCOPE_GUARD_STRICT` controls stricter *additional* infra-layer
behavior in later phases, not whether this service runs at all).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from app.domain.entities import AuthorizationRecord, Target
from app.domain.exceptions import (
    NoActiveAuthorizationError,
    OutOfScopeTargetError,
    ProjectNotActiveError,
    ProjectNotFoundError,
    TargetNotFoundError,
)
from app.domain.repositories import (
    AuthorizationRecordRepository,
    ProjectRepository,
    TargetRepository,
)
from app.domain.value_objects import ProjectState


@dataclass(frozen=True, slots=True)
class ScopeCheckResult:
    """Returned only on success — every failure mode raises instead."""

    project_id: UUID
    authorization_record_id: UUID
    validated_target_ids: tuple[UUID, ...]


class ScopeGuardService:
    def __init__(
        self,
        project_repository: ProjectRepository,
        target_repository: TargetRepository,
        authorization_repository: AuthorizationRecordRepository,
    ) -> None:
        self._projects = project_repository
        self._targets = target_repository
        self._authorizations = authorization_repository

    async def validate_targets(self, project_id: UUID, target_ids: list[UUID]) -> ScopeCheckResult:
        project = await self._projects.get_by_id(project_id)
        if project is None:
            raise ProjectNotFoundError(project_id)

        if project.state is not ProjectState.ACTIVE:
            raise ProjectNotActiveError(project_id, project.state.value)

        now = datetime.now(UTC)
        record = await self._authorizations.get_active_for_project(project_id, now)
        if record is None or not record.is_active_on(now.date()):
            raise NoActiveAuthorizationError(project_id)

        targets: list[Target] = []
        for target_id in target_ids:
            target = await self._targets.get_by_id(target_id)
            if target is None or target.project_id != project_id:
                raise TargetNotFoundError(target_id)
            targets.append(target)

        out_of_scope = tuple(t.id for t in targets if not self._target_covered_by_record(t, record))
        if out_of_scope:
            raise OutOfScopeTargetError(out_of_scope)

        return ScopeCheckResult(
            project_id=project_id,
            authorization_record_id=record.id,
            validated_target_ids=tuple(target_ids),
        )

    @staticmethod
    def _target_covered_by_record(target: Target, record: AuthorizationRecord) -> bool:
        """
        A target is in scope if its value appears in the record's
        `allowed_targets` allow-list, or if the allow-list is empty
        (meaning: "everything belonging to this project is authorized"
        — an explicit, intentional choice recorded at authorization
        time, not a default-permit fallback for missing data).
        """
        if not record.allowed_targets:
            return True
        return target.value in record.allowed_targets
