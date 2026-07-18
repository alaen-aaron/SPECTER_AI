"""Organization endpoints (SRS §2.2, §6.2)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.api.v1.deps import (
    get_current_user,
    get_organization_service,
    require_org_role,
)
from app.api.v1.schemas.organizations import (
    AddOrganizationMemberRequest,
    CreateInvitationRequest,
    CreateOrganizationRequest,
    InvitationResponse,
    OrganizationMemberResponse,
    OrganizationResponse,
    RenameOrganizationRequest,
)
from app.application.organization_service import OrganizationService
from app.domain.entities import Organization, OrganizationInvitation, OrganizationMember, User
from app.domain.value_objects import ORGANIZATION_ADMIN_ROLES

router = APIRouter(prefix="/organizations", tags=["organizations"])


@router.post(
    "",
    response_model=OrganizationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an organization (caller becomes Owner)",
)
async def create_organization(
    body: CreateOrganizationRequest,
    current_user: User = Depends(get_current_user),
    service: OrganizationService = Depends(get_organization_service),
) -> Organization:
    return await service.create(body.name, current_user.id)


@router.get("", response_model=list[OrganizationResponse], summary="List organizations I belong to")
async def list_my_organizations(
    current_user: User = Depends(get_current_user),
    service: OrganizationService = Depends(get_organization_service),
) -> list[Organization]:
    return await service.list_for_user(current_user.id)


@router.get(
    "/{organization_id}",
    response_model=OrganizationResponse,
    summary="Get an organization (any member)",
)
async def get_organization(
    organization_id: UUID,
    _member: OrganizationMember = Depends(require_org_role()),  # membership alone is enough
    service: OrganizationService = Depends(get_organization_service),
) -> Organization:
    return await service.get(organization_id)


@router.patch(
    "/{organization_id}",
    response_model=OrganizationResponse,
    summary="Rename an organization (Owner/Admin only)",
)
async def rename_organization(
    organization_id: UUID,
    body: RenameOrganizationRequest,
    _member: OrganizationMember = Depends(require_org_role(*ORGANIZATION_ADMIN_ROLES)),
    service: OrganizationService = Depends(get_organization_service),
) -> Organization:
    return await service.rename(organization_id, body.name)


@router.delete(
    "/{organization_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Soft-delete an organization (Owner only)",
)
async def delete_organization(
    organization_id: UUID,
    _member: OrganizationMember = Depends(require_org_role(*ORGANIZATION_ADMIN_ROLES)),
    service: OrganizationService = Depends(get_organization_service),
) -> None:
    await service.soft_delete(organization_id)


@router.post(
    "/{organization_id}/members",
    response_model=OrganizationMemberResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a member to an organization (Owner/Admin only)",
)
async def add_organization_member(
    organization_id: UUID,
    body: AddOrganizationMemberRequest,
    _member: OrganizationMember = Depends(require_org_role(*ORGANIZATION_ADMIN_ROLES)),
    service: OrganizationService = Depends(get_organization_service),
) -> OrganizationMember:
    return await service.add_member(organization_id, body.user_id, body.role)


@router.get(
    "/{organization_id}/members",
    response_model=list[OrganizationMemberResponse],
    summary="List organization members (any member)",
)
async def list_organization_members(
    organization_id: UUID,
    _member: OrganizationMember = Depends(require_org_role()),
    service: OrganizationService = Depends(get_organization_service),
) -> list[OrganizationMember]:
    return await service.list_members(organization_id)


@router.post(
    "/{organization_id}/invitations",
    response_model=InvitationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a pending invitation (schema-only — no email is sent yet)",
)
async def create_invitation(
    organization_id: UUID,
    body: CreateInvitationRequest,
    current_user: User = Depends(get_current_user),
    _member: OrganizationMember = Depends(require_org_role(*ORGANIZATION_ADMIN_ROLES)),
    service: OrganizationService = Depends(get_organization_service),
) -> OrganizationInvitation:
    return await service.create_invitation(organization_id, body.email, body.role, current_user.id)


@router.get(
    "/{organization_id}/invitations",
    response_model=list[InvitationResponse],
    summary="List invitations (Owner/Admin only)",
)
async def list_invitations(
    organization_id: UUID,
    _member: OrganizationMember = Depends(require_org_role(*ORGANIZATION_ADMIN_ROLES)),
    service: OrganizationService = Depends(get_organization_service),
) -> list[OrganizationInvitation]:
    return await service.list_invitations(organization_id)
