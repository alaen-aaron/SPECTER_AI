"""
Unit tests for `ProjectService`, focused on the state-transition gate
(FR-2.2/FR-2.3): a project cannot become Active without a currently-
valid AuthorizationRecord attached.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest

from app.application.project_service import ProjectService
from app.domain.entities import AuthorizationRecord
from app.domain.exceptions import InvalidProjectStateTransitionError, ProjectNotAuthorizedError
from app.domain.value_objects import AuthorizationStatus, ProjectState
from tests.fakes import FakeAuthorizationRecordRepository, FakeProjectRepository


@pytest.fixture
def project_repo() -> FakeProjectRepository:
    return FakeProjectRepository()


@pytest.fixture
def auth_repo() -> FakeAuthorizationRecordRepository:
    return FakeAuthorizationRecordRepository()


@pytest.fixture
def service(project_repo, auth_repo) -> ProjectService:
    return ProjectService(project_repo, auth_repo)


def _make_record(project_id, *, days_from: int, days_to: int, status=AuthorizationStatus.ACTIVE):
    return AuthorizationRecord(
        id=uuid4(),
        project_id=project_id,
        client_name="Acme Corp",
        document_reference="s3://evidence/doc.pdf",
        authorized_from=date.today() + timedelta(days=days_from),
        authorized_to=date.today() + timedelta(days=days_to),
        allowed_targets=[],
        approved_by=uuid4(),
        status=status,
        scope_notes=None,
        evidence_pointer=None,
        created_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_create_project_starts_in_draft(service):
    project = await service.create(
        organization_id=uuid4(),
        name="Q3 Pentest",
        description=None,
        tags=None,
        client_metadata=None,
        owner_user_id=uuid4(),
    )
    assert project.state is ProjectState.DRAFT


@pytest.mark.asyncio
async def test_cannot_skip_draft_to_active(service):
    project = await service.create(
        organization_id=uuid4(),
        name="Q3 Pentest",
        description=None,
        tags=None,
        client_metadata=None,
        owner_user_id=uuid4(),
    )
    with pytest.raises(InvalidProjectStateTransitionError):
        await service.transition_state(project.id, ProjectState.ACTIVE)


@pytest.mark.asyncio
async def test_cannot_become_active_without_authorization_record(service):
    project = await service.create(
        organization_id=uuid4(),
        name="Q3 Pentest",
        description=None,
        tags=None,
        client_metadata=None,
        owner_user_id=uuid4(),
    )
    await service.transition_state(project.id, ProjectState.AUTHORIZED)

    with pytest.raises(ProjectNotAuthorizedError):
        await service.transition_state(project.id, ProjectState.ACTIVE)


@pytest.mark.asyncio
async def test_can_become_active_with_valid_authorization_record(service, auth_repo):
    project = await service.create(
        organization_id=uuid4(),
        name="Q3 Pentest",
        description=None,
        tags=None,
        client_metadata=None,
        owner_user_id=uuid4(),
    )
    await service.transition_state(project.id, ProjectState.AUTHORIZED)
    await auth_repo.add(_make_record(project.id, days_from=-1, days_to=30))

    active_project = await service.transition_state(project.id, ProjectState.ACTIVE)
    assert active_project.state is ProjectState.ACTIVE


@pytest.mark.asyncio
async def test_cannot_become_active_with_expired_authorization_record(service, auth_repo):
    project = await service.create(
        organization_id=uuid4(),
        name="Q3 Pentest",
        description=None,
        tags=None,
        client_metadata=None,
        owner_user_id=uuid4(),
    )
    await service.transition_state(project.id, ProjectState.AUTHORIZED)
    await auth_repo.add(_make_record(project.id, days_from=-60, days_to=-30))

    with pytest.raises(ProjectNotAuthorizedError):
        await service.transition_state(project.id, ProjectState.ACTIVE)


@pytest.mark.asyncio
async def test_cannot_become_active_with_revoked_authorization_record(service, auth_repo):
    project = await service.create(
        organization_id=uuid4(),
        name="Q3 Pentest",
        description=None,
        tags=None,
        client_metadata=None,
        owner_user_id=uuid4(),
    )
    await service.transition_state(project.id, ProjectState.AUTHORIZED)
    await auth_repo.add(
        _make_record(project.id, days_from=-1, days_to=30, status=AuthorizationStatus.REVOKED)
    )

    with pytest.raises(ProjectNotAuthorizedError):
        await service.transition_state(project.id, ProjectState.ACTIVE)


@pytest.mark.asyncio
async def test_valid_forward_lifecycle(service, auth_repo):
    project = await service.create(
        organization_id=uuid4(),
        name="Full Lifecycle",
        description=None,
        tags=None,
        client_metadata=None,
        owner_user_id=uuid4(),
    )
    await service.transition_state(project.id, ProjectState.AUTHORIZED)
    await auth_repo.add(_make_record(project.id, days_from=0, days_to=10))

    await service.transition_state(project.id, ProjectState.ACTIVE)
    await service.transition_state(project.id, ProjectState.REPORTING)
    await service.transition_state(project.id, ProjectState.CLOSED)
    final = await service.transition_state(project.id, ProjectState.ARCHIVED)

    assert final.state is ProjectState.ARCHIVED


@pytest.mark.asyncio
async def test_archived_is_terminal(service):
    project = await service.create(
        organization_id=uuid4(),
        name="Terminal Test",
        description=None,
        tags=None,
        client_metadata=None,
        owner_user_id=uuid4(),
    )
    project.state = ProjectState.ARCHIVED  # force into terminal state for this test
    await project_repo_update(service, project)

    with pytest.raises(InvalidProjectStateTransitionError):
        await service.transition_state(project.id, ProjectState.DRAFT)


async def project_repo_update(service: ProjectService, project) -> None:
    """Small helper: reach into the service's repository to persist a
    forced state change, since ProjectService itself has no public
    'force set state' method (by design — production callers must go
    through validated transitions)."""
    await service._projects.update(project)  # noqa: SLF001
