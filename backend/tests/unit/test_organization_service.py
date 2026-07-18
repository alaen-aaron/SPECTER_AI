"""Unit tests for `OrganizationService`."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.application.organization_service import OrganizationService
from app.domain.exceptions import NotAnOrganizationMemberError, OrganizationNotFoundError
from app.domain.value_objects import InvitationStatus, OrganizationRole
from tests.fakes import FakeOrganizationRepository


@pytest.fixture
def service() -> OrganizationService:
    return OrganizationService(FakeOrganizationRepository())


@pytest.mark.asyncio
async def test_create_organization_adds_creator_as_owner(service):
    owner_id = uuid4()
    org = await service.create("Acme Security", owner_id)

    member = await service.require_member(org.id, owner_id)
    assert member.role is OrganizationRole.OWNER


@pytest.mark.asyncio
async def test_get_nonexistent_organization_raises(service):
    with pytest.raises(OrganizationNotFoundError):
        await service.get(uuid4())


@pytest.mark.asyncio
async def test_soft_deleted_organization_is_not_returned(service):
    org = await service.create("Acme Security", uuid4())
    await service.soft_delete(org.id)

    with pytest.raises(OrganizationNotFoundError):
        await service.get(org.id)


@pytest.mark.asyncio
async def test_require_member_raises_for_non_member(service):
    org = await service.create("Acme Security", uuid4())
    with pytest.raises(NotAnOrganizationMemberError):
        await service.require_member(org.id, uuid4())


@pytest.mark.asyncio
async def test_add_member_and_list_members(service):
    owner_id = uuid4()
    new_member_id = uuid4()
    org = await service.create("Acme Security", owner_id)

    await service.add_member(org.id, new_member_id, OrganizationRole.MEMBER)
    members = await service.list_members(org.id)

    member_ids = {m.user_id for m in members}
    assert owner_id in member_ids
    assert new_member_id in member_ids


@pytest.mark.asyncio
async def test_create_invitation_is_pending_by_default(service):
    org = await service.create("Acme Security", uuid4())
    invitation = await service.create_invitation(
        org.id, "newhire@example.com", OrganizationRole.MEMBER, uuid4()
    )

    assert invitation.status is InvitationStatus.PENDING
    invitations = await service.list_invitations(org.id)
    assert invitation.id in {i.id for i in invitations}


@pytest.mark.asyncio
async def test_rename_organization(service):
    org = await service.create("Old Name", uuid4())
    updated = await service.rename(org.id, "New Name")
    assert updated.name == "New Name"

    refetched = await service.get(org.id)
    assert refetched.name == "New Name"
