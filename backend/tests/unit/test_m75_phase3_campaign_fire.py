"""M7.5 Phase 3 — CampaignSchedulerService.fire unit tests.

Every claimed campaign occurrence must resolve to exactly one of three
audited, consuming outcomes (FIRED / SKIPPED_ACTIVE_RUN / REJECTED). The
M7.4 subsystem stays a black box: `AutonomousService` here is a stub that
only models its create-time contract (raise ``AutonomousRunActiveExistsError``
when a run is already active). Unexpected failures are the caller's
(tasks.py) concern — a rollback, tested at the integration layer.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.application.campaign_scheduler_service import (
    CampaignFireOutcome,
    CampaignSchedulerService,
)
from app.domain.entities import (
    AutonomousRun,
    CampaignScheduleConfig,
    Schedule,
)
from app.domain.exceptions import (
    AutonomousRunActiveExistsError,
    NoActiveAuthorizationError,
    ProjectNotActiveError,
    ProjectNotFoundError,
)
from app.domain.value_objects import (
    AutonomousRunStatus,
    ScheduleFrequency,
    ScheduleKind,
)
from tests.fakes import FakeAuditLogRepository


def _make_campaign_schedule(
    *,
    project_id: UUID | None = None,
    creator: UUID | None = None,
    has_creator: bool = True,
    frequency: ScheduleFrequency = ScheduleFrequency.ONCE,
) -> Schedule:
    owner = creator or uuid4()
    return Schedule(
        id=uuid4(),
        project_id=project_id or uuid4(),
        workflow_id=None,
        kind=ScheduleKind.CAMPAIGN,
        frequency=frequency,
        cron_expression=None,
        is_active=True,
        next_run_at=datetime.now(UTC),
        created_by=owner if has_creator else None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        campaign_config=CampaignScheduleConfig(
            objective="enumerate externally reachable services",
            max_actions=10,
            max_runtime_seconds=3600,
        ),
    )


class _FakeScheduleService:
    """Duck-typed against the part of ScheduleService the campaign service uses."""

    def __init__(self) -> None:
        self.marked: list[UUID] = []

    async def mark_run(self, schedule_id: UUID) -> None:
        self.marked.append(schedule_id)


class _FakeScopeGuard:
    def __init__(self, *, exc: Exception | None = None) -> None:
        self._exc = exc
        self.calls: list[tuple[UUID, list]] = []

    async def validate_targets(self, project_id: UUID, target_ids: list) -> None:
        self.calls.append((project_id, target_ids))
        if self._exc is not None:
            raise self._exc


class _FakeAutonomousService:
    """Stands in for AutonomousService.create only (black-box boundary)."""

    def __init__(self, *, active_run: AutonomousRun | None = None) -> None:
        self._active_run = active_run
        self.created: list[AutonomousRun] = []
        self._id_counter = 0

    async def create(
        self,
        *,
        project_id: UUID,
        initiated_by: UUID,
        objective: str = "",
        max_actions: int = 20,
        max_runtime_seconds: int = 1800,
    ) -> AutonomousRun:
        if self._active_run is not None:
            raise AutonomousRunActiveExistsError(project_id)
        self._id_counter += 1
        run = AutonomousRun(
            id=uuid4(),
            project_id=project_id,
            initiated_by=initiated_by,
            status=AutonomousRunStatus.CREATED,
            objective=objective,
            max_actions=max_actions,
            max_runtime_seconds=max_runtime_seconds,
            created_at=datetime.now(UTC),
        )
        self.created.append(run)
        return run


class _FixedClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, tzinfo=UTC)

    def utcnow(self) -> datetime:
        return self.now


def _service(
    *,
    scope_exc: Exception | None = None,
    schedule: _FakeScheduleService | None = None,
    autonomous: _FakeAutonomousService | None = None,
    audit: FakeAuditLogRepository | None = None,
):
    clock = _FixedClock()
    return (
        CampaignSchedulerService(
            schedule_service=schedule or _FakeScheduleService(),
            autonomous_service=autonomous or _FakeAutonomousService(),
            scope_guard=_FakeScopeGuard(exc=scope_exc),
            audit_repository=audit or FakeAuditLogRepository(),
            clock=clock,
            id_factory=lambda: uuid4(),
        ),
        clock,
    )


# --- fired path --------------------------------------------------------------


@pytest.mark.asyncio
async def test_fire_creates_run_and_consumes_occurrence():
    schedule_svc = _FakeScheduleService()
    autonomous = _FakeAutonomousService()
    audit = FakeAuditLogRepository()
    svc, clock = _service(schedule=schedule_svc, autonomous=autonomous, audit=audit)
    schedule = _make_campaign_schedule()

    result = await svc.fire(schedule)

    assert result.outcome is CampaignFireOutcome.FIRED
    assert result.run_id is not None
    assert len(autonomous.created) == 1
    run = autonomous.created[0]
    assert run.project_id == schedule.project_id
    assert run.initiated_by == schedule.created_by
    assert run.objective == schedule.campaign_config.objective
    assert run.max_actions == schedule.campaign_config.max_actions
    assert run.max_runtime_seconds == schedule.campaign_config.max_runtime_seconds
    # The occurrence is consumed in the same unit of work as the run create.
    assert schedule_svc.marked == [schedule.id]
    # Exactly one audit event: campaign_created.
    assert len(audit._entries) == 1
    entry = audit._entries[0]
    assert entry.action == "scheduler.campaign_created"
    assert entry.target_id == schedule.id
    assert entry.actor_id == schedule.created_by
    assert entry.created_at == clock.now
    assert entry.after_state["run_id"] == str(run.id)


@pytest.mark.asyncio
async def test_fire_uses_project_id_as_actor_when_no_creator():
    schedule_svc = _FakeScheduleService()
    audit = FakeAuditLogRepository()
    svc, _ = _service(schedule=schedule_svc, audit=audit)
    schedule = _make_campaign_schedule(has_creator=False)

    result = await svc.fire(schedule)

    assert result.outcome is CampaignFireOutcome.FIRED
    assert audit._entries[0].actor_id == schedule.project_id


@pytest.mark.asyncio
async def test_fire_rejects_non_campaign_schedule():
    svc, _ = _service()
    schedule = _make_campaign_schedule()
    schedule.kind = ScheduleKind.WORKFLOW

    with pytest.raises(ValueError):
        await svc.fire(schedule)


@pytest.mark.asyncio
async def test_fire_rejects_campaign_schedule_missing_config():
    svc, _ = _service()
    schedule = _make_campaign_schedule()
    schedule.campaign_config = None

    with pytest.raises(ValueError):
        await svc.fire(schedule)


# --- skip path (active run invariant) ---------------------------------------


@pytest.mark.asyncio
async def test_fire_skips_when_active_run_exists():
    schedule_svc = _FakeScheduleService()
    autonomous = _FakeAutonomousService(active_run=AutonomousRun(
        id=uuid4(),
        project_id=uuid4(),
        initiated_by=uuid4(),
        status=AutonomousRunStatus.PLANNING,
        objective="existing",
        max_actions=5,
        max_runtime_seconds=900,
        created_at=datetime.now(UTC),
    ))
    audit = FakeAuditLogRepository()
    svc, clock = _service(schedule=schedule_svc, autonomous=autonomous, audit=audit)
    schedule = _make_campaign_schedule()

    result = await svc.fire(schedule)

    assert result.outcome is CampaignFireOutcome.SKIPPED_ACTIVE_RUN
    assert result.run_id is None
    assert autonomous.created == []
    # Consumed (skip marks the occurrence), never re-fired.
    assert schedule_svc.marked == [schedule.id]
    assert len(audit._entries) == 1
    entry = audit._entries[0]
    assert entry.action == "scheduler.campaign_skipped_active_run"
    assert entry.target_id == schedule.id
    assert entry.created_at == clock.now


@pytest.mark.asyncio
async def test_fire_skips_when_repo_raises_active_exists_directly():
    # The stub can also model the DB-level invariant by raising before insert.
    schedule_svc = _FakeScheduleService()
    audit = FakeAuditLogRepository()

    class _RaceFake:
        async def create(self, **kwargs):
            raise AutonomousRunActiveExistsError(kwargs["project_id"])

    svc = CampaignSchedulerService(
        schedule_service=schedule_svc,
        autonomous_service=_RaceFake(),  # type: ignore[arg-type]
        scope_guard=_FakeScopeGuard(),
        audit_repository=audit,
        clock=_FixedClock(),
    )
    schedule = _make_campaign_schedule()

    result = await svc.fire(schedule)

    assert result.outcome is CampaignFireOutcome.SKIPPED_ACTIVE_RUN
    assert schedule_svc.marked == [schedule.id]
    assert audit._entries[0].action == "scheduler.campaign_skipped_active_run"


# --- reject path (scope guard preflight) -------------------------------------


@pytest.mark.asyncio
async def test_fire_rejects_when_project_missing():
    schedule_svc = _FakeScheduleService()
    audit = FakeAuditLogRepository()
    svc, clock = _service(
        schedule=schedule_svc,
        scope_exc=ProjectNotFoundError(uuid4()),
        audit=audit,
    )
    schedule = _make_campaign_schedule()

    result = await svc.fire(schedule)

    assert result.outcome is CampaignFireOutcome.REJECTED
    assert result.reason is not None and "ProjectNotFoundError" in result.reason
    assert schedule_svc.marked == [schedule.id]
    assert len(audit._entries) == 1
    entry = audit._entries[0]
    assert entry.action == "scheduler.campaign_rejected"
    assert entry.target_id == schedule.id
    assert entry.created_at == clock.now
    assert entry.after_state["project_id"] == str(schedule.project_id)


@pytest.mark.asyncio
async def test_fire_rejects_when_project_inactive():
    schedule_svc = _FakeScheduleService()
    audit = FakeAuditLogRepository()
    svc, _ = _service(
        schedule=schedule_svc,
        scope_exc=ProjectNotActiveError(uuid4(), "archived"),
        audit=audit,
    )
    schedule = _make_campaign_schedule()

    result = await svc.fire(schedule)

    assert result.outcome is CampaignFireOutcome.REJECTED
    assert schedule_svc.marked == [schedule.id]
    assert audit._entries[0].action == "scheduler.campaign_rejected"


@pytest.mark.asyncio
async def test_fire_rejects_when_no_active_authorization():
    schedule_svc = _FakeScheduleService()
    audit = FakeAuditLogRepository()
    svc, _ = _service(
        schedule=schedule_svc,
        scope_exc=NoActiveAuthorizationError(uuid4()),
        audit=audit,
    )
    schedule = _make_campaign_schedule()

    result = await svc.fire(schedule)

    assert result.outcome is CampaignFireOutcome.REJECTED
    assert schedule_svc.marked == [schedule.id]
    assert audit._entries[0].action == "scheduler.campaign_rejected"


@pytest.mark.asyncio
async def test_fire_reject_consumes_once_schedule_without_more_runs():
    # Even a ONCE schedule that gets rejected stops cleanly: mark_run is
    # called (the beat loop consumed the occurrence), so it won't wedge.
    schedule_svc = _FakeScheduleService()
    svc, _ = _service(schedule=schedule_svc, scope_exc=ProjectNotFoundError(uuid4()))
    schedule = _make_campaign_schedule(frequency=ScheduleFrequency.ONCE)

    result = await svc.fire(schedule)

    assert result.outcome is CampaignFireOutcome.REJECTED
    assert schedule_svc.marked == [schedule.id]


# --- recurring schedules survive skips/rejections -----------------------------


@pytest.mark.asyncio
async def test_fire_recurring_skip_marks_for_next_slot():
    schedule_svc = _FakeScheduleService()
    autonomous = _FakeAutonomousService(active_run=AutonomousRun(
        id=uuid4(),
        project_id=uuid4(),
        initiated_by=uuid4(),
        status=AutonomousRunStatus.EXECUTING,
        objective="existing",
        max_actions=5,
        max_runtime_seconds=900,
        created_at=datetime.now(UTC),
    ))
    svc, _ = _service(schedule=schedule_svc, autonomous=autonomous)
    schedule = _make_campaign_schedule(frequency=ScheduleFrequency.HOURLY)

    result = await svc.fire(schedule)

    assert result.outcome is CampaignFireOutcome.SKIPPED_ACTIVE_RUN
    # Consumed, not wedged — the next hourly slot will be considered again.
    assert schedule_svc.marked == [schedule.id]


# --- audit completeness ------------------------------------------------------


@pytest.mark.asyncio
async def test_fire_records_audit_entries_for_each_fire():
    schedule_svc = _FakeScheduleService()
    autonomous = _FakeAutonomousService()
    audit = FakeAuditLogRepository()
    svc, _ = _service(schedule=schedule_svc, autonomous=autonomous, audit=audit)
    schedule = _make_campaign_schedule()

    await svc.fire(schedule)
    await svc.fire(schedule)

    # Two occurrences consumed -> two runs, two campaign_created entries.
    assert len(autonomous.created) == 2
    assert len(audit._entries) == 2
    assert all(e.action == "scheduler.campaign_created" for e in audit._entries)