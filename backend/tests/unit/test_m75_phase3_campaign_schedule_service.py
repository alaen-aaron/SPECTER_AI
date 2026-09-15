"""M7.5 Phase 3 — ScheduleService campaign (``ScheduleKind.CAMPAIGN``) creation.

The scheduler must reject every malformed campaign trigger at creation time
(never at fire time), and workflow schedules must remain byte-compatible
with the M7.5 Phase 1/2 behaviour (``kind`` defaults to WORKFLOW).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.application.schedule_service import ScheduleService
from app.domain.entities import CampaignScheduleConfig
from app.domain.exceptions import (
    InvalidScheduleConfigError,
    WorkflowNotFoundError,
)
from app.domain.value_objects import (
    ScheduleFrequency,
    ScheduleKind,
    WorkflowStatus,
)
from tests.fakes import FakeScheduleRepository, FakeWorkflowRepository


def _make_active_workflow():
    from app.domain.entities import Workflow

    return Workflow(
        id=uuid4(),
        project_id=uuid4(),
        name="Test WF",
        description=None,
        status=WorkflowStatus.ACTIVE,
        created_by=uuid4(),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _make_campaign(
    *,
    max_actions: int = 10,
    max_runtime_seconds: int = 3600,
) -> CampaignScheduleConfig:
    return CampaignScheduleConfig(
        objective="enumerate externally reachable services",
        max_actions=max_actions,
        max_runtime_seconds=max_runtime_seconds,
    )


def _service():
    return ScheduleService(FakeScheduleRepository(), FakeWorkflowRepository())


# --- happy path -------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_campaign_schedule_succeeds():
    service = _service()
    project_id = uuid4()
    creator = uuid4()

    sched = await service.create(
        workflow_id=None,
        project_id=project_id,
        frequency=ScheduleFrequency.ONCE,
        created_by=creator,
        kind=ScheduleKind.CAMPAIGN,
        campaign=_make_campaign(),
    )

    assert sched.kind is ScheduleKind.CAMPAIGN
    assert sched.workflow_id is None
    assert sched.campaign_config is not None
    assert sched.campaign_config.objective == "enumerate externally reachable services"
    assert sched.campaign_config.max_actions == 10
    assert sched.campaign_config.max_runtime_seconds == 3600
    assert sched.created_by == creator
    assert sched.next_run_at is not None


@pytest.mark.asyncio
async def test_campaign_config_survives_repo_roundtrip():
    repo = FakeScheduleRepository()
    service = ScheduleService(repo, FakeWorkflowRepository())
    campaign = _make_campaign(max_actions=25, max_runtime_seconds=1500)

    sched = await service.create(
        workflow_id=None,
        project_id=uuid4(),
        frequency=ScheduleFrequency.DAILY,
        created_by=uuid4(),
        kind=ScheduleKind.CAMPAIGN,
        campaign=campaign,
    )
    stored = await repo.get(sched.id)
    assert stored is not None
    assert stored.kind is ScheduleKind.CAMPAIGN
    assert stored.campaign_config == campaign
    assert stored.campaign_config.max_actions == 25


@pytest.mark.asyncio
async def test_create_campaign_with_workflow_id_rejected():
    service = _service()
    workflow = _make_active_workflow()
    await service._workflows.create(workflow)  # type: ignore[attr-defined]

    with pytest.raises(InvalidScheduleConfigError):
        await service.create(
            workflow_id=workflow.id,
            project_id=workflow.project_id,
            frequency=ScheduleFrequency.ONCE,
            created_by=uuid4(),
            kind=ScheduleKind.CAMPAIGN,
            campaign=_make_campaign(),
        )


@pytest.mark.asyncio
async def test_create_campaign_without_config_rejected():
    service = _service()

    with pytest.raises(InvalidScheduleConfigError):
        await service.create(
            workflow_id=None,
            project_id=uuid4(),
            frequency=ScheduleFrequency.ONCE,
            created_by=uuid4(),
            kind=ScheduleKind.CAMPAIGN,
            campaign=None,
        )


@pytest.mark.asyncio
async def test_create_campaign_without_creator_rejected():
    service = _service()

    with pytest.raises(InvalidScheduleConfigError):
        await service.create(
            workflow_id=None,
            project_id=uuid4(),
            frequency=ScheduleFrequency.ONCE,
            created_by=None,
            kind=ScheduleKind.CAMPAIGN,
            campaign=_make_campaign(),
        )


# --- budget bounds (mirror CreateAutonomousRunRequest) -----------------------


@pytest.mark.asyncio
async def test_create_campaign_rejects_zero_max_actions():
    service = _service()

    with pytest.raises(InvalidScheduleConfigError):
        await service.create(
            workflow_id=None,
            project_id=uuid4(),
            frequency=ScheduleFrequency.ONCE,
            created_by=uuid4(),
            kind=ScheduleKind.CAMPAIGN,
            campaign=_make_campaign(max_actions=0),
        )


@pytest.mark.asyncio
async def test_create_campaign_rejects_excessive_max_actions():
    service = _service()

    with pytest.raises(InvalidScheduleConfigError):
        await service.create(
            workflow_id=None,
            project_id=uuid4(),
            frequency=ScheduleFrequency.ONCE,
            created_by=uuid4(),
            kind=ScheduleKind.CAMPAIGN,
            campaign=_make_campaign(max_actions=51),
        )


@pytest.mark.asyncio
async def test_create_campaign_rejects_runtime_below_floor():
    service = _service()

    with pytest.raises(InvalidScheduleConfigError):
        await service.create(
            workflow_id=None,
            project_id=uuid4(),
            frequency=ScheduleFrequency.ONCE,
            created_by=uuid4(),
            kind=ScheduleKind.CAMPAIGN,
            campaign=_make_campaign(max_runtime_seconds=59),
        )


@pytest.mark.asyncio
async def test_create_campaign_rejects_runtime_above_ceiling():
    service = _service()

    with pytest.raises(InvalidScheduleConfigError):
        await service.create(
            workflow_id=None,
            project_id=uuid4(),
            frequency=ScheduleFrequency.ONCE,
            created_by=uuid4(),
            kind=ScheduleKind.CAMPAIGN,
            campaign=_make_campaign(max_runtime_seconds=7201),
        )


@pytest.mark.asyncio
async def test_create_campaign_accepts_boundary_values():
    service = _service()

    sched = await service.create(
        workflow_id=None,
        project_id=uuid4(),
        frequency=ScheduleFrequency.ONCE,
        created_by=uuid4(),
        kind=ScheduleKind.CAMPAIGN,
        campaign=_make_campaign(max_actions=50, max_runtime_seconds=7200),
    )
    assert sched.campaign_config is not None
    assert sched.campaign_config.max_actions == 50
    assert sched.campaign_config.max_runtime_seconds == 7200


# --- workflow schedules are untouched by Phase 3 ----------------------------


@pytest.mark.asyncio
async def test_workflow_kind_defaults_and_requires_existing_workflow():
    service = _service()

    with pytest.raises(WorkflowNotFoundError):
        await service.create(
            workflow_id=uuid4(),
            project_id=uuid4(),
            frequency=ScheduleFrequency.ONCE,
            created_by=uuid4(),
        )


@pytest.mark.asyncio
async def test_workflow_schedule_has_no_campaign_config():
    service = _service()
    workflow = _make_active_workflow()
    await service._workflows.create(workflow)  # type: ignore[attr-defined]

    sched = await service.create(
        workflow_id=workflow.id,
        project_id=workflow.project_id,
        frequency=ScheduleFrequency.HOURLY,
        created_by=uuid4(),
    )
    assert sched.kind is ScheduleKind.WORKFLOW
    assert sched.workflow_id == workflow.id
    assert sched.campaign_config is None


@pytest.mark.asyncio
async def test_explicit_workflow_kind_keeps_workflow_contract():
    service = _service()
    workflow = _make_active_workflow()
    await service._workflows.create(workflow)  # type: ignore[attr-defined]

    sched = await service.create(
        workflow_id=workflow.id,
        project_id=workflow.project_id,
        frequency=ScheduleFrequency.ONCE,
        created_by=uuid4(),
        kind=ScheduleKind.WORKFLOW,
    )
    assert sched.kind is ScheduleKind.WORKFLOW
    assert sched.workflow_id == workflow.id