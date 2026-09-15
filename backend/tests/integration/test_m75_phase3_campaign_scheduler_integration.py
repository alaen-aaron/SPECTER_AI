"""
M7.5 Phase 3 — campaign scheduler integration tests (real Postgres).

Verifies DB-backed guarantees that fakes cannot model:

  * concurrent claim → exactly one fire + run created
  * claim → fire → mark_run consumes occurrence atomically
  * active-run DB unique index backstop → skip + consume on re-delivery
  * rejected project consumes occurrence (no silent scan)
  * expired campaign schedules are invisible to claimDue

Skipped automatically when DATABASE_URL is unreachable (see
`tests/integration/conftest.py`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.application.autonomous_service import AutonomousService
from app.application.campaign_scheduler_service import CampaignSchedulerService
from app.application.schedule_service import ScheduleService
from app.application.scope_guard_service import ScopeGuardService
from app.core.config import get_settings
from app.domain.entities import (
    AuthorizationRecord,
    CampaignScheduleConfig,
    Organization,
    OrganizationMember,
    Project,
    User,
    Workflow,
)
from app.domain.value_objects import (
    AuthorizationStatus,
    OrganizationRole,
    ProjectState,
    ScheduleFrequency,
    ScheduleKind,
    WorkflowStatus,
)
from app.infrastructure.db.models.autonomous import AutonomousRunModel
from app.infrastructure.db.repositories.audit_log_repository import (
    SqlAlchemyAuditLogRepository,
)
from app.infrastructure.db.repositories.authorization_repository import (
    SqlAlchemyAuthorizationRecordRepository,
)
from app.infrastructure.db.repositories.autonomous_run_action_repository import (
    SqlAlchemyAutonomousRunActionRepository,
)
from app.infrastructure.db.repositories.autonomous_run_repository import (
    SqlAlchemyAutonomousRunRepository,
)
from app.infrastructure.db.repositories.identity_repository import SqlAlchemyUserRepository
from app.infrastructure.db.repositories.organization_repository import (
    SqlAlchemyOrganizationRepository,
)
from app.infrastructure.db.repositories.project_repository import SqlAlchemyProjectRepository
from app.infrastructure.db.repositories.target_repository import SqlAlchemyTargetRepository
from app.infrastructure.db.repositories.workflow_repository import (
    SqlAlchemyScheduleRepository,
    SqlAlchemyWorkflowRepository,
)
from tests.integration.conftest import requires_postgres

pytestmark = requires_postgres


# ── helpers ─────────────────────────────────────────────────────────────────


async def _seed(
    db_session: AsyncSession, *, tag: str, project_state: ProjectState = ProjectState.ACTIVE
) -> tuple[Project, User, Workflow]:
    """Seed org → user → project → auth record → workflow."""
    org_repo = SqlAlchemyOrganizationRepository(db_session)
    user_repo = SqlAlchemyUserRepository(db_session)
    project_repo = SqlAlchemyProjectRepository(db_session)
    auth_repo = SqlAlchemyAuthorizationRecordRepository(db_session)
    workflow_repo = SqlAlchemyWorkflowRepository(db_session)

    org = Organization(id=uuid4(), name=f"M75-P3 Org {tag}", created_at=datetime.now(UTC))
    await org_repo.add(org)

    user = User(
        id=uuid4(),
        email=f"m75p3-{tag}-{uuid4()}@example.com",
        password_hash="hash",
        full_name="M75 P3 User",
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
        name=f"M75-P3 Project {tag}",
        description=None,
        state=project_state,
        tags=[],
        client_metadata={},
        created_at=now,
        updated_at=now,
    )
    await project_repo.add(project)

    if project_state is ProjectState.ACTIVE:
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

    workflow = Workflow(
        id=uuid4(),
        project_id=project.id,
        name=f"M75-P3 WF {tag}",
        description=None,
        status=WorkflowStatus.ACTIVE,
        created_by=user.id,
        created_at=now,
        updated_at=now,
    )
    await workflow_repo.create(workflow)
    return project, user, workflow


async def _make_due_campaign_schedule(
    db_session: AsyncSession,
    project: Project,
    user: User,
    *,
    in_past: timedelta,
    expires_at: datetime | None = None,
) -> None:
    sched_repo = SqlAlchemyScheduleRepository(db_session)
    wf_repo = SqlAlchemyWorkflowRepository(db_session)
    service = ScheduleService(sched_repo, wf_repo)

    now = datetime.now(UTC)
    schedule = await service.create(
        workflow_id=None,
        project_id=project.id,
        frequency=ScheduleFrequency.ONCE,
        created_by=user.id,
        kind=ScheduleKind.CAMPAIGN,
        campaign=CampaignScheduleConfig(
            objective="enumerate externally reachable services",
            max_actions=5,
            max_runtime_seconds=600,
        ),
        expires_at=expires_at,
    )
    schedule.next_run_at = now - in_past
    schedule.updated_at = now
    await sched_repo.update(schedule)
    await db_session.commit()


def _owned_by(project: Project, schedules: list) -> list:
    """Filter global claim to this test's project."""
    return [s for s in schedules if s.project_id == project.id]


