"""
M7.5 Phase 1 — schedule fire-lock & cron-persistence integration tests
(real Postgres).

The FakeScheduleRepository cannot model `FOR UPDATE SKIP LOCKED` (an
in-memory dict has no row locks), so these tests exercise the real
SQLAlchemy repository + transaction semantics that harden the scheduler:

  * two concurrent beats claiming one due row -> exactly one wins
  * the claim is transaction-scoped: it releases on commit and survives
    a rollback (schedule stays due -> at-least-once, never wedged)
  * an expired schedule is invisible to the claimer (deadline never fires)
  * a successful mark_run (@commit) advances next_run_at so the next
    claim finds nothing.

Skipped automatically when DATABASE_URL is unreachable (see
`tests/integration/conftest.py`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.application.schedule_service import ScheduleService
from app.core.config import get_settings
from app.domain.entities import (
    Organization,
    Project,
    User,
    Workflow,
)
from app.domain.value_objects import ProjectState, ScheduleFrequency, WorkflowStatus
from app.infrastructure.db.models.workflow import ScheduleModel
from app.infrastructure.db.repositories.identity_repository import SqlAlchemyUserRepository
from app.infrastructure.db.repositories.organization_repository import (
    SqlAlchemyOrganizationRepository,
)
from app.infrastructure.db.repositories.project_repository import SqlAlchemyProjectRepository
from app.infrastructure.db.repositories.workflow_repository import (
    SqlAlchemyScheduleRepository,
    SqlAlchemyWorkflowRepository,
)
from tests.integration.conftest import requires_postgres

pytestmark = requires_postgres


async def _seed(db_session: AsyncSession, *, tag: str) -> tuple[Project, User, Workflow]:
    org_repo = SqlAlchemyOrganizationRepository(db_session)
    user_repo = SqlAlchemyUserRepository(db_session)
    project_repo = SqlAlchemyProjectRepository(db_session)
    workflow_repo = SqlAlchemyWorkflowRepository(db_session)

    org = Organization(id=uuid4(), name=f"M75 Org {tag}", created_at=datetime.now(UTC))
    await org_repo.add(org)

    initiator = User(
        id=uuid4(),
        email=f"m75-{tag}-{uuid4()}@example.com",
        password_hash="hash",
        full_name="M75 Initiator",
        is_active=True,
        created_at=datetime.now(UTC),
    )
    await user_repo.add(initiator)

    now = datetime.now(UTC)
    project = Project(
        id=uuid4(),
        organization_id=org.id,
        name=f"M75 Project {tag}",
        description=None,
        state=ProjectState.ACTIVE,
        tags=[],
        client_metadata={},
        created_at=now,
        updated_at=now,
    )
    await project_repo.add(project)

    workflow = Workflow(
        id=uuid4(),
        project_id=project.id,
        name=f"M75 Workflow {tag}",
        description=None,
        status=WorkflowStatus.ACTIVE,
        created_by=initiator.id,
        created_at=now,
        updated_at=now,
    )
    await workflow_repo.create(workflow)
    return project, initiator, workflow


async def _make_due_schedule(
    db_session: AsyncSession,
    project: Project,
    workflow: Workflow,
    *,
    in_past: timedelta,
    expires_at: datetime | None = None,
) -> datetime:
    """Persist a fresh ONCE schedule whose next run is already due."""
    sched_repo = SqlAlchemyScheduleRepository(db_session)
    service = ScheduleService(sched_repo, SqlAlchemyWorkflowRepository(db_session))
    now = datetime.now(UTC)
    schedule = await service.create(
        project_id=project.id,
        workflow_id=workflow.id,
        frequency=ScheduleFrequency.ONCE,
        created_by=workflow.created_by,
        expires_at=expires_at,
    )
    schedule.next_run_at = now - in_past
    schedule.updated_at = now
    await sched_repo.update(schedule)
    await db_session.commit()
    return now


def _owned_by(project: Project, schedules: list) -> list:
    """quarantine: claim_due is global and the dev DB may hold residue."""
    return [s for s in schedules if s.project_id == project.id]


async def _purge_project_schedules(db_session: AsyncSession, project: Project) -> None:
    """delete residue schedules from prior runs of this project's tests."""
    repo = SqlAlchemyScheduleRepository(db_session)
    for schedule in await repo.list_for_project(project.id):
        await repo.delete(schedule.id)
    await db_session.commit()


