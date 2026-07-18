"""Project endpoints (SRS §2.2, §6.2)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.api.v1.deps import (
    get_current_user,
    get_project_service,
    require_org_role,
    require_project_role,
)
from app.api.v1.schemas.projects import (
    AddProjectMemberRequest,
    CreateProjectRequest,
    ProjectMemberResponse,
    ProjectResponse,
    TransitionProjectStateRequest,
    UpdateProjectRequest,
)
from app.application.project_service import ProjectService
from app.domain.entities import OrganizationMember, Project, ProjectMember, User
from app.domain.value_objects import PROJECT_ADMIN_ROLES

router = APIRouter(tags=["projects"])


@router.post(
    "/organizations/{organization_id}/projects",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a project within an organization (caller becomes Project Owner)",
)
async def create_project(
    organization_id: UUID,
    body: CreateProjectRequest,
    current_user: User = Depends(get_current_user),
    _member: OrganizationMember = Depends(require_org_role()),  # any org member may propose one
    service: ProjectService = Depends(get_project_service),
) -> Project:
    return await service.create(
        organization_id=organization_id,
        name=body.name,
        description=body.description,
        tags=body.tags,
        client_metadata=body.client_metadata,
        owner_user_id=current_user.id,
    )


@router.get(
    "/organizations/{organization_id}/projects",
    response_model=list[ProjectResponse],
    summary="List projects in an organization",
)
async def list_projects(
    organization_id: UUID,
    _member: OrganizationMember = Depends(require_org_role()),
    service: ProjectService = Depends(get_project_service),
) -> list[Project]:
    return await service.list_for_organization(organization_id)


@router.get(
    "/projects/{project_id}",
    response_model=ProjectResponse,
    summary="Get a project (any project member)",
)
async def get_project(
    project_id: UUID,
    _member: ProjectMember = Depends(require_project_role()),
    service: ProjectService = Depends(get_project_service),
) -> Project:
    return await service.get(project_id)


@router.patch(
    "/projects/{project_id}",
    response_model=ProjectResponse,
    summary="Update project metadata (Owner/Admin only)",
)
async def update_project(
    project_id: UUID,
    body: UpdateProjectRequest,
    _member: ProjectMember = Depends(require_project_role(*PROJECT_ADMIN_ROLES)),
    service: ProjectService = Depends(get_project_service),
) -> Project:
    return await service.update_metadata(
        project_id,
        name=body.name,
        description=body.description,
        tags=body.tags,
        client_metadata=body.client_metadata,
    )


@router.patch(
    "/projects/{project_id}/state",
    response_model=ProjectResponse,
    summary="Transition project lifecycle state (Owner/Admin only; validated transitions only)",
)
async def transition_project_state(
    project_id: UUID,
    body: TransitionProjectStateRequest,
    _member: ProjectMember = Depends(require_project_role(*PROJECT_ADMIN_ROLES)),
    service: ProjectService = Depends(get_project_service),
) -> Project:
    return await service.transition_state(project_id, body.state)


@router.delete(
    "/projects/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Soft-delete a project (Owner/Admin only)",
)
async def delete_project(
    project_id: UUID,
    _member: ProjectMember = Depends(require_project_role(*PROJECT_ADMIN_ROLES)),
    service: ProjectService = Depends(get_project_service),
) -> None:
    await service.soft_delete(project_id)


@router.post(
    "/projects/{project_id}/members",
    response_model=ProjectMemberResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a member to a project (Owner/Admin only)",
)
async def add_project_member(
    project_id: UUID,
    body: AddProjectMemberRequest,
    _member: ProjectMember = Depends(require_project_role(*PROJECT_ADMIN_ROLES)),
    service: ProjectService = Depends(get_project_service),
) -> ProjectMember:
    return await service.add_member(project_id, body.user_id, body.role)


@router.get(
    "/projects/{project_id}/members",
    response_model=list[ProjectMemberResponse],
    summary="List project members (any project member)",
)
async def list_project_members(
    project_id: UUID,
    _member: ProjectMember = Depends(require_project_role()),
    service: ProjectService = Depends(get_project_service),
) -> list[ProjectMember]:
    return await service.list_members(project_id)