def _build_scheduler(
    db_session: AsyncSession,
) -> tuple[CampaignSchedulerService, ScheduleService]:
    sched_repo = SqlAlchemyScheduleRepository(db_session)
    wf_repo = SqlAlchemyWorkflowRepository(db_session)
    schedule_service = ScheduleService(sched_repo, wf_repo)

    project_repo = SqlAlchemyProjectRepository(db_session)
    target_repo = SqlAlchemyTargetRepository(db_session)
    auth_repo = SqlAlchemyAuthorizationRecordRepository(db_session)
    scope_guard = ScopeGuardService(project_repo, target_repo, auth_repo)

    run_repo = SqlAlchemyAutonomousRunRepository(db_session)
    action_repo = SqlAlchemyAutonomousRunActionRepository(db_session)
    autonomous_svc = AutonomousService(run_repo, action_repo)

    audit_repo = SqlAlchemyAuditLogRepository(db_session)

    scheduler = CampaignSchedulerService(
        schedule_service,
        autonomous_svc,
        scope_guard,
        audit_repo,
    )
    return scheduler, schedule_service


async def _count_active_runs(db_session: AsyncSession, project_id) -> int:
    terminal_statuses = {
        "completed",
        "cancelled",
        "failed",
    }
    stmt = select(AutonomousRunModel).where(
        AutonomousRunModel.project_id == project_id,
        AutonomousRunModel.status.notin_(terminal_statuses),
    )
    result = await db_session.execute(stmt)
    return len(result.scalars().all())


# ── tests ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_campaign_fire_commits_run_and_consumes_occurrence(db_session: AsyncSession) -> None:
    """The happy path: claim → fire → create run + audit + mark_run
    should all land in a single committed transaction."""
    project, user, _ = await _seed(db_session, tag="fire")
    await _make_due_campaign_schedule(db_session, project, user, in_past=timedelta(hours=1))

    scheduler, schedule_svc = _build_scheduler(db_session)
    sched_repo = SqlAlchemyScheduleRepository(db_session)

    claimed = _owned_by(project, await sched_repo.claim_due(datetime.now(UTC), limit=50))
    assert len(claimed) == 1

    result = await scheduler.fire(claimed[0])
    assert result.outcome.value == "fired"
    assert result.run_id is not None

    # mark_run in same transaction as the claim.
    await schedule_svc.mark_run(claimed[0].id)
    await db_session.commit()

    # verify run exists in DB.
    run_repo = SqlAlchemyAutonomousRunRepository(db_session)
    run = await run_repo.get(result.run_id)
    assert run is not None
    assert run.project_id == project.id

    # schedule consumed: next fire finds nothing.
    again = _owned_by(project, await sched_repo.claim_due(datetime.now(UTC), limit=50))
    assert again == []


