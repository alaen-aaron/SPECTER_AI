"""
M7.4 Phase 4 — recovery & concurrency integration tests (real Postgres).

Exercises the SQLAlchemy repositories that fakes cannot faithfully model:
the partial-unique one-active-run guard, the transaction-scoped advisory
cycle lock, retry_count / scan-attempt lineage persistence, and a
full reconcile→transport-retry round trip through the real repos.

Skipped automatically when DATABASE_URL is unreachable (see
`tests/integration/conftest.py`) — these are opt-in, not part of the
fast unit loop.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.application.autonomous_recovery import AutonomousRecoveryService
from app.application.autonomous_service import AutonomousService
from app.core.config import get_settings
from app.domain.entities import (
    AutonomousRun,
    AutonomousRunAction,
    Organization,
    PlannedAction,
    Project,
    Scan,
    User,
)
from app.domain.exceptions import AutonomousRunActiveExistsError
from app.domain.value_objects import (
    ActionCategory,
    AutonomousRunStatus,
    PlannedActionStatus,
    ProjectState,
    ScanFailureKind,
    ScanStatus,
)
from app.infrastructure.db.repositories.audit_log_repository import (
    SqlAlchemyAuditLogRepository,
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
from app.infrastructure.db.repositories.planned_action_repository import (
    SqlAlchemyPlannedActionRepository,
)
from app.infrastructure.db.repositories.project_repository import SqlAlchemyProjectRepository
from app.infrastructure.db.repositories.scan_repository import SqlAlchemyScanRepository
from tests.fakes import FakePlannerService
from tests.integration.conftest import requires_postgres

pytestmark = requires_postgres


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _seed_project(db_session: AsyncSession):
    """Create an org + initiator + active project; return their entities."""
    org_repo = SqlAlchemyOrganizationRepository(db_session)
    user_repo = SqlAlchemyUserRepository(db_session)
    project_repo = SqlAlchemyProjectRepository(db_session)

    org = Organization(id=uuid4(), name="M74 Phase4 Org", created_at=datetime.now(UTC))
    await org_repo.add(org)

    initiator = User(
        id=uuid4(),
        email=f"p4-{uuid4()}@example.com",
        password_hash="hash",
        full_name="Phase4 Initiator",
        is_active=True,
        created_at=datetime.now(UTC),
    )
    await user_repo.add(initiator)

    now = datetime.now(UTC)
    project = Project(
        id=uuid4(),
        organization_id=org.id,
        name="M74 Phase4 Project",
        description=None,
        state=ProjectState.ACTIVE,
        tags=[],
        client_metadata={},
        created_at=now,
        updated_at=now,
    )
    await project_repo.add(project)
    return org, initiator, project


def _run(project: Project, initiator: User, now: datetime) -> AutonomousRun:
    return AutonomousRun(
        id=uuid4(),
        project_id=project.id,
        initiated_by=initiator.id,
        status=AutonomousRunStatus.EXECUTING,
        objective="phase4 integration",
        max_actions=5,
        max_runtime_seconds=300,
        started_at=now,
        created_at=now,
    )


def _persist_launcher(scan_repo: SqlAlchemyScanRepository):
    """Mimic ScanService.create: persist the scan row, then return it."""

    async def launch(
        *,
        project_id: UUID,
        plugin_name: str,
        plugin_config: dict[str, object],
        target_ids: list[UUID],
        initiated_by: UUID,
    ) -> Scan:
        scan = Scan(
            id=uuid4(),
            project_id=project_id,
            initiated_by=initiated_by,
            plugin=plugin_name,
            status=ScanStatus.QUEUED,
            target_ids=list(target_ids),
            plugin_config=dict(plugin_config),
            created_at=datetime.now(UTC),
        )
        await scan_repo.create(scan)
        return scan

    return launch


# ---------------------------------------------------------------------------
# Failure-kind persistence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_failure_kind_round_trips_through_postgres(db_session) -> None:
    _, initiator, project = await _seed_project(db_session)
    scan_repo = SqlAlchemyScanRepository(db_session)
    scan = Scan(
        id=uuid4(),
        project_id=project.id,
        initiated_by=initiator.id,
        plugin="ping",
        status=ScanStatus.QUEUED,
        target_ids=[uuid4()],
        plugin_config={},
        created_at=datetime.now(UTC),
    )
    await scan_repo.create(scan)

    await scan_repo.fail(scan.id, "executor unreachable", 0, ScanFailureKind.TRANSPORT)
    failed = await scan_repo.get(scan.id)
    assert failed is not None
    assert failed.status is ScanStatus.FAILED
    assert failed.failure_kind is ScanFailureKind.TRANSPORT
    assert failed.error_message == "executor unreachable"


# ---------------------------------------------------------------------------
# One-active-run-per-project guard (partial unique index)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_autonomous_one_active_run_enforced_by_storage(db_session) -> None:
    _, initiator, project = await _seed_project(db_session)
    run_repo = SqlAlchemyAutonomousRunRepository(db_session)
    now = datetime.now(UTC)
    first = _run(project, initiator, now)
    await run_repo.create(first)
    await db_session.commit()  # first run is durable before the conflict test

    second = _run(project, initiator, now)
    with pytest.raises(AutonomousRunActiveExistsError):
        await run_repo.create(second)
    # The aborted server transaction must be rolled back before the
    # session can serve further statements (mirrors the request-scoped
    # session teardown in production, which ends right after the 409).
    await db_session.rollback()

    active = await run_repo.get_active_for_project(project.id)
    assert active is not None
    assert active.id == first.id


@pytest.mark.asyncio
async def test_active_slot_frees_when_run_terminates(db_session) -> None:
    _, initiator, project = await _seed_project(db_session)
    run_repo = SqlAlchemyAutonomousRunRepository(db_session)
    now = datetime.now(UTC)
    first = _run(project, initiator, now)
    await run_repo.create(first)

    first.status = AutonomousRunStatus.FAILED
    first.completed_at = now
    first.error_message = "settled"
    await run_repo.update(first)

    second = _run(project, initiator, now)
    await run_repo.create(second)  # the partial unique index freed the slot
    assert second.id is not None


# ---------------------------------------------------------------------------
# Durable cycle lock — transaction-scoped advisory lock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cycle_lock_is_transaction_scoped(db_session) -> None:
    _, initiator, project = await _seed_project(db_session)
    run_repo = SqlAlchemyAutonomousRunRepository(db_session)
    run = _run(project, initiator, datetime.now(UTC))
    await run_repo.create(run)
    await db_session.commit()

    engine = create_async_engine(str(get_settings().DATABASE_URL))
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        async with session_factory() as other:
            other_repo = SqlAlchemyAutonomousRunRepository(other)

            # Held: session A's transaction owns the lock; B cannot take it.
            assert await run_repo.try_cycle_lock(run.id) is True
            assert await other_repo.try_cycle_lock(run.id) is False

            # Released at commit: a fresh transaction may re-acquire.
            await db_session.commit()
            assert await other_repo.try_cycle_lock(run.id) is True
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Retry accounting persists (retry_count + scan-attempt lineage)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_accounting_persists_and_budget_stays_idempotent(
    db_session,
) -> None:
    _, initiator, project = await _seed_project(db_session)
    run_repo = SqlAlchemyAutonomousRunRepository(db_session)
    action_repo = SqlAlchemyAutonomousRunActionRepository(db_session)
    scan_repo = SqlAlchemyScanRepository(db_session)
    pa_repo = SqlAlchemyPlannedActionRepository(db_session)
    now = datetime.now(UTC)
    run = _run(project, initiator, now)
    await run_repo.create(run)

    # Real FK rows the action must reference: a planned action (M7.2) and
    # the executed scan the retry replaces.
    pa = PlannedAction(
        id=uuid4(),
        project_id=project.id,
        action_type="recon",
        title="probe",
        description="",
        justification="",
        plugin="ping",
        target_ids=[uuid4()],
        status=PlannedActionStatus.EXECUTED,
        created_by=initiator.id,
        objective=run.objective,
        risk_level="low",
    )
    await pa_repo.create(pa)
    origin_scan = Scan(
        id=uuid4(),
        project_id=project.id,
        initiated_by=initiator.id,
        plugin="ping",
        status=ScanStatus.COMPLETED,
        target_ids=list(pa.target_ids),
        plugin_config={},
        created_at=now,
        completed_at=now,
    )
    await scan_repo.create(origin_scan)

    action = AutonomousRunAction(
        id=uuid4(),
        run_id=run.id,
        project_id=project.id,
        cycle=1,
        action_type="recon",
        plugin="ping",
        title="probe",
        target_ids=list(pa.target_ids),
        category=ActionCategory.CATEGORY_2,
        status="executed",
        planned_action_id=pa.id,
        scan_id=origin_scan.id,
        retry_count=0,
        created_at=now,
    )
    await action_repo.create(action)

    svc = AutonomousService(run_repo=run_repo, action_repo=action_repo)
    retry_scan = Scan(
        id=uuid4(),
        project_id=project.id,
        initiated_by=initiator.id,
        plugin="ping",
        status=ScanStatus.QUEUED,
        target_ids=list(pa.target_ids),
        plugin_config={},
        created_at=now,
    )
    await scan_repo.create(retry_scan)
    await svc.retry_action_execution(action.id, retry_scan.id, max_retries=1)

    reloaded = await action_repo.get(action.id)
    assert reloaded is not None
    assert reloaded.retry_count == 1
    assert reloaded.scan_id == retry_scan.id
    assert reloaded.result_summary["scan_attempt_ids"] == [str(origin_scan.id)]

    # Re-recording the same execution must not double-increment the budget.
    await svc.record_action_execution(action.id, retry_scan.id)
    refetched_run = await run_repo.get(run.id)
    assert refetched_run.actions_completed == 0  # retry path never increments


# ---------------------------------------------------------------------------
# list_stale_active — anchor & terminal semantics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_stale_active_excludes_terminal_and_recent(db_session) -> None:
    run_repo = SqlAlchemyAutonomousRunRepository(db_session)
    now = datetime.now(UTC)

    # Each run needs its own project: two non-terminal runs in ONE project
    # would violate the partial-unique active-run guard, not the point here.
    _, initiator, stale_project = await _seed_project(db_session)
    stale = _run(stale_project, initiator, now - timedelta(minutes=30))
    await run_repo.create(stale)

    _, initiator2, recent_project = await _seed_project(db_session)
    recent = _run(recent_project, initiator2, now)
    await run_repo.create(recent)

    _, initiator3, done_project = await _seed_project(db_session)
    completed = _run(done_project, initiator3, now - timedelta(hours=2))
    completed.status = AutonomousRunStatus.COMPLETED
    completed.completed_at = now - timedelta(hours=2)
    await run_repo.create(completed)

    # This query is global (the supervisor has no project filter), so
    # assert on MY runs' membership and their RELATIVE order — other
    # residue in a long-lived dev DB is not our concern.
    stale_list = await run_repo.list_stale_active(now - timedelta(minutes=10))
    ids = {r.id for r in stale_list}
    assert stale.id in ids
    assert recent.id not in ids
    assert completed.id not in ids

    _, initiator4, older_project = await _seed_project(db_session)
    older = _run(older_project, initiator4, now - timedelta(hours=3))
    await run_repo.create(older)
    stale_list = await run_repo.list_stale_active(now - timedelta(minutes=10))
    positions = {r.id: i for i, r in enumerate(stale_list)}
    assert positions[older.id] < positions[stale.id]  # oldest-first


# ---------------------------------------------------------------------------
# Reconcile → transport retry, end-to-end through the real repos
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_transport_retry_end_to_end(db_session) -> None:
    _, initiator, project = await _seed_project(db_session)
    run_repo = SqlAlchemyAutonomousRunRepository(db_session)
    action_repo = SqlAlchemyAutonomousRunActionRepository(db_session)
    scan_repo = SqlAlchemyScanRepository(db_session)
    audit_repo = SqlAlchemyAuditLogRepository(db_session)
    pa_repo = SqlAlchemyPlannedActionRepository(db_session)
    now = datetime.now(UTC)
    run = _run(project, initiator, now)
    run.actions_completed = 1
    await run_repo.create(run)

    # M7.2 planned action (persisted for the FK; the fake planner owns it too).
    pa = PlannedAction(
        id=uuid4(),
        project_id=project.id,
        action_type="recon",
        title="probe",
        description="",
        justification="",
        plugin="ping",
        target_ids=[uuid4()],
        status=PlannedActionStatus.EXECUTED,
        created_by=initiator.id,
        objective=run.objective,
        risk_level="low",
    )
    await pa_repo.create(pa)

    failed_scan = Scan(
        id=uuid4(),
        project_id=project.id,
        initiated_by=initiator.id,
        plugin="ping",
        status=ScanStatus.FAILED,
        target_ids=list(pa.target_ids),
        plugin_config={},
        created_at=now,
        completed_at=now,
        error_message="executor unreachable at first dispatch",
        failure_kind=ScanFailureKind.TRANSPORT,
    )
    await scan_repo.create(failed_scan)
    action = AutonomousRunAction(
        id=uuid4(),
        run_id=run.id,
        project_id=project.id,
        cycle=1,
        action_type="recon",
        plugin="ping",
        title="probe",
        target_ids=list(pa.target_ids),
        category=ActionCategory.CATEGORY_2,
        status="executed",
        planned_action_id=pa.id,
        scan_id=failed_scan.id,
        retry_count=0,
        created_at=now,
    )
    await action_repo.create(action)
    await db_session.commit()

    planner = FakePlannerService()
    planner._store[pa.id] = pa
    svc = AutonomousService(run_repo=run_repo, action_repo=action_repo)
    recovery = AutonomousRecoveryService(
        autonomous_service=svc,
        planner=planner,
        launcher=_persist_launcher(scan_repo),
        scan_repository=scan_repo,
        audit_repository=audit_repo,
        max_retries_per_action=1,
    )

    outcome = await recovery.reconcile(run.id)

    assert outcome.retried == 1
    retried_action = await action_repo.get(action.id)
    assert retried_action is not None
    assert retried_action.retry_count == 1
    assert retried_action.result_summary["scan_attempt_ids"] == [str(failed_scan.id)]
    assert retried_action.scan_id != failed_scan.id

    # The retry scan is a REAL persisted row, distinct from the original.
    retry_scan = await scan_repo.get(retried_action.scan_id)  # type: ignore[arg-type]
    assert retry_scan is not None
    assert retry_scan.status is ScanStatus.QUEUED

    # Planned action re-armed EXECUTED -> re-ran to EXECUTED via the bridge.
    persisted_pa = await pa_repo.get(pa.id)
    assert persisted_pa is not None
    assert persisted_pa.status is PlannedActionStatus.EXECUTED

    reloaded_run = await run_repo.get(run.id)
    assert reloaded_run.actions_completed == 1  # budget untouched

    # A recovery of a terminal run is a no-op.
    reloaded_run.status = AutonomousRunStatus.COMPLETED
    await run_repo.update(reloaded_run)
    again = await recovery.reconcile(run.id)
    assert again.retried == 0


# ---------------------------------------------------------------------------
# recover_stale — advance to OBSERVING once all executed scans are terminal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recover_stale_advances_when_scans_terminal(db_session) -> None:
    _, initiator, project = await _seed_project(db_session)
    run_repo = SqlAlchemyAutonomousRunRepository(db_session)
    action_repo = SqlAlchemyAutonomousRunActionRepository(db_session)
    scan_repo = SqlAlchemyScanRepository(db_session)
    audit_repo = SqlAlchemyAuditLogRepository(db_session)
    now = datetime.now(UTC)
    run = _run(project, initiator, now - timedelta(minutes=30))
    await run_repo.create(run)

    done_scan = Scan(
        id=uuid4(),
        project_id=project.id,
        initiated_by=initiator.id,
        plugin="ping",
        status=ScanStatus.FAILED,
        target_ids=[uuid4()],
        plugin_config={},
        created_at=now,
        completed_at=now,
        error_message="tool failure (not retryable)",
        failure_kind=ScanFailureKind.TOOL,
    )
    await scan_repo.create(done_scan)
    action = AutonomousRunAction(
        id=uuid4(),
        run_id=run.id,
        project_id=project.id,
        cycle=1,
        action_type="recon",
        plugin="ping",
        title="probe",
        target_ids=list(done_scan.target_ids),
        category=ActionCategory.CATEGORY_2,
        status="executed",
        planned_action_id=None,
        scan_id=done_scan.id,
        retry_count=1,
        created_at=now,
    )
    await action_repo.create(action)
    await db_session.commit()

    svc = AutonomousService(run_repo=run_repo, action_repo=action_repo)
    recovery = AutonomousRecoveryService(
        autonomous_service=svc,
        planner=FakePlannerService(),
        launcher=_persist_launcher(scan_repo),
        scan_repository=scan_repo,
        audit_repository=audit_repo,
        max_retries_per_action=1,
    )

    outcome = await recovery.recover_stale(run.id)

    assert outcome.advanced_to_observing is True
    assert (await run_repo.get(run.id)).status is AutonomousRunStatus.OBSERVING


# ---------------------------------------------------------------------------
# Orchestrator integration: concurrent cycle is blocked by the durable lock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_orchestrator_cycle_fails_closed_when_lock_held(db_session) -> None:
    from app.application.action_classifier import ActionClassificationPolicy
    from app.application.autonomous_orchestrator import AutonomousOrchestrator
    from app.domain.exceptions import AutonomousCycleNotAllowedError

    _, initiator, project = await _seed_project(db_session)
    run_repo = SqlAlchemyAutonomousRunRepository(db_session)
    action_repo = SqlAlchemyAutonomousRunActionRepository(db_session)
    scan_repo = SqlAlchemyScanRepository(db_session)
    audit_repo = SqlAlchemyAuditLogRepository(db_session)
    now = datetime.now(UTC)
    run = _run(project, initiator, now)
    await run_repo.create(run)
    await action_repo.create(
        AutonomousRunAction(
            id=uuid4(),
            run_id=run.id,
            project_id=project.id,
            cycle=1,
            action_type="recon",
            plugin="ping",
            title="probe",
            target_ids=[uuid4()],
            category=ActionCategory.CATEGORY_2,
            status="proposed",
            created_at=now,
        )
    )
    await db_session.commit()

    # A PEER transaction holds the durable cycle lock (mirroring another
    # process mid-cycle). Advisory locks are re-entrant *within* one
    # transaction, so the lock must come from a separate session — the
    # orchestrator's own re-acquire (same session) would succeed by design.
    engine = create_async_engine(str(get_settings().DATABASE_URL))
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        async with session_factory() as peer:
            peer_repo = SqlAlchemyAutonomousRunRepository(peer)
            assert await peer_repo.try_cycle_lock(run.id) is True

            orch = AutonomousOrchestrator(
                autonomous_service=AutonomousService(
                    run_repo=run_repo, action_repo=action_repo
                ),
                planner=FakePlannerService(),
                launcher=_persist_launcher(scan_repo),
                run_repository=run_repo,
                classification=ActionClassificationPolicy(
                    auto_eligible_plugins=frozenset({"ping"})
                ),
                audit_repository=audit_repo,
                cycle_max_actions=3,
                session_timeout_seconds=15.0,
            )
            with pytest.raises(AutonomousCycleNotAllowedError):
                await orch.cycle(run.id)
    finally:
        await engine.dispose()
    await db_session.commit()