"""
Organization use-case services (SRS §2.2).

Covers CRUD, membership, invitations (schema-only per Milestone 2
scope — no email delivery or accept flow), and soft delete.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from app.domain.entities import Organization, OrganizationInvitation, OrganizationMember
from app.domain.exceptions import (
    NotAnOrganizationMemberError,
    OrganizationNotFoundError,
)
from app.domain.repositories import OrganizationRepository
from app.domain.value_objects import InvitationStatus, OrganizationRole

INVITATION_TTL_DAYS = 7


class OrganizationService:
    def __init__(self, organization_repository: OrganizationRepository) -> None:
        self._organizations = organization_repository

    async def create(self, name: str, owner_user_id: UUID) -> Organization:
        """Creates an organization and adds the creator as its Owner."""
        organization = Organization(id=uuid4(), name=name, created_at=datetime.now(UTC))
        await self._organizations.add(organization)

        owner_membership = OrganizationMember(
            organization_id=organization.id,
            user_id=owner_user_id,
            role=OrganizationRole.OWNER,
            created_at=datetime.now(UTC),
        )
        await self._organizations.add_member(owner_membership)
        return organization

    async def get(self, organization_id: UUID) -> Organization:
        organization = await self._organizations.get_by_id(organization_id)
        if organization is None:
            raise OrganizationNotFoundError(organization_id)
        return organization

    async def list_for_user(self, user_id: UUID) -> list[Organization]:
        return await self._organizations.list_for_user(user_id)

    async def rename(self, organization_id: UUID, new_name: str) -> Organization:
        organization = await self.get(organization_id)
        organization.name = new_name
        await self._organizations.update(organization)
        return organization

    async def soft_delete(self, organization_id: UUID) -> None:
        await self.get(organization_id)  # raises if not found/already deleted
        await self._organizations.soft_delete(organization_id)

    async def add_member(
        self, organization_id: UUID, user_id: UUID, role: OrganizationRole
    ) -> OrganizationMember:
        await self.get(organization_id)
        member = OrganizationMember(
            organization_id=organization_id,
            user_id=user_id,
            role=role,
            created_at=datetime.now(UTC),
        )
        await self._organizations.add_member(member)
        return member

    async def list_members(self, organization_id: UUID) -> list[OrganizationMember]:
        await self.get(organization_id)
        return await self._organizations.list_members(organization_id)

    async def require_member(self, organization_id: UUID, user_id: UUID) -> OrganizationMember:
        """Raises `NotAnOrganizationMemberError` if the user has no membership row."""
        member = await self._organizations.get_member(organization_id, user_id)
        if member is None:
            raise NotAnOrganizationMemberError(organization_id)
        return member

    async def get_member_or_none(
        self, organization_id: UUID, user_id: UUID
    ) -> OrganizationMember | None:
        """Non-raising counterpart to `require_member`, for callers that
        need to try organization membership as a fallback check (e.g.
        `require_scan_launch_permission`) without catching an exception
        for what is, in that context, an entirely expected outcome."""
        return await self._organizations.get_member(organization_id, user_id)

    async def create_invitation(
        self,
        organization_id: UUID,
        email: str,
        role: OrganizationRole,
        invited_by: UUID,
    ) -> OrganizationInvitation:
        """
        Schema-only per Milestone 2 scope: persists an invitation row.
        No email is sent and there is no accept endpoint yet — those
        are later-milestone additions once real invite delivery is in
        scope.
        """
        await self.get(organization_id)
        invitation = OrganizationInvitation(
            id=uuid4(),
            organization_id=organization_id,
            email=email,
            role=role,
            invited_by=invited_by,
            status=InvitationStatus.PENDING,
            created_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(days=INVITATION_TTL_DAYS),
        )
        await self._organizations.add_invitation(invitation)
        return invitation

    async def list_invitations(self, organization_id: UUID) -> list[OrganizationInvitation]:
        await self.get(organization_id)
        return await self._organizations.list_invitations(organization_id)
