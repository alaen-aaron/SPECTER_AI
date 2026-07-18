"""
Pydantic v2 request/response schemas for authorization-record and
Scope Guard preview endpoints (SRS §16.3).
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.value_objects import AuthorizationStatus


class CreateAuthorizationRecordRequest(BaseModel):
    client_name: str = Field(min_length=1, max_length=255, examples=["Acme Corp"])
    document_reference: str = Field(examples=["s3://evidence/acme-scope-agreement.pdf"])
    authorized_from: date
    authorized_to: date
    allowed_targets: list[str] = Field(
        default_factory=list,
        description="Target VALUES (not IDs) this record covers. Empty list means "
        "'every target belonging to this project is authorized' — an explicit choice.",
        examples=[["10.10.10.0/24", "lab.example.com"]],
    )
    scope_notes: str | None = None
    evidence_pointer: str | None = None


class AuthorizationRecordResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    project_id: UUID
    client_name: str
    document_reference: str
    authorized_from: date
    authorized_to: date
    allowed_targets: list[str]
    approved_by: UUID
    status: AuthorizationStatus
    scope_notes: str | None
    evidence_pointer: str | None
    created_at: datetime


class ScopeCheckRequest(BaseModel):
    """
    Milestone 2 preview request body — validates a hypothetical set of
    targets against Scope Guard without launching anything. The real
    scan-launch endpoint (SRS §6.2 `POST /projects/{id}/scans`) is a
    Phase 2 deliverable that will call the exact same
    `ScopeGuardService.validate_targets` used here.
    """

    target_ids: list[UUID] = Field(min_length=1)


class ScopeCheckResponse(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)

    project_id: UUID
    authorization_record_id: UUID
    validated_target_ids: list[UUID]
