"""
Target endpoints (SRS §2.3, §6.2).

Routes nested under `/projects/{project_id}` use `require_project_role`
directly. Routes keyed by `target_id` alone (get/update/delete a single
target) use `require_project_role_for_target`, which resolves the
target's owning project first and applies the identical membership +
role check — a target's permissions are always its project's
permissions, never a separate RBAC layer.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.api.v1.deps import (
    get_target_service,
    require_project_role,
    require_project_role_for_target,
)
from app.api.v1.schemas.targets import CreateTargetRequest, TargetResponse, UpdateTargetRequest
from app.application.target_service import TargetService
from app.domain.entities import ProjectMember, Target
from app.domain.value_objects import PROJECT_ADMIN_ROLES

router = APIRouter(tags=["targets"])


@router.post(
    "/projects/{project_id}/targets",
    response_model=TargetResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a target to a project (Owner/Admin only)",
)
async def create_target(
    project_id: UUID,
    body: CreateTargetRequest,
    _member: ProjectMember = Depends(require_project_role(*PROJECT_ADMIN_ROLES)),
    service: TargetService = Depends(get_target_service),
) -> Target:
    return await service.create(project_id, body.value, body.target_type)


@router.get(
    "/projects/{project_id}/targets",
    response_model=list[TargetResponse],
    summary="List targets in a project (any project member)",
)
async def list_targets(
    project_id: UUID,
    _member: ProjectMember = Depends(require_project_role()),
    service: TargetService = Depends(get_target_service),
) -> list[Target]:
    return await service.list_for_project(project_id)


@router.get(
    "/targets/{target_id}",
    response_model=TargetResponse,
    summary="Get a single target by id (any member of its owning project)",
)
async def get_target(
    target_id: UUID,
    _member: ProjectMember = Depends(require_project_role_for_target()),
    service: TargetService = Depends(get_target_service),
) -> Target:
    return await service.get(target_id)


@router.patch(
    "/targets/{target_id}",
    response_model=TargetResponse,
    summary="Update a target's value, type, or scope flag (Owner/Admin of its project)",
)
async def update_target(
    target_id: UUID,
    body: UpdateTargetRequest,
    _member: ProjectMember = Depends(require_project_role_for_target(*PROJECT_ADMIN_ROLES)),
    service: TargetService = Depends(get_target_service),
) -> Target:
    return await service.update(
        target_id,
        value=body.value,
        target_type=body.target_type,
        in_scope=body.in_scope,
    )


@router.delete(
    "/targets/{target_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Soft-delete a target (Owner/Admin of its project)",
)
async def delete_target(
    target_id: UUID,
    _member: ProjectMember = Depends(require_project_role_for_target(*PROJECT_ADMIN_ROLES)),
    service: TargetService = Depends(get_target_service),
) -> None:
    await service.soft_delete(target_id)
