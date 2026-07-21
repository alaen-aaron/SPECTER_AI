"""
Repository-layer integration tests (real Postgres — see
`tests/integration/conftest.py` for the skip-if-unreachable fixture).

These deliberately test things the in-memory fakes cannot: CITEXT
case-insensitive uniqueness, JSONB round-tripping, and actual foreign-
key/cascade behavior.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest

from app.domain.entities import AuthorizationRecord, Organization, Project, Scan, Target, User
from app.domain.value_objects import AuthorizationStatus, ProjectState, ScanStatus, TargetType
from app.infrastructure.db.repositories.authorization_repository import (
    SqlAlchemyAuthorizationRecordRepository,
)
from app.infrastructure.db.repositories.identity_repository import SqlAlchemyUserRepository
from app.infrastructure.db.repositories.organization_repository import (
    SqlAlchemyOrganizationRepository,
)
from app.infrastructure.db.repositories.project_repository import SqlAlchemyProjectRepository
from app.infrastructure.db.repositories.scan_repository import SqlAlchemyScanRepository
from app.infrastructure.db.repositories.target_repository import SqlAlchemyTargetRepository
from tests.integration.conftest import requires_postgres

pytestmark = requires_postgres


@pytest.mark.asyncio
async def test_user_email_lookup_is_case_insensitive(db_session):
    repo = SqlAlchemyUserRepository(db_session)
    user = User(
        id=uuid4(),
        email="Case.Sensitive@Example.com",
        password_hash="hash",
        full_name="Case Test",
        is_active=True,
        created_at=datetime.now(UTC),
    )
    await repo.add(user)

    fetched = await repo.get_by_email("case.sensitive@example.com")
    assert fetched is not None
    assert fetched.id == user.id


@pytest.mark.asyncio
async def test_organization_soft_delete_excludes_from_get(db_session):
    repo = SqlAlchemyOrganizationRepository(db_session)
    org = Organization(id=uuid4(), name="Integration Test Org", created_at=datetime.now(UTC))
    await repo.add(org)

    assert await repo.get_by_id(org.id) is not None
    await repo.soft_delete(org.id)
    assert await repo.get_by_id(org.id) is None


@pytest.mark.asyncio
async def test_project_tags_and_metadata_round_trip_through_jsonb(db_session):
    org_repo = SqlAlchemyOrganizationRepository(db_session)
    project_repo = SqlAlchemyProjectRepository(db_session)

    org = Organization(id=uuid4(), name="JSONB Test Org", created_at=datetime.now(UTC))
    await org_repo.add(org)

    now = datetime.now(UTC)
    project = Project(
        id=uuid4(),
        organization_id=org.id,
        name="JSONB Test Project",
        description="testing jsonb",
        state=ProjectState.DRAFT,
        tags=["external", "web", "critical"],
        client_metadata={"account_manager": "Jane Doe", "priority": "high"},
        created_at=now,
        updated_at=now,
    )
    await project_repo.add(project)

    fetched = await project_repo.get_by_id(project.id)
    assert fetched is not None
    assert fetched.tags == ["external", "web", "critical"]
    assert fetched.client_metadata == {"account_manager": "Jane Doe", "priority": "high"}


@pytest.mark.asyncio
async def test_target_project_cascade_delete_via_project_fk(db_session):
    """
    Verifies the actual `ON DELETE CASCADE` foreign key from
    `targets.project_id` -> `projects.id` (SRS §5.2) — something an
    in-memory fake cannot meaningfully test since it has no real FK
    enforcement.
    """
    org_repo = SqlAlchemyOrganizationRepository(db_session)
    project_repo = SqlAlchemyProjectRepository(db_session)
    target_repo = SqlAlchemyTargetRepository(db_session)

    org = Organization(id=uuid4(), name="Cascade Test Org", created_at=datetime.now(UTC))
    await org_repo.add(org)

    now = datetime.now(UTC)
    project = Project(
        id=uuid4(),
        organization_id=org.id,
        name="Cascade Test Project",
        description=None,
        state=ProjectState.DRAFT,
        tags=[],
        client_metadata={},
        created_at=now,
        updated_at=now,
    )
    await project_repo.add(project)

    target = Target(
        id=uuid4(),
        project_id=project.id,
        value="10.0.0.99",
        target_type=TargetType.IP,
        in_scope=True,
        created_at=now,
        updated_at=now,
    )
    await target_repo.add(target)

    # Delete the project's row directly (hard delete, bypassing the
    # application-layer soft-delete, specifically to exercise the DB-level
    # cascade rule) and confirm the target row is gone too.
    from sqlalchemy import delete

    from app.infrastructure.db.models.project import ProjectModel

    await db_session.execute(delete(ProjectModel).where(ProjectModel.id == project.id))
    await db_session.flush()

    assert await target_repo.get_by_id(target.id) is None


@pytest.mark.asyncio
async def test_authorization_record_active_lookup_respects_date_range(db_session):
    org_repo = SqlAlchemyOrganizationRepository(db_session)
    project_repo = SqlAlchemyProjectRepository(db_session)
    user_repo = SqlAlchemyUserRepository(db_session)
    auth_repo = SqlAlchemyAuthorizationRecordRepository(db_session)

    org = Organization(id=uuid4(), name="Auth Test Org", created_at=datetime.now(UTC))
    await org_repo.add(org)

    approver = User(
        id=uuid4(),
        email="approver@example.com",
        password_hash="hash",
        full_name="Approver",
        is_active=True,
        created_at=datetime.now(UTC),
    )
    await user_repo.add(approver)

    now = datetime.now(UTC)
    project = Project(
        id=uuid4(),
        organization_id=org.id,
        name="Auth Test Project",
        description=None,
        state=ProjectState.DRAFT,
        tags=[],
        client_metadata={},
        created_at=now,
        updated_at=now,
    )
    await project_repo.add(project)

    active_record = AuthorizationRecord(
        id=uuid4(),
        project_id=project.id,
        client_name="Acme",
        document_reference="doc.pdf",
        authorized_from=date.today() - timedelta(days=1),
        authorized_to=date.today() + timedelta(days=10),
        allowed_targets=[],
        approved_by=approver.id,
        status=AuthorizationStatus.ACTIVE,
        scope_notes=None,
        evidence_pointer=None,
        created_at=now,
    )
    await auth_repo.add(active_record)

    fetched = await auth_repo.get_active_for_project(project.id, now)
    assert fetched is not None
    assert fetched.id == active_record.id


@pytest.mark.asyncio
async def test_scan_lifecycle_methods_persist_correctly(db_session):
    """
    Verifies `create`/`get`/`update_status`/`append_log`/`complete`/`fail`
    all round-trip through real Postgres JSONB columns correctly —
    including that `target_ids` (stored as a JSONB array of strings)
    comes back as actual `UUID` objects, not strings.
    """
    org_repo = SqlAlchemyOrganizationRepository(db_session)
    project_repo = SqlAlchemyProjectRepository(db_session)
    user_repo = SqlAlchemyUserRepository(db_session)
    scan_repo = SqlAlchemyScanRepository(db_session)

    org = Organization(id=uuid4(), name="Scan Test Org", created_at=datetime.now(UTC))
    await org_repo.add(org)

    initiator = User(
        id=uuid4(),
        email="initiator@example.com",
        password_hash="hash",
        full_name="Initiator",
        is_active=True,
        created_at=datetime.now(UTC),
    )
    await user_repo.add(initiator)

    now = datetime.now(UTC)
    project = Project(
        id=uuid4(),
        organization_id=org.id,
        name="Scan Test Project",
        description=None,
        state=ProjectState.ACTIVE,
        tags=[],
        client_metadata={},
        created_at=now,
        updated_at=now,
    )
    await project_repo.add(project)

    target_id = uuid4()
    scan = Scan(
        id=uuid4(),
        project_id=project.id,
        initiated_by=initiator.id,
        plugin="echo",
        status=ScanStatus.QUEUED,
        target_ids=[target_id],
        plugin_config={"nested": {"key": "value"}},
        created_at=now,
    )
    await scan_repo.create(scan)

    fetched = await scan_repo.get(scan.id)
    assert fetched is not None
    assert fetched.target_ids == [target_id]
    assert isinstance(fetched.target_ids[0], type(target_id))
    assert fetched.plugin_config == {"nested": {"key": "value"}}

    await scan_repo.update_status(scan.id, ScanStatus.RUNNING)
    running = await scan_repo.get(scan.id)
    assert running.status is ScanStatus.RUNNING
    assert running.started_at is not None

    await scan_repo.append_log(scan.id, "/tmp/some/log/path")
    logged = await scan_repo.get(scan.id)
    assert logged.logs_path == "/tmp/some/log/path"

    await scan_repo.complete(scan.id, 0, "/tmp/some/artifacts")
    completed = await scan_repo.get(scan.id)
    assert completed.status is ScanStatus.COMPLETED
    assert completed.exit_code == 0
    assert completed.artifacts_path == "/tmp/some/artifacts"
    assert completed.completed_at is not None


@pytest.mark.asyncio
async def test_scan_fail_records_error_message(db_session):
    org_repo = SqlAlchemyOrganizationRepository(db_session)
    project_repo = SqlAlchemyProjectRepository(db_session)
    user_repo = SqlAlchemyUserRepository(db_session)
    scan_repo = SqlAlchemyScanRepository(db_session)

    org = Organization(id=uuid4(), name="Scan Fail Org", created_at=datetime.now(UTC))
    await org_repo.add(org)

    initiator = User(
        id=uuid4(),
        email="initiator2@example.com",
        password_hash="hash",
        full_name="Initiator2",
        is_active=True,
        created_at=datetime.now(UTC),
    )
    await user_repo.add(initiator)

    now = datetime.now(UTC)
    project = Project(
        id=uuid4(),
        organization_id=org.id,
        name="Scan Fail Project",
        description=None,
        state=ProjectState.ACTIVE,
        tags=[],
        client_metadata={},
        created_at=now,
        updated_at=now,
    )
    await project_repo.add(project)

    scan = Scan(
        id=uuid4(),
        project_id=project.id,
        initiated_by=initiator.id,
        plugin="ping",
        status=ScanStatus.QUEUED,
        target_ids=[],
        plugin_config={},
        created_at=now,
    )
    await scan_repo.create(scan)

    await scan_repo.fail(scan.id, "binary not found", None)
    failed = await scan_repo.get(scan.id)
    assert failed.status is ScanStatus.FAILED
    assert failed.error_message == "binary not found"
    assert failed.exit_code is None
    assert failed.completed_at is not None
