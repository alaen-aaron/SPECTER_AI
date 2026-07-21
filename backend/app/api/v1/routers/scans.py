"""
Scan endpoints (Milestone 3).

Every route delegates to `ScanService` — no router here ever imports
`PluginManager`, `ExecutionEngine`, or Celery directly, matching the
milestone's explicit "no API router should execute tools directly"
requirement.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.v1.deps import (
    get_current_user,
    get_scan_service,
    require_project_role,
    require_scan_launch_permission,
    require_scan_permission_for_scan,
    require_scan_view_permission,
)
from app.api.v1.schemas.scans import CreateScanRequest, PaginatedScanResponse, ScanResponse
from app.application.scan_service import ScanService
from app.domain.entities import OrganizationMember, ProjectMember, Scan, User

router = APIRouter(tags=["scans"])


@router.post(
    "/projects/{project_id}/scans",
    response_model=ScanResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Launch a scan (Scope Guard-validated; Owner/Admin/Lead/Tester or Org Admin)",
)
async def create_scan(
    project_id: UUID,
    body: CreateScanRequest,
    current_user: User = Depends(get_current_user),
    _member: ProjectMember | OrganizationMember = Depends(require_scan_launch_permission()),
    service: ScanService = Depends(get_scan_service),
) -> Scan:
    return await service.create(
        project_id=project_id,
        plugin_name=body.plugin,
        plugin_config=body.plugin_config,
        target_ids=body.target_ids,
        initiated_by=current_user.id,
    )


@router.get(
    "/projects/{project_id}/scans",
    response_model=PaginatedScanResponse,
    summary="List scans for a project (any project member)",
)
async def list_scans(
    project_id: UUID,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: datetime | None = Query(default=None),
    _member: ProjectMember = Depends(require_project_role()),
    service: ScanService = Depends(get_scan_service),
) -> PaginatedScanResponse:
    scans = await service.list_for_project(project_id, limit=limit, cursor=cursor)
    has_more = len(scans) > limit
    items = scans[:limit]
    next_cursor = items[-1].created_at if has_more and items else None
    return PaginatedScanResponse(items=items, next_cursor=next_cursor)


@router.get(
    "/scans/{scan_id}",
    response_model=ScanResponse,
    summary="Get a single scan by id (any member of its owning project)",
)
async def get_scan(
    scan_id: UUID,
    _member: ProjectMember = Depends(require_scan_view_permission()),
    service: ScanService = Depends(get_scan_service),
) -> Scan:
    return await service.get(scan_id)


@router.delete(
    "/scans/{scan_id}",
    response_model=ScanResponse,
    summary=(
        "Cancel a queued or running scan (soft cancellation — see ScanService.cancel "
        "docstring). Same permission rule as launching a scan."
    ),
)
async def cancel_scan(
    scan_id: UUID,
    _member: ProjectMember | OrganizationMember = Depends(require_scan_permission_for_scan()),
    service: ScanService = Depends(get_scan_service),
) -> Scan:
    return await service.cancel(scan_id)
