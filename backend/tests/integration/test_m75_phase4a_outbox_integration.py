"""
M7.5 Phase 4-A — outbox transactional guarantees (real Postgres).

Verifies the invariants fakes cannot model: that an event committed in
the SAME transaction as the domain write is durable, that a rollback
removes it, that a failed event write aborts the domain transition, and
that the declared ON DELETE SET NULL refs let the observation trail
survive the deletion of its subjects.

Skipped automatically when DATABASE_URL is unreachable (see
`tests/integration/conftest.py`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.application.autonomous_service import AutonomousService
from app.application.outbox_service import OutboxService
from app.core.config import get_settings
from app.domain.entities import (
    AuthorizationRecord,
    Organization,
    OrganizationMember,
    Project,
    User,
)
from app.domain.value_objects import AuthorizationStatus, OrganizationRole, ProjectState
from app.infrastructure.db.models.event_outbox import EventOutboxModel
from app.infrastructure.db.models.project import ProjectModel
from app.infrastructure.db.repositories.authorization_repository import (
    SqlAlchemyAuthorizationRecordRepository,
)
from app.infrastructure.db.repositories.autonomous_run_action_repository import (
    SqlAlchemyAutonomousRunActionRepository,
)
from app.infrastructure.db.repositories.autonomous_run_repository import (
    SqlAlchemyAutonomousRunRepository,
)
from app.infrastructure.db.repositories.event_outbox_repository import (
    SqlAlchemyOutboxEventRepository,
)
from app.infrastructure.db.repositories.identity_repository import SqlAlchemyUserRepository
from app.infrastructure.db.repositories.organization_repository import (
    SqlAlchemyOrganizationRepository,
)
from app.infrastructure.db.repositories.project_repository import SqlAlchemyProjectRepository
from tests.integration.conftest import requires_postgres

pytestmark = requires_postgres


async def _seed(db_session: AsyncSession, *, tag: str) -> tuple[Project, User]:
    org_repo = SqlAlchemyOrganizationRepository(db_session)
    user_repo = SqlAlchemyUserRepository(db_session)
    project_repo = SqlAlchemyProjectRepository(db_session)
    auth_repo = SqlAlchemyAuthorizationRecordRepository(db_session)

    org = Organization(id=uuid4(), name=f"M75-P4A Org {tag}", created_at=datetime.now(UTC))
    await org_repo.add(org)

    user = User(
        id=uuid4(),
        email=f"m75p4a-{tag}-{uuid4()}@example.com",
        password_hash="hash",
        full_name="M75 P4A User",
        is_active=True,
        created_at=datetime.now(UTC),
    )
    await user_repo.add(user)
    await org_repo.add_member(
        member=OrganizationMember(
            organization_id=org.id,
            user_id=user.id,
            role=OrganizationRole.OWNER,
            created_at=datetime.now(UTC),
        )
    )

    now = datetime.now(UTC)
    project = Project(
        id=uuid4(),
        organization_id=org.id,
        name=f"M75-P4A Project {tag}",
        description=None,
        state=ProjectState.ACTIVE,
        tags=[],
        client_metadata={},
        created_at=now,
        updated_at=now,
    )
    await project_repo.add(project)

    auth_record = AuthorizationRecord(
        id=uuid4(),
        project_id=project.id,
        client_name="test",
        document_reference="doc",
        authorized_from=datetime.now(UTC).date() - timedelta(days=1),
        authorized_to=datetime.now(UTC).date() + timedelta(days=30),
        allowed_targets=[],
        approved_by=user.id,
        status=AuthorizationStatus.ACTIVE,
        scope_notes=None,
        evidence_pointer=None,
        created_at=now,
    )
    await auth_repo.add(auth_record)
    await db_session.commit()
    return project, user


async def _count_outbox(db_session: AsyncSession, project_id) -> int:
    stmt = select(func.count()).select_from(EventOutboxModel).where(
        EventOutboxModel.project_id == project_id
    )
    result = await db_session.execute(stmt)
    return int(result.scalar_one())


async def _create_run_with_started_event(
    db_session: AsyncSession,
    *,
    project: Project,
    user: User,
):
    """Create a run + a started event in the caller's (open) transaction."""
    autonomous = AutonomousService(
        SqlAlchemyAutonomousRunRepository(db_session),
        SqlAlchemyAutonomousRunActionRepository(db_session),
    )
    run = await autonomous.create(
        project_id=project.id,
        initiated_by=user.id,
        objective="enumerate externally reachable services",
        max_actions=5,
        max_runtime_seconds=600,
    )
    outbox = OutboxService(SqlAlchemyOutboxEventRepository(db_session))
    await outbox.record_campaign_run_started(
        run_id=run.id,
        project_id=run.project_id,
        organization_id=project.organization_id,
        schedule_id=None,
        objective=run.objective,
        max_actions=run.max_actions,
        max_runtime_seconds=run.max_runtime_seconds,
        initiated_by=user.id,
    )
    return run


