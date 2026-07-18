"""Pydantic v2 request/response schemas for organization endpoints."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.domain.value_objects import InvitationStatus, OrganizationRole


class CreateOrganizationRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255, examples=["Acme Security Consulting"])


class RenameOrganizationRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class OrganizationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    name: str
    created_at: datetime


class AddOrganizationMemberRequest(BaseModel):
    user_id: UUID
    role: OrganizationRole = Field(examples=[OrganizationRole.MEMBER])


class OrganizationMemberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    organization_id: UUID
    user_id: UUID
    role: OrganizationRole
    created_at: datetime


class CreateInvitationRequest(BaseModel):
    email: EmailStr
    role: OrganizationRole = Field(examples=[OrganizationRole.MEMBER])


class InvitationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    organization_id: UUID
    email: str
    role: OrganizationRole
    status: InvitationStatus
    created_at: datetime
    expires_at: datetime
