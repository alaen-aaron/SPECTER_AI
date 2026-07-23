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

    sched = await service.create(
        wf.id, wf.project_id, ScheduleFrequency.DAILY, created_by=uuid4()
    )
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

    sched = await service.create(
        wf.id, wf.project_id, ScheduleFrequency.ONCE, created_by=uuid4()
    )
    assert sched.frequency is ScheduleFrequency.ONCE
    assert sched.cron_expression is None


@pytest.mark.asyncio
async def test_pause_schedule():
    wf_repo = FakeWorkflowRepository()
    sched_repo = FakeScheduleRepository()
    service = ScheduleService(sched_repo, wf_repo)

    wf = _make_active_workflow()
    await wf_repo.create(wf)
    sched = await service.create(
        wf.id, wf.project_id, ScheduleFrequency.DAILY, created_by=uuid4()
    )

    paused = await service.pause(sched.id)
    assert paused.is_active is False


@pytest.mark.asyncio
async def test_resume_schedule():
    wf_repo = FakeWorkflowRepository()
    sched_repo = FakeScheduleRepository()
    service = ScheduleService(sched_repo, wf_repo)

    wf = _make_active_workflow()
    await wf_repo.create(wf)
    sched = await service.create(
        wf.id, wf.project_id, ScheduleFrequency.DAILY, created_by=uuid4()
    )

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
    sched = await service.create(
        wf.id, wf.project_id, ScheduleFrequency.DAILY, created_by=uuid4()
    )

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
    sched = await service.create(
        wf.id, wf.project_id, ScheduleFrequency.ONCE, created_by=uuid4()
    )

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
    sched = await service.create(
        wf.id, wf.project_id, ScheduleFrequency.DAILY, created_by=uuid4()
    )

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
