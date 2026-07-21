"""Finding endpoints."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.v1.deps import (
    get_finding_service,
    require_finding_edit_permission,
    require_finding_view_permission,
    require_project_role,
)
from app.api.v1.schemas.findings import (
    FindingResponse,
    PaginatedFindingResponse,
    UpdateFindingStatusRequest,
)
from app.application.finding_service import FindingService
from app.domain.entities import Finding, OrganizationMember, ProjectMember

router = APIRouter(tags=["findings"])


@router.get(
    "/projects/{project_id}/findings",
    response_model=PaginatedFindingResponse,
    summary="List findings for a project (any project member)",
)
async def list_findings(
    project_id: UUID,
    severity: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    cursor: datetime | None = Query(default=None),
    _member: ProjectMember = Depends(require_project_role()),
    service: FindingService = Depends(get_finding_service),
) -> PaginatedFindingResponse:
    from app.domain.value_objects import Severity

    severity_enum = Severity(severity) if severity else None
    findings = await service.list_for_project(
        project_id, severity_enum, limit=limit, cursor=cursor
    )
    has_more = len(findings) > limit
    items = findings[:limit]
    next_cursor = items[-1].created_at if has_more and items else None
    return PaginatedFindingResponse(items=items, next_cursor=next_cursor)


@router.get(
    "/findings/{finding_id}",
    response_model=FindingResponse,
    summary="Get a single finding by id (any member of its owning project)",
)
async def get_finding(
    finding_id: UUID,
    _member: ProjectMember = Depends(require_finding_view_permission()),
    service: FindingService = Depends(get_finding_service),
) -> Finding:
    return await service.get(finding_id)


@router.patch(
    "/findings/{finding_id}/status",
    response_model=FindingResponse,
    status_code=status.HTTP_200_OK,
    summary="Update finding status (scan-capable project role or org admin)",
)
async def update_finding_status(
    finding_id: UUID,
    body: UpdateFindingStatusRequest,
    _member: ProjectMember | OrganizationMember = Depends(require_finding_edit_permission()),
    service: FindingService = Depends(get_finding_service),
) -> Finding:
    return await service.update_status(finding_id, body.status)
