"""
Unit tests for `ScopeGuardService` (SRS §16.3).

This is the safety-critical service in Milestone 2 — every failure
mode listed in the Milestone 2 spec gets its own adversarial test,
matching the SRS §20 testing-strategy requirement that Scope Guard
tests must "always fail closed."
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest

from app.application.scope_guard_service import ScopeGuardService
from app.domain.entities import AuthorizationRecord, Project, Target
from app.domain.exceptions import (
    NoActiveAuthorizationError,
    OutOfScopeTargetError,
    ProjectNotActiveError,
    ProjectNotFoundError,
    TargetNotFoundError,
)
from app.domain.value_objects import AuthorizationStatus, ProjectState, TargetType
from tests.fakes import (
    FakeAuthorizationRecordRepository,
    FakeProjectRepository,
    FakeTargetRepository,
)


def _make_project(state: ProjectState = ProjectState.ACTIVE) -> Project:
    now = datetime.now(UTC)
    return Project(
        id=uuid4(),
        organization_id=uuid4(),
        name="Test Engagement",
        description=None,
        state=state,
        tags=[],
        client_metadata={},
        created_at=now,
        updated_at=now,
    )


def _make_target(project_id, value: str = "10.0.0.5") -> Target:
    now = datetime.now(UTC)
    return Target(
        id=uuid4(),
        project_id=project_id,
        value=value,
        target_type=TargetType.IP,
        in_scope=True,
        created_at=now,
        updated_at=now,
    )


def _make_authorization_record(
    project_id,
    allowed_targets: list[str],
    *,
    status: AuthorizationStatus = AuthorizationStatus.ACTIVE,
    days_from: int = -1,
    days_to: int = 30,
) -> AuthorizationRecord:
    today = date.today()
    return AuthorizationRecord(
        id=uuid4(),
        project_id=project_id,
        client_name="Acme Corp",
        document_reference="s3://evidence/scope-doc.pdf",
        authorized_from=today + timedelta(days=days_from),
        authorized_to=today + timedelta(days=days_to),
        allowed_targets=allowed_targets,
        approved_by=uuid4(),
        status=status,
        scope_notes=None,
        evidence_pointer=None,
        created_at=datetime.now(UTC),
    )


@pytest.fixture
def repos():
    return {
        "projects": FakeProjectRepository(),
        "targets": FakeTargetRepository(),
        "authorizations": FakeAuthorizationRecordRepository(),
    }


def _make_service(repos) -> ScopeGuardService:
    return ScopeGuardService(
        project_repository=repos["projects"],
        target_repository=repos["targets"],
        authorization_repository=repos["authorizations"],
    )


@pytest.mark.asyncio
async def test_validate_targets_succeeds_for_fully_authorized_target(repos):
    project = _make_project()
    target = _make_target(project.id, "10.0.0.5")
    record = _make_authorization_record(project.id, allowed_targets=["10.0.0.5"])

    await repos["projects"].add(project)
    await repos["targets"].add(target)
    await repos["authorizations"].add(record)

    service = _make_service(repos)
    result = await service.validate_targets(project.id, [target.id])

    assert result.project_id == project.id
    assert result.authorization_record_id == record.id
    assert result.validated_target_ids == (target.id,)


@pytest.mark.asyncio
async def test_validate_targets_succeeds_when_allowed_targets_empty(repos):
    """An empty allow-list means 'everything in this project is authorized' —
    an explicit choice recorded at authorization time, not a silent bypass."""
    project = _make_project()
    target = _make_target(project.id)
    record = _make_authorization_record(project.id, allowed_targets=[])

    await repos["projects"].add(project)
    await repos["targets"].add(target)
    await repos["authorizations"].add(record)

    service = _make_service(repos)
    result = await service.validate_targets(project.id, [target.id])
    assert result.validated_target_ids == (target.id,)


@pytest.mark.asyncio
async def test_validate_targets_rejects_nonexistent_project(repos):
    service = _make_service(repos)
    with pytest.raises(ProjectNotFoundError):
        await service.validate_targets(uuid4(), [uuid4()])


@pytest.mark.asyncio
async def test_validate_targets_rejects_non_active_project(repos):
    project = _make_project(state=ProjectState.DRAFT)
    await repos["projects"].add(project)

    service = _make_service(repos)
    with pytest.raises(ProjectNotActiveError):
        await service.validate_targets(project.id, [uuid4()])


@pytest.mark.asyncio
async def test_validate_targets_rejects_missing_authorization(repos):
    project = _make_project()
    target = _make_target(project.id)
    await repos["projects"].add(project)
    await repos["targets"].add(target)
    # deliberately no authorization record added

    service = _make_service(repos)
    with pytest.raises(NoActiveAuthorizationError):
        await service.validate_targets(project.id, [target.id])


@pytest.mark.asyncio
async def test_validate_targets_rejects_expired_authorization(repos):
    project = _make_project()
    target = _make_target(project.id)
    expired_record = _make_authorization_record(
        project.id, allowed_targets=[target.value], days_from=-30, days_to=-1
    )
    await repos["projects"].add(project)
    await repos["targets"].add(target)
    await repos["authorizations"].add(expired_record)

    service = _make_service(repos)
    with pytest.raises(NoActiveAuthorizationError):
        await service.validate_targets(project.id, [target.id])


@pytest.mark.asyncio
async def test_validate_targets_rejects_not_yet_valid_authorization(repos):
    project = _make_project()
    target = _make_target(project.id)
    future_record = _make_authorization_record(
        project.id, allowed_targets=[target.value], days_from=5, days_to=30
    )
    await repos["projects"].add(project)
    await repos["targets"].add(target)
    await repos["authorizations"].add(future_record)

    service = _make_service(repos)
    with pytest.raises(NoActiveAuthorizationError):
        await service.validate_targets(project.id, [target.id])


@pytest.mark.asyncio
async def test_validate_targets_rejects_revoked_authorization(repos):
    project = _make_project()
    target = _make_target(project.id)
    revoked_record = _make_authorization_record(
        project.id, allowed_targets=[target.value], status=AuthorizationStatus.REVOKED
    )
    await repos["projects"].add(project)
    await repos["targets"].add(target)
    await repos["authorizations"].add(revoked_record)

    service = _make_service(repos)
    with pytest.raises(NoActiveAuthorizationError):
        await service.validate_targets(project.id, [target.id])


@pytest.mark.asyncio
async def test_validate_targets_rejects_target_not_in_allowed_list(repos):
    project = _make_project()
    target = _make_target(project.id, "192.168.1.1")
    record = _make_authorization_record(project.id, allowed_targets=["10.0.0.0/8"])

    await repos["projects"].add(project)
    await repos["targets"].add(target)
    await repos["authorizations"].add(record)

    service = _make_service(repos)
    with pytest.raises(OutOfScopeTargetError) as exc_info:
        await service.validate_targets(project.id, [target.id])
    assert target.id in exc_info.value.target_ids


@pytest.mark.asyncio
async def test_validate_targets_rejects_target_belonging_to_different_project(repos):
    project_a = _make_project()
    project_b = _make_project()
    target_in_b = _make_target(project_b.id)
    record_for_a = _make_authorization_record(project_a.id, allowed_targets=[])

    await repos["projects"].add(project_a)
    await repos["projects"].add(project_b)
    await repos["targets"].add(target_in_b)
    await repos["authorizations"].add(record_for_a)

    service = _make_service(repos)
    # Asking to validate project_b's target against project_a's scope check.
    with pytest.raises(TargetNotFoundError):
        await service.validate_targets(project_a.id, [target_in_b.id])


@pytest.mark.asyncio
async def test_validate_targets_rejects_nonexistent_target(repos):
    project = _make_project()
    record = _make_authorization_record(project.id, allowed_targets=[])
    await repos["projects"].add(project)
    await repos["authorizations"].add(record)

    service = _make_service(repos)
    with pytest.raises(TargetNotFoundError):
        await service.validate_targets(project.id, [uuid4()])


@pytest.mark.asyncio
async def test_validate_targets_rejects_mixed_in_scope_and_out_of_scope(repos):
    """One out-of-scope target in a batch must fail the whole batch — no partial success."""
    project = _make_project()
    good_target = _make_target(project.id, "10.0.0.5")
    bad_target = _make_target(project.id, "8.8.8.8")
    record = _make_authorization_record(project.id, allowed_targets=["10.0.0.5"])

    await repos["projects"].add(project)
    await repos["targets"].add(good_target)
    await repos["targets"].add(bad_target)
    await repos["authorizations"].add(record)

    service = _make_service(repos)
    with pytest.raises(OutOfScopeTargetError) as exc_info:
        await service.validate_targets(project.id, [good_target.id, bad_target.id])
    assert bad_target.id in exc_info.value.target_ids
    assert good_target.id not in exc_info.value.target_ids
