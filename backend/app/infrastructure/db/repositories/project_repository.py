"""SQLAlchemy implementation of `ProjectRepository`."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession as SqlAsyncSession

from app.domain.entities import Project, ProjectMember
from app.domain.value_objects import ProjectRole, ProjectState
from app.infrastructure.db.models.project import ProjectMemberModel, ProjectModel


def _project_to_entity(row: ProjectModel) -> Project:
    return Project(
        id=row.id,
        organization_id=row.organization_id,
        name=row.name,
        description=row.description,
        state=ProjectState(row.state),
        tags=list(row.tags or []),
        client_metadata=dict(row.client_metadata or {}),
        created_at=row.created_at,
        updated_at=row.updated_at,
        deleted_at=row.deleted_at,
    )


def _member_to_entity(row: ProjectMemberModel) -> ProjectMember:
    return ProjectMember(
        project_id=row.project_id,
        user_id=row.user_id,
        role=ProjectRole(row.role),
        created_at=row.created_at,
    )


class SqlAlchemyProjectRepository:
    """Satisfies `app.domain.repositories.ProjectRepository` structurally."""

    def __init__(self, session: SqlAsyncSession) -> None:
        self._session = session

    async def get_by_id(self, project_id: UUID) -> Project | None:
        row = await self._session.get(ProjectModel, project_id)
        if row is None or row.deleted_at is not None:
            return None
        return _project_to_entity(row)

    async def list_for_organization(self, organization_id: UUID) -> list[Project]:
        stmt = select(ProjectModel).where(
            ProjectModel.organization_id == organization_id,
            ProjectModel.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return [_project_to_entity(row) for row in result.scalars().all()]

    async def add(self, project: Project) -> None:
        model = ProjectModel(
            id=project.id,
            organization_id=project.organization_id,
            name=project.name,
            description=project.description,
            state=project.state.value,
            tags=project.tags,
            client_metadata=project.client_metadata,
        )
        self._session.add(model)
        await self._session.flush()

    async def update(self, project: Project) -> None:
        stmt = (
            update(ProjectModel)
            .where(ProjectModel.id == project.id)
            .values(
                name=project.name,
                description=project.description,
                state=project.state.value,
                tags=project.tags,
                client_metadata=project.client_metadata,
                updated_at=datetime.now(UTC),
            )
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def soft_delete(self, project_id: UUID) -> None:
        stmt = (
            update(ProjectModel)
            .where(ProjectModel.id == project_id)
            .values(deleted_at=datetime.now(UTC))
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def add_member(self, member: ProjectMember) -> None:
        model = ProjectMemberModel(
            project_id=member.project_id,
            user_id=member.user_id,
            role=member.role.value,
        )
        self._session.add(model)
        await self._session.flush()

    async def get_member(self, project_id: UUID, user_id: UUID) -> ProjectMember | None:
        row = await self._session.get(ProjectMemberModel, (project_id, user_id))
        return _member_to_entity(row) if row else None

    async def list_members(self, project_id: UUID) -> list[ProjectMember]:
        stmt = select(ProjectMemberModel).where(ProjectMemberModel.project_id == project_id)
        result = await self._session.execute(stmt)
        return [_member_to_entity(row) for row in result.scalars().all()]

    async def update_member_role(self, project_id: UUID, user_id: UUID, role: ProjectRole) -> None:
        stmt = (
            update(ProjectMemberModel)
            .where(
                ProjectMemberModel.project_id == project_id,
                ProjectMemberModel.user_id == user_id,
            )
            .values(role=role.value)
        )
        await self._session.execute(stmt)
        await self._session.flush()
