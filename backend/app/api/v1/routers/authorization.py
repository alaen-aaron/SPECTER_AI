"""
Authorization Record + Scope Guard preview endpoints (SRS §16.3).

`POST /projects/{id}/authorization` matches the frozen SRS §6.2 path
exactly. `POST /projects/{id}/scope-check` is a Milestone 2 addition —
see its docstring below for why it exists ahead of the real scan-launch
endpoint that is Phase 2 scope.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.api.v1.deps import (
    get_authorization_record_service,
    get_current_user,
    get_scope_guard_service,
    require_project_role,
)
from app.api.v1.schemas.authorization import (
    AuthorizationRecordResponse,
    CreateAuthorizationRecordRequest,
    ScopeCheckRequest,
    ScopeCheckResponse,
)
from app.application.authorization_service import AuthorizationRecordService
from app.application.scope_guard_service import ScopeCheckResult, ScopeGuardService
from app.domain.entities import AuthorizationRecord, ProjectMember, User
from app.domain.value_objects import PROJECT_ADMIN_ROLES

router = APIRouter(prefix="/projects/{project_id}", tags=["authorization"])


@router.post(
    "/authorization",
    response_model=AuthorizationRecordResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Attach a signed authorization/scope document to a project (Owner/Admin only)",
)
async def create_authorization_record(
    project_id: UUID,
    body: CreateAuthorizationRecordRequest,
    current_user: User = Depends(get_current_user),
    _member: ProjectMember = Depends(require_project_role(*PROJECT_ADMIN_ROLES)),
    service: AuthorizationRecordService = Depends(get_authorization_record_service),
) -> AuthorizationRecord:
    return await service.create(
        project_id=project_id,
        client_name=body.client_name,
        document_reference=body.document_reference,
        authorized_from=body.authorized_from,
        authorized_to=body.authorized_to,
        allowed_targets=body.allowed_targets,
        approved_by=current_user.id,
        scope_notes=body.scope_notes,
        evidence_pointer=body.evidence_pointer,
    )


@router.get(
    "/authorization",
    response_model=list[AuthorizationRecordResponse],
    summary="List authorization records for a project (any project member)",
)
async def list_authorization_records(
    project_id: UUID,
    _member: ProjectMember = Depends(require_project_role()),
    service: AuthorizationRecordService = Depends(get_authorization_record_service),
) -> list[AuthorizationRecord]:
    return await service.list_for_project(project_id)


@router.post(
    "/scope-check",
    response_model=ScopeCheckResponse,
    summary="[Milestone 2 preview] Validate targets against Scope Guard",
    description=(
        "Runs the exact `ScopeGuardService.validate_targets` logic that every "
        "future scan-launch code path (Phase 2 plugin dispatch, Phase 4 AI "
        "Planner-approved actions) will be required to call. This endpoint "
        "itself is NOT the SRS §6.2 scan-launch route — no scan exists yet — "
        "it exists so Scope Guard is exercisable and testable end-to-end "
        "ahead of Phase 2."
    ),
)
async def scope_check(
    project_id: UUID,
    body: ScopeCheckRequest,
    _member: ProjectMember = Depends(require_project_role()),
    service: ScopeGuardService = Depends(get_scope_guard_service),
) -> ScopeCheckResult:
    return await service.validate_targets(project_id, body.target_ids)
