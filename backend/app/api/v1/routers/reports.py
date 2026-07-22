"""Report endpoints (Milestone 5)."""

from __future__ import annotations

import os
from uuid import UUID

from fastapi import APIRouter, Depends, status
from fastapi.responses import FileResponse

from app.api.v1.deps import (
    get_current_user,
    get_report_service,
    require_project_role,
    require_report_view_permission,
    require_scan_launch_permission,
)
from app.api.v1.schemas.reports import (
    CreateReportRequest,
    ReportResponse,
    ReportVersionResponse,
)
from app.application.report_service import ReportService
from app.domain.entities import ProjectMember, Report, ReportVersion, User

router = APIRouter(tags=["reports"])


@router.post(
    "/projects/{project_id}/reports",
    response_model=ReportResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a report draft for a project",
)
async def create_report(
    project_id: UUID,
    body: CreateReportRequest,
    _member: ProjectMember = Depends(require_scan_launch_permission()),
    service: ReportService = Depends(get_report_service),
) -> Report:
    return await service.create(project_id=project_id, title=body.title)


@router.get(
    "/projects/{project_id}/reports",
    response_model=list[ReportResponse],
    summary="List reports for a project",
)
async def list_reports(
    project_id: UUID,
    _member: ProjectMember = Depends(require_project_role()),
    service: ReportService = Depends(get_report_service),
) -> list[Report]:
    return await service.list_for_project(project_id)


@router.get(
    "/reports/{report_id}",
    response_model=ReportResponse,
    summary="Get a single report by id",
)
async def get_report(
    report_id: UUID,
    _member: ProjectMember = Depends(require_report_view_permission()),
    service: ReportService = Depends(get_report_service),
) -> Report:
    return await service.get(report_id)


@router.post(
    "/reports/{report_id}/versions",
    response_model=ReportVersionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Generate a new version of a report",
)
async def generate_version(
    report_id: UUID,
    current_user: User = Depends(get_current_user),
    _member: ProjectMember = Depends(require_scan_launch_permission()),
    service: ReportService = Depends(get_report_service),
) -> ReportVersion:
    report = await service.get(report_id)
    return await service.generate_version(
        report_id=report_id,
        project_id=report.project_id,
        generated_by=current_user.id,
    )


@router.post(
    "/reports/{report_id}/finalize",
    response_model=ReportResponse,
    summary="Finalize a report (irreversible)",
)
async def finalize_report(
    report_id: UUID,
    _member: ProjectMember = Depends(require_scan_launch_permission()),
    service: ReportService = Depends(get_report_service),
) -> Report:
    return await service.finalize(report_id)


@router.get(
    "/report-versions/{version_id}",
    response_model=ReportVersionResponse,
    summary="Get report version metadata",
)
async def get_version(
    version_id: UUID,
    _member: ProjectMember = Depends(require_report_view_permission()),
    service: ReportService = Depends(get_report_service),
) -> ReportVersion:
    version = await service._version_repo.get(version_id)
    if version is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Report version not found.")
    return version


@router.get(
    "/report-versions/{version_id}/download",
    response_model=None,
    summary="Download report version file",
)
async def download_version(
    version_id: UUID,
    _member: ProjectMember = Depends(require_report_view_permission()),
    service: ReportService = Depends(get_report_service),
) -> FileResponse:
    version = await service._version_repo.get(version_id)
    if version is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Report version not found.")

    if not os.path.isfile(version.file_pointer):
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Report file not found on disk.")

    return FileResponse(
        path=version.file_pointer,
        media_type="text/markdown",
        filename=f"report_v{version.version_number}.md",
    )
