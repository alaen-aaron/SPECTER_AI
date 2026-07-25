"""Unit tests for PlannerService (Phase 4, SRS §8)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.application.planner_service import PlannerService
from app.domain.entities import Asset, Finding, PlannedAction
from app.domain.exceptions import (
    PlannedActionExpiredError,
    PlannedActionNotApprovableError,
    PlannedActionNotFoundError,
)
from app.domain.value_objects import (
    AssetType,
    FindingStatus,
    PlannedActionStatus,
    Severity,
)
from tests.fakes import (
    FakeAIContextMemoryRepository,
    FakeAssetRepository,
    FakeFindingRepository,
    FakePlannedActionRepository,
)


def _make_finding(
    project_id: UUID,
    title: str = "Test Finding",
    severity: Severity = Severity.MEDIUM,
) -> Finding:
    return Finding(
        id=uuid4(),
        project_id=project_id,
        title=title,
        severity=severity,
        status=FindingStatus.OPEN,
        dedup_key=f"dedup:{title}",
    )


@pytest.mark.asyncio
async def test_suggest_returns_actions_when_empty():
    project_id = uuid4()
    svc = PlannerService(
        planned_action_repo=FakePlannedActionRepository(),
        finding_repo=FakeFindingRepository(),
        asset_repo=FakeAssetRepository(),
        context_memory_repo=FakeAIContextMemoryRepository(),
    )
    actions = await svc.suggest(project_id)
    assert len(actions) > 0
    assert all(a.status is PlannedActionStatus.PENDING_REVIEW for a in actions)


@pytest.mark.asyncio
async def test_suggest_with_findings_returns_investigate():
    project_id = uuid4()
    finding_repo = FakeFindingRepository()
    asset_repo = FakeAssetRepository()
    finding = _make_finding(
        project_id, "SQL Injection", Severity.CRITICAL
    )
    await finding_repo.add(finding)
    asset = Asset(
        id=uuid4(),
        project_id=project_id,
        value="192.168.1.1",
        asset_type=AssetType.HOST,
        first_seen=datetime.now(UTC),
        last_seen=datetime.now(UTC),
    )
    await asset_repo.add(asset)

    svc = PlannerService(
        planned_action_repo=FakePlannedActionRepository(),
        finding_repo=finding_repo,
        asset_repo=asset_repo,
        context_memory_repo=FakeAIContextMemoryRepository(),
    )
    actions = await svc.suggest(project_id)
    assert any("investigate" in a.action_type for a in actions)


@pytest.mark.asyncio
async def test_approve_action():
    project_id = uuid4()
    action_repo = FakePlannedActionRepository()
    action = PlannedAction(
        id=uuid4(),
        project_id=project_id,
        action_type="scan",
        title="Run scan",
        description="desc",
        justification="because",
        status=PlannedActionStatus.PENDING_REVIEW,
    )
    await action_repo.create(action)

    svc = PlannerService(
        planned_action_repo=action_repo,
        finding_repo=FakeFindingRepository(),
        asset_repo=FakeAssetRepository(),
        context_memory_repo=FakeAIContextMemoryRepository(),
    )
    approved = await svc.approve(action.id, approved_by=uuid4())
    assert approved.status is PlannedActionStatus.APPROVED
    assert approved.approved_by is not None
    assert approved.approved_at is not None


@pytest.mark.asyncio
async def test_approve_nonexistent_raises():
    svc = PlannerService(
        planned_action_repo=FakePlannedActionRepository(),
        finding_repo=FakeFindingRepository(),
        asset_repo=FakeAssetRepository(),
        context_memory_repo=FakeAIContextMemoryRepository(),
    )
    with pytest.raises(PlannedActionNotFoundError):
        await svc.approve(uuid4(), approved_by=uuid4())


@pytest.mark.asyncio
async def test_approve_already_approved_raises():
    project_id = uuid4()
    action_repo = FakePlannedActionRepository()
    action = PlannedAction(
        id=uuid4(),
        project_id=project_id,
        action_type="scan",
        title="Run scan",
        description="desc",
        justification="because",
        status=PlannedActionStatus.APPROVED,
    )
    await action_repo.create(action)

    svc = PlannerService(
        planned_action_repo=action_repo,
        finding_repo=FakeFindingRepository(),
        asset_repo=FakeAssetRepository(),
        context_memory_repo=FakeAIContextMemoryRepository(),
    )
    with pytest.raises(PlannedActionNotApprovableError):
        await svc.approve(action.id, approved_by=uuid4())


@pytest.mark.asyncio
async def test_reject_action():
    project_id = uuid4()
    action_repo = FakePlannedActionRepository()
    action = PlannedAction(
        id=uuid4(),
        project_id=project_id,
        action_type="scan",
        title="Run scan",
        description="desc",
        justification="because",
        status=PlannedActionStatus.PENDING_REVIEW,
    )
    await action_repo.create(action)

    svc = PlannerService(
        planned_action_repo=action_repo,
        finding_repo=FakeFindingRepository(),
        asset_repo=FakeAssetRepository(),
        context_memory_repo=FakeAIContextMemoryRepository(),
    )
    rejected = await svc.reject(
        action.id, rejected_by=uuid4(), reason="Not needed"
    )
    assert rejected.status is PlannedActionStatus.REJECTED
    assert rejected.rejection_reason == "Not needed"


@pytest.mark.asyncio
async def test_approve_expired_action_raises():
    project_id = uuid4()
    action_repo = FakePlannedActionRepository()
    action = PlannedAction(
        id=uuid4(),
        project_id=project_id,
        action_type="scan",
        title="Run scan",
        description="desc",
        justification="because",
        status=PlannedActionStatus.PENDING_REVIEW,
        expires_at=datetime.now(UTC) - timedelta(hours=1),
    )
    await action_repo.create(action)

    svc = PlannerService(
        planned_action_repo=action_repo,
        finding_repo=FakeFindingRepository(),
        asset_repo=FakeAssetRepository(),
        context_memory_repo=FakeAIContextMemoryRepository(),
    )
    with pytest.raises(PlannedActionExpiredError):
        await svc.approve(action.id, approved_by=uuid4())


@pytest.mark.asyncio
async def test_list_for_project():
    project_id = uuid4()
    action_repo = FakePlannedActionRepository()
    for _ in range(3):
        action = PlannedAction(
            id=uuid4(),
            project_id=project_id,
            action_type="scan",
            title="Run scan",
            description="desc",
            justification="because",
        )
        await action_repo.create(action)

    svc = PlannerService(
        planned_action_repo=action_repo,
        finding_repo=FakeFindingRepository(),
        asset_repo=FakeAssetRepository(),
        context_memory_repo=FakeAIContextMemoryRepository(),
    )
    actions = await svc.list_for_project(project_id)
    assert len(actions) == 3


@pytest.mark.asyncio
async def test_get_action():
    project_id = uuid4()
    action_repo = FakePlannedActionRepository()
    action = PlannedAction(
        id=uuid4(),
        project_id=project_id,
        action_type="scan",
        title="Run scan",
        description="desc",
        justification="because",
    )
    await action_repo.create(action)

    svc = PlannerService(
        planned_action_repo=action_repo,
        finding_repo=FakeFindingRepository(),
        asset_repo=FakeAssetRepository(),
        context_memory_repo=FakeAIContextMemoryRepository(),
    )
    result = await svc.get(action.id)
    assert result.id == action.id
