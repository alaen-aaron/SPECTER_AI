"""Evidence endpoints (SRS §2.9)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, UploadFile, status
from fastapi.responses import StreamingResponse

from app.api.v1.deps import (
    get_current_user,
    get_evidence_service,
    require_finding_view_permission,
)
from app.api.v1.schemas.evidence import EvidenceResponse, PaginatedEvidenceResponse
from app.application.evidence_service import EvidenceService
from app.domain.entities import Evidence, ProjectMember, User
from app.domain.value_objects import EvidenceType

router = APIRouter(tags=["evidence"])


@router.post(
    "/findings/{finding_id}/evidence",
    response_model=EvidenceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload evidence attached to a finding",
)
async def upload_evidence(
    finding_id: UUID,
    file: UploadFile,
    evidence_type: str = "raw_log",
    _member: ProjectMember = Depends(require_finding_view_permission()),
    service: EvidenceService = Depends(get_evidence_service),
    current_user: User = Depends(get_current_user),
) -> Evidence:
    content = await file.read()
    return await service.attach(
        finding_id=finding_id,
        content=content,
        evidence_type=EvidenceType(evidence_type),
        filename=file.filename,
        collected_by=current_user.id,
    )


@router.get(
    "/findings/{finding_id}/evidence",
    response_model=PaginatedEvidenceResponse,
    summary="List evidence for a finding",
)
async def list_evidence(
    finding_id: UUID,
    _member: ProjectMember = Depends(require_finding_view_permission()),
    service: EvidenceService = Depends(get_evidence_service),
) -> PaginatedEvidenceResponse:
    items = await service.list_for_finding(finding_id)
    return PaginatedEvidenceResponse(items=items)


@router.get(
    "/evidence/{evidence_id}",
    response_model=EvidenceResponse,
    summary="Get single evidence metadata",
)
async def get_evidence(
    evidence_id: UUID,
    service: EvidenceService = Depends(get_evidence_service),
    _member: ProjectMember = Depends(require_finding_view_permission()),
) -> Evidence:
    return await service.get(evidence_id)


@router.get(
    "/evidence/{evidence_id}/download",
    summary="Download evidence content",
)
async def download_evidence(
    evidence_id: UUID,
    service: EvidenceService = Depends(get_evidence_service),
    _member: ProjectMember = Depends(require_finding_view_permission()),
) -> StreamingResponse:
    evidence = await service.get(evidence_id)
    content = await service.get_content(evidence_id)
    if content is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Evidence content not found on disk.")

    media_type = _media_type_for(evidence.evidence_type)
    filename = evidence.filename or evidence.content_hash

    return StreamingResponse(
        iter([content]),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _media_type_for(evidence_type: EvidenceType) -> str:
    mapping = {
        EvidenceType.SCREENSHOT: "image/png",
        EvidenceType.RAW_LOG: "text/plain",
        EvidenceType.SESSION_RECORDING: "video/webm",
        EvidenceType.REQUEST_RESPONSE: "application/json",
    }
    return mapping.get(evidence_type, "application/octet-stream")