@pytest.mark.asyncio
async def test_commit_persists_event_with_domain_write(db_session: AsyncSession) -> None:
    """Commit → the outbox row and the run are both durable."""
    project, user = await _seed(db_session, tag="commit")

    run = await _create_run_with_started_event(db_session, project=project, user=user)

    # Not yet durable: a SECOND connection (fresh transaction) must not see
    # the uncommitted event row.
    engine = create_async_engine(str(get_settings().DATABASE_URL))
    try:
        async with engine.connect() as conn:
            visible = await conn.execute(
                select(func.count())
                .select_from(EventOutboxModel)
                .where(EventOutboxModel.autonomous_run_id == run.id)
            )
            assert int(visible.scalar_one()) == 0
    finally:
        await engine.dispose()

    await db_session.commit()

    result = await db_session.execute(
        select(EventOutboxModel).where(EventOutboxModel.autonomous_run_id == run.id)
    )
    row = result.scalars().first()
    assert row is not None
    assert row.event_id is not None
    assert row.event_type == "campaign.run.started"
    assert row.schema_version == 1
    assert row.organization_id == project.organization_id
    assert row.project_id == project.id
    assert row.autonomous_run_id == run.id
    assert row.schedule_id is None
    assert row.payload["project_id"] == str(project.id)
    assert row.payload["max_actions"] == 5


@pytest.mark.asyncio
async def test_rollback_removes_event_with_domain_write(db_session: AsyncSession) -> None:
    """Rollback → neither the run nor the event exists (atomic pair)."""
    project, user = await _seed(db_session, tag="rollback")

    run = await _create_run_with_started_event(db_session, project=project, user=user)
    await db_session.rollback()

    assert await _count_outbox(db_session, project.id) == 0
    run_repo = SqlAlchemyAutonomousRunRepository(db_session)
    assert await run_repo.get(run.id) is None


@pytest.mark.asyncio
async def test_event_write_failure_aborts_domain_transition(db_session: AsyncSession) -> None:
    """A failing event append rolls back the domain change with it."""
    project, user = await _seed(db_session, tag="failure")

    autonomous = AutonomousService(
        SqlAlchemyAutonomousRunRepository(db_session),
        SqlAlchemyAutonomousRunActionRepository(db_session),
    )
    run = await autonomous.create(
        project_id=project.id,
        initiated_by=user.id,
        objective="o",
        max_actions=3,
        max_runtime_seconds=120,
    )

    class _BrokenOutbox:
        async def add(self, event) -> None:
            raise RuntimeError("event persistence failed")

    broken = OutboxService(_BrokenOutbox())
    with pytest.raises(RuntimeError):
        await broken.record_campaign_run_started(
            run_id=run.id,
            project_id=run.project_id,
            organization_id=project.organization_id,
            schedule_id=None,
            objective=run.objective,
            max_actions=run.max_actions,
            max_runtime_seconds=run.max_runtime_seconds,
            initiated_by=user.id,
        )

    await db_session.rollback()

    run_repo = SqlAlchemyAutonomousRunRepository(db_session)
    assert await run_repo.get(run.id) is None
    assert await _count_outbox(db_session, project.id) == 0


@pytest.mark.asyncio
async def test_repository_add_flushes_but_never_commits(db_session: AsyncSession) -> None:
    """`add` leaves the transaction open: flushed (visible in-session) but
    absent after a rollback."""
    project, user = await _seed(db_session, tag="flush")
    await _create_run_with_started_event(db_session, project=project, user=user)
    assert await _count_outbox(db_session, project.id) == 1

    await db_session.rollback()
    assert await _count_outbox(db_session, project.id) == 0


@pytest.mark.asyncio
async def test_event_survives_referenced_project_delete(db_session: AsyncSession) -> None:
    """Decoupled observation layer: deleting the project does NOT affect the
    outbox row (no FK → no cascade/SET NULL). The event trail is append-only
    and outlives its subjects."""
    project, user = await _seed(db_session, tag="survive")
    run = await _create_run_with_started_event(db_session, project=project, user=user)
    await db_session.commit()

    result = await db_session.execute(
        select(EventOutboxModel).where(EventOutboxModel.autonomous_run_id == run.id)
    )
    event_id = result.scalars().first().event_id

    await db_session.execute(delete(ProjectModel).where(ProjectModel.id == project.id))
    await db_session.commit()

    result = await db_session.execute(
        select(EventOutboxModel).where(EventOutboxModel.event_id == event_id)
    )
    row = result.scalars().first()
    assert row is not None
    assert row.project_id == project.id
    assert row.organization_id == project.organization_id
    assert row.autonomous_run_id == run.id
    assert row.event_type == "campaign.run.started"