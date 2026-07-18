"""SQLAlchemy implementation of `OrganizationRepository`."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession as SqlAsyncSession

from app.domain.entities import Organization, OrganizationInvitation, OrganizationMember
from app.domain.value_objects import OrganizationRole
from app.infrastructure.db.models.organization import (
    OrganizationInvitationModel,
    OrganizationMemberModel,
    OrganizationModel,
)


def _org_to_entity(row: OrganizationModel) -> Organization:
    return Organization(
        id=row.id,
        name=row.name,
        created_at=row.created_at,
        deleted_at=row.deleted_at,
    )


def _member_to_entity(row: OrganizationMemberModel) -> OrganizationMember:
    return OrganizationMember(
        organization_id=row.organization_id,
        user_id=row.user_id,
        role=OrganizationRole(row.role),
        created_at=row.created_at,
    )


def _invitation_to_entity(row: OrganizationInvitationModel) -> OrganizationInvitation:
    from app.domain.value_objects import InvitationStatus

    return OrganizationInvitation(
        id=row.id,
        organization_id=row.organization_id,
        email=str(row.email),
        role=OrganizationRole(row.role),
        invited_by=row.invited_by,
        status=InvitationStatus(row.status),
        created_at=row.created_at,
        expires_at=row.expires_at,
    )


class SqlAlchemyOrganizationRepository:
    """Satisfies `app.domain.repositories.OrganizationRepository` structurally."""

    def __init__(self, session: SqlAsyncSession) -> None:
        self._session = session

    async def get_by_id(self, organization_id: UUID) -> Organization | None:
        row = await self._session.get(OrganizationModel, organization_id)
        if row is None or row.deleted_at is not None:
            return None
        return _org_to_entity(row)

    async def list_for_user(self, user_id: UUID) -> list[Organization]:
        stmt = (
            select(OrganizationModel)
            .join(
                OrganizationMemberModel,
                OrganizationMemberModel.organization_id == OrganizationModel.id,
            )
            .where(
                OrganizationMemberModel.user_id == user_id, OrganizationModel.deleted_at.is_(None)
            )
        )
        result = await self._session.execute(stmt)
        return [_org_to_entity(row) for row in result.scalars().all()]

    async def add(self, organization: Organization) -> None:
        model = OrganizationModel(id=organization.id, name=organization.name)
        self._session.add(model)
        await self._session.flush()

    async def update(self, organization: Organization) -> None:
        stmt = (
            update(OrganizationModel)
            .where(OrganizationModel.id == organization.id)
            .values(name=organization.name)
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def soft_delete(self, organization_id: UUID) -> None:
        stmt = (
            update(OrganizationModel)
            .where(OrganizationModel.id == organization_id)
            .values(deleted_at=datetime.now(UTC))
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def add_member(self, member: OrganizationMember) -> None:
        model = OrganizationMemberModel(
            organization_id=member.organization_id,
            user_id=member.user_id,
            role=member.role.value,
        )
        self._session.add(model)
        await self._session.flush()

    async def get_member(self, organization_id: UUID, user_id: UUID) -> OrganizationMember | None:
        row = await self._session.get(OrganizationMemberModel, (organization_id, user_id))
        return _member_to_entity(row) if row else None

    async def list_members(self, organization_id: UUID) -> list[OrganizationMember]:
        stmt = select(OrganizationMemberModel).where(
            OrganizationMemberModel.organization_id == organization_id
        )
        result = await self._session.execute(stmt)
        return [_member_to_entity(row) for row in result.scalars().all()]

    async def update_member_role(
        self, organization_id: UUID, user_id: UUID, role: OrganizationRole
    ) -> None:
        stmt = (
            update(OrganizationMemberModel)
            .where(
                OrganizationMemberModel.organization_id == organization_id,
                OrganizationMemberModel.user_id == user_id,
            )
            .values(role=role.value)
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def add_invitation(self, invitation: OrganizationInvitation) -> None:
        model = OrganizationInvitationModel(
            id=invitation.id,
            organization_id=invitation.organization_id,
            email=invitation.email,
            role=invitation.role.value,
            invited_by=invitation.invited_by,
            status=invitation.status.value,
            expires_at=invitation.expires_at,
        )
        self._session.add(model)
        await self._session.flush()

    async def list_invitations(self, organization_id: UUID) -> list[OrganizationInvitation]:
        stmt = select(OrganizationInvitationModel).where(
            OrganizationInvitationModel.organization_id == organization_id
        )
        result = await self._session.execute(stmt)
        return [_invitation_to_entity(row) for row in result.scalars().all()]
