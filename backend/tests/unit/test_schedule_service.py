"""Unit tests for ScheduleService."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.application.schedule_service import ScheduleService
from app.domain.exceptions import ScheduleNotFoundError
from app.domain.value_objects import ScheduleFrequency, WorkflowStatus
from tests.fakes import FakeScheduleRepository, FakeWorkflowRepository


def _make_active_workflow():
    from datetime import UTC, datetime

    from app.domain.entities import Workflow

    wf_id = uuid4()
    return Workflow(
        id=wf_id,
        project_id=uuid4(),
        name="Test WF",
        description=None,
        status=WorkflowStatus.ACTIVE,
        created_by=uuid4(),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_create_schedule():
    wf_repo = FakeWorkflowRepository()
    sched_repo = FakeScheduleRepository()
    service = ScheduleService(sched_repo, wf_repo)

    wf = _make_active_workflow()
    await wf_repo.create(wf)

    sched = await service.create(wf.id, wf.project_id, ScheduleFrequency.DAILY, created_by=uuid4())
    assert sched.frequency is ScheduleFrequency.DAILY
    assert sched.is_active is True
    assert sched.next_run_at is not None


@pytest.mark.asyncio
async def test_create_once_schedule():
    wf_repo = FakeWorkflowRepository()
    sched_repo = FakeScheduleRepository()
    service = ScheduleService(sched_repo, wf_repo)

    wf = _make_active_workflow()
    await wf_repo.create(wf)

    sched = await service.create(wf.id, wf.project_id, ScheduleFrequency.ONCE, created_by=uuid4())
    assert sched.frequency is ScheduleFrequency.ONCE
    assert sched.cron_expression is None


@pytest.mark.asyncio
async def test_pause_schedule():
    wf_repo = FakeWorkflowRepository()
    sched_repo = FakeScheduleRepository()
    service = ScheduleService(sched_repo, wf_repo)

    wf = _make_active_workflow()
    await wf_repo.create(wf)
    sched = await service.create(wf.id, wf.project_id, ScheduleFrequency.DAILY, created_by=uuid4())

    paused = await service.pause(sched.id)
    assert paused.is_active is False


@pytest.mark.asyncio
async def test_resume_schedule():
    wf_repo = FakeWorkflowRepository()
    sched_repo = FakeScheduleRepository()
    service = ScheduleService(sched_repo, wf_repo)

    wf = _make_active_workflow()
    await wf_repo.create(wf)
    sched = await service.create(wf.id, wf.project_id, ScheduleFrequency.DAILY, created_by=uuid4())

    await service.pause(sched.id)
    resumed = await service.resume(sched.id)
    assert resumed.is_active is True
    assert resumed.next_run_at is not None


@pytest.mark.asyncio
async def test_delete_schedule():
    wf_repo = FakeWorkflowRepository()
    sched_repo = FakeScheduleRepository()
    service = ScheduleService(sched_repo, wf_repo)

    wf = _make_active_workflow()
    await wf_repo.create(wf)
    sched = await service.create(wf.id, wf.project_id, ScheduleFrequency.DAILY, created_by=uuid4())

    await service.delete(sched.id)
    with pytest.raises(ScheduleNotFoundError):
        await service.get(sched.id)


@pytest.mark.asyncio
async def test_mark_run_once_deactivates():
    wf_repo = FakeWorkflowRepository()
    sched_repo = FakeScheduleRepository()
    service = ScheduleService(sched_repo, wf_repo)

    wf = _make_active_workflow()
    await wf_repo.create(wf)
    sched = await service.create(wf.id, wf.project_id, ScheduleFrequency.ONCE, created_by=uuid4())

    await service.mark_run(sched.id)
    updated = await service.get(sched.id)
    assert updated.is_active is False
    assert updated.next_run_at is None


@pytest.mark.asyncio
async def test_mark_run_daily_reschedules():
    wf_repo = FakeWorkflowRepository()
    sched_repo = FakeScheduleRepository()
    service = ScheduleService(sched_repo, wf_repo)

    wf = _make_active_workflow()
    await wf_repo.create(wf)
    sched = await service.create(wf.id, wf.project_id, ScheduleFrequency.DAILY, created_by=uuid4())

    await service.mark_run(sched.id)
    updated = await service.get(sched.id)
    assert updated.is_active is True
    assert updated.last_run_at is not None
    assert updated.next_run_at is not None


@pytest.mark.asyncio
async def test_schedule_not_found():
    service = ScheduleService(FakeScheduleRepository(), FakeWorkflowRepository())
    with pytest.raises(ScheduleNotFoundError):
        await service.get(uuid4())


@pytest.mark.asyncio
async def test_list_due_returns_only_due_schedules():
    from datetime import UTC, datetime, timedelta

    sched_repo = FakeScheduleRepository()
    wf_repo = FakeWorkflowRepository()
    service = ScheduleService(sched_repo, wf_repo)

    wf = _make_active_workflow()
    await wf_repo.create(wf)

    sched1 = await service.create(wf.id, wf.project_id, ScheduleFrequency.ONCE, created_by=uuid4())
    sched2 = await service.create(wf.id, wf.project_id, ScheduleFrequency.ONCE, created_by=uuid4())

    # sched1 is due (next_run_at in the past), sched2 is not yet due
    sched1.next_run_at = datetime.now(UTC) - timedelta(hours=1)
    await sched_repo.update(sched1)
    sched2.next_run_at = datetime.now(UTC) + timedelta(hours=1)
    await sched_repo.update(sched2)

    due = await sched_repo.list_due(datetime.now(UTC))
    due_ids = {s.id for s in due}
    assert sched1.id in due_ids
    assert sched2.id not in due_ids


@pytest.mark.asyncio
async def test_list_due_excludes_inactive_schedules():
    from datetime import UTC, datetime, timedelta

    sched_repo = FakeScheduleRepository()
    wf_repo = FakeWorkflowRepository()
    service = ScheduleService(sched_repo, wf_repo)

    wf = _make_active_workflow()
    await wf_repo.create(wf)

    sched = await service.create(wf.id, wf.project_id, ScheduleFrequency.ONCE, created_by=uuid4())
    sched.next_run_at = datetime.now(UTC) - timedelta(hours=1)
    await sched_repo.update(sched)

    # deactivate it
    await service.pause(sched.id)

    due = await sched_repo.list_due(datetime.now(UTC))
    assert all(s.id != sched.id for s in due)


# --- M7.5 Phase 1: expiry, real cron, and the fire-lock ----------------------


@pytest.mark.asyncio
async def test_create_rejects_invalid_cron_expression():
    from app.domain.exceptions import InvalidScheduleConfigError

    wf_repo = FakeWorkflowRepository()
    sched_repo = FakeScheduleRepository()
    service = ScheduleService(sched_repo, wf_repo)

    wf = _make_active_workflow()
    await wf_repo.create(wf)

    with pytest.raises(InvalidScheduleConfigError):
        await service.create(
            wf.id,
            wf.project_id,
            ScheduleFrequency.DAILY,
            cron_expression="not a cron",
            created_by=uuid4(),
        )

    with pytest.raises(InvalidScheduleConfigError):
        await service.create(
            wf.id,
            wf.project_id,
            ScheduleFrequency.DAILY,
            cron_expression="0 0 30 2 *",  # Feb 30 never matches
            created_by=uuid4(),
        )

    assert sched_repo._schedules == {}


@pytest.mark.asyncio
async def test_create_uses_explicit_cron_for_next_run():
    wf_repo = FakeWorkflowRepository()
    sched_repo = FakeScheduleRepository()
    service = ScheduleService(sched_repo, wf_repo)

    wf = _make_active_workflow()
    await wf_repo.create(wf)

    sched = await service.create(
        wf.id,
        wf.project_id,
        ScheduleFrequency.DAILY,
        cron_expression="0 12 * * *",
        created_by=uuid4(),
    )
    assert sched.cron_expression == "0 12 * * *"
    assert sched.next_run_at is not None
    assert sched.next_run_at.hour == 12


@pytest.mark.asyncio
async def test_create_with_expired_deadline_is_inactive():
    from datetime import UTC, datetime, timedelta

    wf_repo = FakeWorkflowRepository()
    sched_repo = FakeScheduleRepository()
    service = ScheduleService(sched_repo, wf_repo)

    wf = _make_active_workflow()
    await wf_repo.create(wf)

    sched = await service.create(
        wf.id,
        wf.project_id,
        ScheduleFrequency.ONCE,
        created_by=uuid4(),
        expires_at=datetime.now(UTC) - timedelta(hours=1),
    )
    assert sched.is_active is False
    assert sched.next_run_at is None


@pytest.mark.asyncio
async def test_create_with_deadline_before_next_run_is_inactive():
    from datetime import UTC, datetime, timedelta

    wf_repo = FakeWorkflowRepository()
    sched_repo = FakeScheduleRepository()
    service = ScheduleService(sched_repo, wf_repo)

    wf = _make_active_workflow()
    await wf_repo.create(wf)

    # Daily at midnight, but the deadline is in 2 hours -> first run can never
    # happen inside the bounded lifetime.
    sched = await service.create(
        wf.id,
        wf.project_id,
        ScheduleFrequency.DAILY,
        created_by=uuid4(),
        expires_at=datetime.now(UTC) + timedelta(hours=2),
    )
    assert sched.is_active is False
    assert sched.next_run_at is None


@pytest.mark.asyncio
async def test_create_with_deadline_after_next_run_is_active():
    from datetime import UTC, datetime, timedelta

    wf_repo = FakeWorkflowRepository()
    sched_repo = FakeScheduleRepository()
    service = ScheduleService(sched_repo, wf_repo)

    wf = _make_active_workflow()
    await wf_repo.create(wf)

    sched = await service.create(
        wf.id,
        wf.project_id,
        ScheduleFrequency.HOURLY,
        created_by=uuid4(),
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )
    assert sched.is_active is True
    assert sched.next_run_at is not None
    assert sched.next_run_at <= sched.expires_at


@pytest.mark.asyncio
async def test_resume_expired_schedule_stays_dead():
    from datetime import UTC, datetime, timedelta

    wf_repo = FakeWorkflowRepository()
    sched_repo = FakeScheduleRepository()
    service = ScheduleService(sched_repo, wf_repo)

    wf = _make_active_workflow()
    await wf_repo.create(wf)

    sched = await service.create(
        wf.id,
        wf.project_id,
        ScheduleFrequency.HOURLY,
        created_by=uuid4(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    await service.pause(sched.id)

    # Time passes past the deadline...
    sched.expires_at = datetime.now(UTC) - timedelta(hours=1)
    await sched_repo.update(sched)

    resumed = await service.resume(sched.id)
    assert resumed.is_active is False
    assert resumed.next_run_at is None


@pytest.mark.asyncio
async def test_mark_run_honours_expiry_on_reschedule():
    from datetime import UTC, datetime, timedelta

    wf_repo = FakeWorkflowRepository()
    sched_repo = FakeScheduleRepository()
    service = ScheduleService(sched_repo, wf_repo)

    wf = _make_active_workflow()
    await wf_repo.create(wf)

    sched = await service.create(
        wf.id,
        wf.project_id,
        ScheduleFrequency.DAILY,
        created_by=uuid4(),
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
    )
    # Force an immediate run: the next daily occurrence lands after the 30-min
    # deadline, so mark_run must disable the schedule.
    sched.next_run_at = datetime.now(UTC) - timedelta(minutes=1)
    await sched_repo.update(sched)

    await service.mark_run(sched.id)
    updated = await service.get(sched.id)
    assert updated.last_run_at is not None
    assert updated.is_active is False
    assert updated.next_run_at is None


@pytest.mark.asyncio
async def test_claim_due_returns_each_due_schedule_once():
    from datetime import UTC, datetime, timedelta

    sched_repo = FakeScheduleRepository()
    wf_repo = FakeWorkflowRepository()
    service = ScheduleService(sched_repo, wf_repo)

    wf = _make_active_workflow()
    await wf_repo.create(wf)

    sched1 = await service.create(wf.id, wf.project_id, ScheduleFrequency.ONCE, created_by=uuid4())
    sched2 = await service.create(wf.id, wf.project_id, ScheduleFrequency.ONCE, created_by=uuid4())
    sched1.next_run_at = datetime.now(UTC) - timedelta(hours=1)
    await sched_repo.update(sched1)
    sched2.next_run_at = datetime.now(UTC) + timedelta(hours=1)
    await sched_repo.update(sched2)

    now = datetime.now(UTC)
    claimed = await sched_repo.claim_due(now)
    assert [s.id for s in claimed] == [sched1.id]
    # A second claim in the same "transaction" sees nothing new.
    assert await sched_repo.claim_due(now) == []


@pytest.mark.asyncio
async def test_claim_due_excludes_expired_schedules():
    from datetime import UTC, datetime, timedelta

    sched_repo = FakeScheduleRepository()
    wf_repo = FakeWorkflowRepository()
    service = ScheduleService(sched_repo, wf_repo)

    wf = _make_active_workflow()
    await wf_repo.create(wf)

    live = await service.create(wf.id, wf.project_id, ScheduleFrequency.ONCE, created_by=uuid4())
    dead = await service.create(
        wf.id,
        wf.project_id,
        ScheduleFrequency.ONCE,
        created_by=uuid4(),
        expires_at=datetime.now(UTC) - timedelta(hours=2),
    )
    live.next_run_at = datetime.now(UTC) - timedelta(hours=1)
    await sched_repo.update(live)
    dead.next_run_at = datetime.now(UTC) - timedelta(hours=1)
    await sched_repo.update(dead)

    now = datetime.now(UTC)
    claimed = await sched_repo.claim_due(now)
    assert [s.id for s in claimed] == [live.id]
