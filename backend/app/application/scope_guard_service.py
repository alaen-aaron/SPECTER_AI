"""Scope Guard (SRS §16.3): the core safety control keeping SPECTER_AI
aligned with "authorized environments only."

Validation performed, in order:
  1. Project exists
  2. Project is Active
  3. At least one active authorization record exists for the project
  4. Every requested target belongs to the project
  5. Every requested target is covered by **any** active authorization record
     (a target is in scope if ANY record's allowed_targets list contains it)

No bypasses: every check raises on failure.
"""

from __future__ import annotations

import logging
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

logger = logging.getLogger(__name__)


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
            logger.warning("Scope check failed — project %s not found", project_id)
            raise ProjectNotFoundError(project_id)
        logger.info("Project %s found (state=%s)", project_id, project.state.value)

        if project.state is not ProjectState.ACTIVE:
            logger.warning(
                "Scope check failed — project %s is %s (requires ACTIVE)",
                project_id,
                project.state.value,
            )
            raise ProjectNotActiveError(project_id, project.state.value)

        now = datetime.now(UTC)
        now_date = now.date()

        active_records = await self._authorizations.list_active_for_project(project_id, now)
        logger.info(
            "Found %d active auth record(s) for project %s as of %s",
            len(active_records),
            project_id,
            now_date,
        )
        for r in active_records:
            logger.info(
                "  Auth record %s: allowed_targets=%s date_range=[%s, %s] status=%s",
                r.id,
                r.allowed_targets,
                r.authorized_from,
                r.authorized_to,
                r.status.value,
            )

        if not active_records:
            logger.warning(
                "Scope check failed — no active auth record for project %s as of %s",
                project_id,
                now_date,
            )
            raise NoActiveAuthorizationError(project_id)

        targets: list[Target] = []
        for target_id in target_ids:
            target = await self._targets.get_by_id(target_id)
            if target is None or target.project_id != project_id:
                logger.warning(
                    "Scope check failed — target %s not found or not in project %s",
                    target_id,
                    project_id,
                )
                raise TargetNotFoundError(target_id)
            targets.append(target)
            logger.debug("  Target loaded: id=%s value=%r", target.id, target.value)

        out_of_scope: list[UUID] = []
        for target in targets:
            covered = self._target_covered_by_any(target, active_records)
            logger.debug(
                "Target %s (value=%r): covered by any active record = %s",
                target.id,
                target.value,
                covered,
            )
            if not covered:
                out_of_scope.append(target.id)

        if out_of_scope:
            joined_ids = ", ".join(str(t) for t in out_of_scope)
            logger.warning(
                "Scope check failed — %d target(s) out of scope: %s",
                len(out_of_scope),
                joined_ids,
            )
            raise OutOfScopeTargetError(tuple(out_of_scope))

        logger.info(
            "Scope check passed — project=%s, %d target(s) validated against record %s",
            project_id,
            len(targets),
            active_records[0].id,
        )
        return ScopeCheckResult(
            project_id=project_id,
            authorization_record_id=active_records[0].id,
            validated_target_ids=tuple(target_ids),
        )

    @staticmethod
    def _target_covered_by_any(
        target: Target, records: list[AuthorizationRecord]
    ) -> bool:
        """Return True if *target* is within scope of *any* active record."""
        for record in records:
            if ScopeGuardService._target_covered_by_record(target, record):
                return True
        return False

    @staticmethod
    def _target_covered_by_record(target: Target, record: AuthorizationRecord) -> bool:
        """Return True if *target.value* is in *record.allowed_targets* (or the list is empty)."""
        if not record.allowed_targets:
            logger.info(
                "Record %s has empty allowed_targets → implicitly covers everything",
                record.id,
            )
            return True
        is_covered = target.value in record.allowed_targets
        logger.debug(
            "Record %s: target.value=%r in allowed_targets=%r → %s",
            record.id,
            target.value,
            record.allowed_targets,
            is_covered,
        )
        return is_covered