@pytest.mark.asyncio
async def test_concurrent_beat_claim_is_exactly_one_winner(db_session) -> None:
    project, _, workflow = await _seed(db_session, tag="race")
    await _purge_project_schedules(db_session, project)
    await _make_due_schedule(db_session, project, workflow, in_past=timedelta(hours=1))

    engine = create_async_engine(str(get_settings().DATABASE_URL))
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        async with session_factory() as other:
            repo_a = SqlAlchemyScheduleRepository(db_session)
            repo_b = SqlAlchemyScheduleRepository(other)

            # Beat A claims first: its transaction owns the row lock.
            claimed = _owned_by(project, await repo_a.claim_due(datetime.now(UTC), limit=50))
            assert len(claimed) == 1

            # Beat B (a real concurrent transaction) sees the ROW but SKIP LOCKED
            # skips it -> B gets nothing, so the occurrence fires exactly once.
            other_claimed = _owned_by(project, await repo_b.claim_due(datetime.now(UTC), limit=50))
            assert other_claimed == []

            # A commits (e.g. mark_run sliding next_run forward): the lock is
            # released with the transaction — B's next tick may reclaim.
            await db_session.commit()

            # mark_run moves the ONCE schedule to inactive+next_run=None, so
            # even though the claim lock is gone there is nothing left to fire.
            now = datetime.now(UTC)
            schedule_id = claimed[0].id
            service = ScheduleService(repo_a, SqlAlchemyWorkflowRepository(db_session))
            await service.mark_run(schedule_id)
            await db_session.commit()

            assert _owned_by(project, await repo_b.claim_due(now, limit=50)) == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_rollback_leaves_schedule_due(db_session) -> None:
    """A failed beat (rollback before mark_run) must not consume the run."""
    project, _, workflow = await _seed(db_session, tag="rollback")
    await _purge_project_schedules(db_session, project)
    await _make_due_schedule(db_session, project, workflow, in_past=timedelta(hours=1))

    repo_a = SqlAlchemyScheduleRepository(db_session)
    claimed = _owned_by(project, await repo_a.claim_due(datetime.now(UTC), limit=50))
    assert len(claimed) == 1

    # Simulate a failure: the beat dies without committing.
    await db_session.rollback()

    # Next tick: still due (claim died with the transaction).
    again = _owned_by(project, await repo_a.claim_due(datetime.now(UTC), limit=50))
    assert [s.id for s in again] == [claimed[0].id]


@pytest.mark.asyncio
async def test_claim_excludes_expired_schedules(db_session) -> None:
    project, _, workflow = await _seed(db_session, tag="expiry")
    await _purge_project_schedules(db_session, project)
    now = await _make_due_schedule(db_session, project, workflow, in_past=timedelta(hours=1))
    await _make_due_schedule(
        db_session,
        project,
        workflow,
        in_past=timedelta(hours=1),
        expires_at=now - timedelta(minutes=30),
    )

    repo_a = SqlAlchemyScheduleRepository(db_session)
    claimed = _owned_by(project, await repo_a.claim_due(datetime.now(UTC), limit=50))
    # Only the schedule whose deadline is still in the future is claimable.
    assert len(claimed) == 1
    assert claimed[0].expires_at is None


@pytest.mark.asyncio
async def test_claim_limits_batch_size(db_session) -> None:
    project, _, workflow = await _seed(db_session, tag="batch")
    # The batch limit applies to the GLOBAL claim queue (any project), so the
    # test must be hermetic against residue schedules from prior dev-DB runs.
    await db_session.execute(delete(ScheduleModel))
    await db_session.commit()
    for _ in range(3):
        await _make_due_schedule(db_session, project, workflow, in_past=timedelta(hours=1))

    repo_a = SqlAlchemyScheduleRepository(db_session)
    assert len(await repo_a.claim_due(datetime.now(UTC), limit=2)) == 2
