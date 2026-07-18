"""Pydantic v2 request/response schemas for project endpoints."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.value_objects import ProjectRole, ProjectState


class CreateProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255, examples=["Q3 2026 External Pentest"])
    description: str | None = Field(default=None)
    tags: list[str] = Field(default_factory=list, examples=[["external", "web"]])
    client_metadata: dict[str, str] = Field(default_factory=dict)


class UpdateProjectRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    tags: list[str] | None = None
    client_metadata: dict[str, str] | None = None


class TransitionProjectStateRequest(BaseModel):
    state: ProjectState = Field(examples=[ProjectState.AUTHORIZED])


class ProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    organization_id: UUID
    name: str
    description: str | None
    state: ProjectState
    tags: list[str]
    client_metadata: dict[str, str]
    created_at: datetime
    updated_at: datetime


class AddProjectMemberRequest(BaseModel):
    user_id: UUID
    role: ProjectRole = Field(examples=[ProjectRole.TESTER])


class ProjectMemberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    project_id: UUID
    user_id: UUID
    role: ProjectRole
    created_at: datetime
