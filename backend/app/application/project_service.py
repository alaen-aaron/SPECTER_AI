"""
Project use-case services (SRS §2.2, FR-2.2/FR-2.3).

The state-transition gate here is the load-bearing piece: a project
cannot move to `Active` without a currently-valid `AuthorizationRecord`
attached (FR-2.3 — "a hard gate in the workflow engine, not just a UI
nudge"). That check lives in `ProjectService.transition_state`, not in
the API layer, so no future caller (API, CLI, background job) can
bypass it by skipping a UI step.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.domain.entities import Project, ProjectMember
from app.domain.exceptions import (
    InvalidProjectStateTransitionError,
    NotAProjectMemberError,
    ProjectNotAuthorizedError,
    ProjectNotFoundError,
)
from app.domain.repositories import AuthorizationRecordRepository, ProjectRepository
from app.domain.value_objects import ProjectRole, ProjectState


class ProjectService:
    def __init__(
        self,
        project_repository: ProjectRepository,
        authorization_repository: AuthorizationRecordRepository,
    ) -> None:
        self._projects = project_repository
        self._authorizations = authorization_repository

    async def create(
        self,
        organization_id: UUID,
        name: str,
        description: str | None,
        tags: list[str] | None,
        client_metadata: dict[str, str] | None,
        owner_user_id: UUID,
    ) -> Project:
        now = datetime.now(UTC)
        project = Project(
            id=uuid4(),
            organization_id=organization_id,
            name=name,
            description=description,
            state=ProjectState.DRAFT,
            tags=tags or [],
            client_metadata=client_metadata or {},
            created_at=now,
            updated_at=now,
        )
        await self._projects.add(project)

        owner_membership = ProjectMember(
            project_id=project.id,
            user_id=owner_user_id,
            role=ProjectRole.OWNER,
            created_at=now,
        )
        await self._projects.add_member(owner_membership)
        return project

    async def get(self, project_id: UUID) -> Project:
        project = await self._projects.get_by_id(project_id)
        if project is None:
            raise ProjectNotFoundError(project_id)
        return project

    async def list_for_organization(self, organization_id: UUID) -> list[Project]:
        return await self._projects.list_for_organization(organization_id)

    async def update_metadata(
        self,
        project_id: UUID,
        *,
        name: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        client_metadata: dict[str, str] | None = None,
    ) -> Project:
        project = await self.get(project_id)
        if name is not None:
            project.name = name
        if description is not None:
            project.description = description
        if tags is not None:
            project.tags = tags
        if client_metadata is not None:
            project.client_metadata = client_metadata
        await self._projects.update(project)
        return project

    async def transition_state(self, project_id: UUID, new_state: ProjectState) -> Project:
        """
        Validated state transition (FR-2.2). Raises
        `InvalidProjectStateTransitionError` for any transition not in
        `VALID_PROJECT_TRANSITIONS`, and `ProjectNotAuthorizedError` if
        the specific transition to `Active` is attempted without a
        currently-valid authorization record (FR-2.3).
        """
        project = await self.get(project_id)

        if not project.can_transition_to(new_state):
            raise InvalidProjectStateTransitionError(project.state.value, new_state.value)

        if new_state is ProjectState.ACTIVE:
            active_record = await self._authorizations.get_active_for_project(
                project_id, datetime.now(UTC)
            )
            if active_record is None or not active_record.is_active_on(datetime.now(UTC).date()):
                raise ProjectNotAuthorizedError(project_id)

        project.state = new_state
        await self._projects.update(project)
        return project

    async def soft_delete(self, project_id: UUID) -> None:
        await self.get(project_id)
        await self._projects.soft_delete(project_id)

    async def add_member(self, project_id: UUID, user_id: UUID, role: ProjectRole) -> ProjectMember:
        await self.get(project_id)
        member = ProjectMember(
            project_id=project_id,
            user_id=user_id,
            role=role,
            created_at=datetime.now(UTC),
        )
        await self._projects.add_member(member)
        return member

    async def list_members(self, project_id: UUID) -> list[ProjectMember]:
        await self.get(project_id)
        return await self._projects.list_members(project_id)

    async def require_member(self, project_id: UUID, user_id: UUID) -> ProjectMember:
        member = await self._projects.get_member(project_id, user_id)
        if member is None:
            raise NotAProjectMemberError(project_id)
        return member