@pytest.mark.asyncio
async def test_concurrent_beat_claim_one_winner(
    db_session: AsyncSession,
) -> None:
    """Two sessions claim due campaign schedule concurrently; only one
    fires, following FOR UPDATE SKIP LOCKED semantics."""
    project, user, _ = await _seed(db_session, tag="race")
    await _make_due_campaign_schedule(db_session, project, user, in_past=timedelta(hours=1))

    engine = create_async_engine(str(get_settings().DATABASE_URL))
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        async with session_factory() as other:
            repo_a = SqlAlchemyScheduleRepository(db_session)
            repo_b = SqlAlchemyScheduleRepository(other)

            claimed_a = _owned_by(project, await repo_a.claim_due(datetime.now(UTC), limit=50))
            assert len(claimed_a) == 1

            # B sees locked row → SKIP LOCKED → nothing.
            claimed_b = _owned_by(project, await repo_b.claim_due(datetime.now(UTC), limit=50))
            assert claimed_b == []

            # A fires + mark_run + commits.
            scheduler_a, schedule_svc_a = _build_scheduler(db_session)
            await scheduler_a.fire(claimed_a[0])
            await schedule_svc_a.mark_run(claimed_a[0].id)
            await db_session.commit()

            assert await _count_active_runs(db_session, project.id) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_active_run_backstop_skips_duplicate_fire(
    db_session: AsyncSession,
) -> None:
    """When an active autonomous run already exists for the project, a
    concurrent fire is an auditable skip, not a duplicate run."""
    project, user, _ = await _seed(db_session, tag="backstop")

    # Seed one active run directly via the repo.
    run_repo = SqlAlchemyAutonomousRunRepository(db_session)
    action_repo = SqlAlchemyAutonomousRunActionRepository(db_session)
    autonomous_svc = AutonomousService(run_repo, action_repo)
    await autonomous_svc.create(
        project_id=project.id,
        initiated_by=user.id,
        objective="first",
        max_actions=3,
        max_runtime_seconds=120,
    )
    await db_session.commit()

    # A second campaign schedule for the same project is claimed + fired.
    await _make_due_campaign_schedule(db_session, project, user, in_past=timedelta(hours=2))
    scheduler, schedule_svc = _build_scheduler(db_session)
    sched_repo = SqlAlchemyScheduleRepository(db_session)

    claimed = _owned_by(project, await sched_repo.claim_due(datetime.now(UTC), limit=50))
    assert len(claimed) == 1

    result = await scheduler.fire(claimed[0])
    assert result.outcome.value == "skipped_active_run"

    await schedule_svc.mark_run(claimed[0].id)
    await db_session.commit()

    # DB unique index enforced: exactly one active run.
    assert await _count_active_runs(db_session, project.id) == 1

    # No second run was created (result.run_id is None on a skip).
    assert result.run_id is None


@pytest.mark.asyncio
async def test_rejected_project_consumes_occurrence(db_session: AsyncSession) -> None:
    """Project missing active authorization → scope guard rejects →
    occurrence consumed, audit written, no run created."""
    project, user, _ = await _seed(db_session, tag="reject", project_state=ProjectState.DRAFT)
    await _make_due_campaign_schedule(db_session, project, user, in_past=timedelta(hours=1))

    scheduler, schedule_svc = _build_scheduler(db_session)
    sched_repo = SqlAlchemyScheduleRepository(db_session)

    claimed = _owned_by(project, await sched_repo.claim_due(datetime.now(UTC), limit=50))
    assert len(claimed) == 1

    result = await scheduler.fire(claimed[0])
    assert result.outcome.value == "rejected"
    assert result.reason is not None
    assert result.run_id is None

    await schedule_svc.mark_run(claimed[0].id)
    await db_session.commit()

    # No autonomous run created.
    assert await _count_active_runs(db_session, project.id) == 0

    # Schedule consumed.
    again = _owned_by(project, await sched_repo.claim_due(datetime.now(UTC), limit=50))
    assert again == []


@pytest.mark.asyncio
async def test_expired_campaign_schedule_invisible(db_session: AsyncSession) -> None:
    """An expired campaign schedule is never claimed."""
    project, user, _ = await _seed(db_session, tag="expiry")
    await _make_due_campaign_schedule(
        db_session,
        project,
        user,
        in_past=timedelta(hours=1),
        expires_at=datetime.now(UTC) - timedelta(minutes=30),
    )

    sched_repo = SqlAlchemyScheduleRepository(db_session)
    claimed = _owned_by(project, await sched_repo.claim_due(datetime.now(UTC), limit=50))
    assert claimed == []


@pytest.mark.asyncio
async def test_rollback_leaves_schedule_due(db_session: AsyncSession) -> None:
    """A fire that aborts before commit leaves the schedule claimable."""
    project, user, _ = await _seed(db_session, tag="rollback")
    await _make_due_campaign_schedule(db_session, project, user, in_past=timedelta(hours=1))

    sched_repo = SqlAlchemyScheduleRepository(db_session)
    claimed = _owned_by(project, await sched_repo.claim_due(datetime.now(UTC), limit=50))
    assert len(claimed) == 1

    # Roll back before commit.
    await db_session.rollback()

    # Schedule still due.
    again = _owned_by(project, await sched_repo.claim_due(datetime.now(UTC), limit=50))
    assert [s.id for s in again] == [claimed[0].id]
