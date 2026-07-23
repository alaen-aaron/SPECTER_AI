"""
Schedule endpoints (Phase 2/3).

Manages workflow scheduling — create, pause, resume, delete schedules.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.api.v1.deps import (
    get_current_user,
    get_schedule_service,
    require_project_role,
)
from app.api.v1.schemas.workflows import (
    CreateScheduleRequest,
    ScheduleListResponse,
    ScheduleResponse,
)
from app.application.schedule_service import ScheduleService
from app.domain.entities import ProjectMember, Schedule, User

router = APIRouter(tags=["schedules"])


@router.post(
    "/projects/{project_id}/schedules",
    response_model=ScheduleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a schedule for a workflow",
)
async def create_schedule(
    project_id: UUID,
    body: CreateScheduleRequest,
    current_user: User = Depends(get_current_user),
    _member: ProjectMember = Depends(require_project_role()),
    service: ScheduleService = Depends(get_schedule_service),
) -> Schedule:
    return await service.create(
        workflow_id=body.workflow_id,
        project_id=project_id,
        frequency=body.frequency,
        cron_expression=body.cron_expression,
        created_by=current_user.id,
    )


@router.get(
    "/projects/{project_id}/schedules",
    response_model=ScheduleListResponse,
    summary="List schedules for a project",
)
async def list_schedules(
    project_id: UUID,
    _member: ProjectMember = Depends(require_project_role()),
    service: ScheduleService = Depends(get_schedule_service),
) -> ScheduleListResponse:
    schedules = await service.list_for_project(project_id)
    return ScheduleListResponse(items=schedules)


@router.get(
    "/schedules/{schedule_id}",
    response_model=ScheduleResponse,
    summary="Get a schedule by id",
)
async def get_schedule(
    schedule_id: UUID,
    _member: ProjectMember = Depends(require_project_role()),
    service: ScheduleService = Depends(get_schedule_service),
) -> Schedule:
    return await service.get(schedule_id)


@router.post(
    "/schedules/{schedule_id}/pause",
    response_model=ScheduleResponse,
    summary="Pause a schedule",
)
async def pause_schedule(
    schedule_id: UUID,
    _member: ProjectMember = Depends(require_project_role()),
    service: ScheduleService = Depends(get_schedule_service),
) -> Schedule:
    return await service.pause(schedule_id)


@router.post(
    "/schedules/{schedule_id}/resume",
    response_model=ScheduleResponse,
    summary="Resume a paused schedule",
)
async def resume_schedule(
    schedule_id: UUID,
    _member: ProjectMember = Depends(require_project_role()),
    service: ScheduleService = Depends(get_schedule_service),
) -> Schedule:
    return await service.resume(schedule_id)


@router.delete(
    "/schedules/{schedule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Delete a schedule",
)
async def delete_schedule(
    schedule_id: UUID,
    _member: ProjectMember = Depends(require_project_role()),
    service: ScheduleService = Depends(get_schedule_service),
) -> None:
    await service.delete(schedule_id)
