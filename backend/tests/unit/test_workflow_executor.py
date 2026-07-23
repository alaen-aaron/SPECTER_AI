"""Unit tests for WorkflowExecutor."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.application.workflow_executor import WorkflowExecutor, _evaluate_condition
from app.domain.entities import WorkflowExecution, WorkflowStep
from app.domain.value_objects import ScanStatus
from tests.fakes import (
    FakeFindingRepository,
    FakeScanRepository,
    FakeToolResultRepository,
    FakeWorkflowExecutionRepository,
    FakeWorkflowStepRepository,
)


class FakePluginManager:
    """Minimal fake that returns success for any plugin call."""

    def run(self, plugin_name: str, config: dict, timeout: int):
        from app.plugins.base import PluginResult
        return PluginResult(
            success=True,
            stdout=f"OK from {plugin_name}",
            stderr="",
            exit_code=0,
            metadata={"plugin": plugin_name},
        )


class FailingPluginManager:
    """Fake that always fails."""

    def run(self, plugin_name: str, config: dict, timeout: int):
        from app.plugins.base import PluginResult
        return PluginResult(
            success=False,
            stdout="",
            stderr="Plugin failed",
            exit_code=1,
        )


class FakeNormalizerRegistry:
    def get(self, name: str):
        return None


# --- Condition evaluation tests ---


def test_condition_exists_true():
    results = {"step-1": {"status": "completed"}}
    assert _evaluate_condition(
        {"step_id": "step-1", "operator": "exists", "field": "status"},
        results,
    )


def test_condition_exists_false():
    assert not _evaluate_condition(
        {"step_id": "step-1", "operator": "exists", "field": "status"},
        {},
    )


def test_condition_equals():
    results = {"step-1": {"status": "completed"}}
    assert _evaluate_condition(
        {"step_id": "step-1", "operator": "equals", "field": "status", "value": "completed"},
        results,
    )


def test_condition_not_equals():
    results = {"step-1": {"status": "completed"}}
    assert _evaluate_condition(
        {"step_id": "step-1", "operator": "not_equals", "field": "status", "value": "failed"},
        results,
    )


def test_condition_greater_than():
    results = {"step-1": {"exit_code": 5}}
    assert _evaluate_condition(
        {"step_id": "step-1", "operator": "greater_than", "field": "exit_code", "value": 0},
        results,
    )


def test_condition_less_than():
    results = {"step-1": {"exit_code": 0}}
    assert _evaluate_condition(
        {"step_id": "step-1", "operator": "less_than", "field": "exit_code", "value": 1},
        results,
    )


# --- Executor tests ---


@pytest.mark.asyncio
async def test_execute_linear_workflow():
    exec_repo = FakeWorkflowExecutionRepository()
    step_repo = FakeWorkflowStepRepository()
    scan_repo = FakeScanRepository()
    tool_results_repo = FakeToolResultRepository()
    finding_repo = FakeFindingRepository()

    wf_id = uuid4()
    proj_id = uuid4()
    s1 = WorkflowStep(
        id=uuid4(), workflow_id=wf_id, step_type="scan", plugin="echo",
        name="Step 1", plugin_config={}, depends_on=[], order=0,
    )
    s2 = WorkflowStep(
        id=uuid4(), workflow_id=wf_id, step_type="scan", plugin="echo",
        name="Step 2", plugin_config={}, depends_on=[s1.id], order=1,
    )
    await step_repo.add(s1)
    await step_repo.add(s2)

    from app.application.correlation_service import CorrelationService
    executor = WorkflowExecutor(
        plugin_manager=FakePluginManager(),  # type: ignore[arg-type]
        normalizer_registry=FakeNormalizerRegistry(),  # type: ignore[arg-type]
        execution_repository=exec_repo,
        step_repository=step_repo,
        scan_repository=scan_repo,  # type: ignore[arg-type]
        tool_result_repository=tool_results_repo,
        correlation_service=CorrelationService(finding_repo),
    )

    execution = WorkflowExecution(
        id=uuid4(),
        workflow_id=wf_id,
        project_id=proj_id,
        initiated_by=uuid4(),
        status=ScanStatus.QUEUED,
    )
    await exec_repo.create(execution)

    await executor.execute(execution.id)

    updated = await exec_repo.get(execution.id)
    assert updated is not None
    assert updated.status is ScanStatus.COMPLETED


@pytest.mark.asyncio
async def test_execute_workflow_condition_skip():
    exec_repo = FakeWorkflowExecutionRepository()
    step_repo = FakeWorkflowStepRepository()
    scan_repo = FakeScanRepository()
    tool_results_repo = FakeToolResultRepository()
    finding_repo = FakeFindingRepository()

    wf_id = uuid4()
    s1 = WorkflowStep(
        id=uuid4(), workflow_id=wf_id, step_type="scan", plugin="echo",
        name="Step 1", plugin_config={}, depends_on=[], order=0,
    )
    s2 = WorkflowStep(
        id=uuid4(), workflow_id=wf_id, step_type="scan", plugin="echo",
        name="Step 2", plugin_config={}, depends_on=[s1.id], order=1,
        condition={
            "step_id": str(s1.id),
            "operator": "equals",
            "field": "status",
            "value": "failed",
        },
    )
    await step_repo.add(s1)
    await step_repo.add(s2)

    from app.application.correlation_service import CorrelationService
    executor = WorkflowExecutor(
        plugin_manager=FakePluginManager(),  # type: ignore[arg-type]
        normalizer_registry=FakeNormalizerRegistry(),  # type: ignore[arg-type]
        execution_repository=exec_repo,
        step_repository=step_repo,
        scan_repository=scan_repo,  # type: ignore[arg-type]
        tool_result_repository=tool_results_repo,
        correlation_service=CorrelationService(finding_repo),
    )

    execution = WorkflowExecution(
        id=uuid4(), workflow_id=wf_id, project_id=uuid4(),
        initiated_by=uuid4(), status=ScanStatus.QUEUED,
    )
    await exec_repo.create(execution)
    await executor.execute(execution.id)

    updated = await exec_repo.get(execution.id)
    assert updated is not None
    assert updated.status is ScanStatus.COMPLETED
    assert updated.step_results[str(s2.id)]["status"] == "skipped"


@pytest.mark.asyncio
async def test_execute_workflow_plugin_failure():
    exec_repo = FakeWorkflowExecutionRepository()
    step_repo = FakeWorkflowStepRepository()
    scan_repo = FakeScanRepository()
    tool_results_repo = FakeToolResultRepository()
    finding_repo = FakeFindingRepository()

    wf_id = uuid4()
    s1 = WorkflowStep(
        id=uuid4(), workflow_id=wf_id, step_type="scan", plugin="nmap",
        name="Step 1", plugin_config={}, depends_on=[], order=0,
    )
    await step_repo.add(s1)

    from app.application.correlation_service import CorrelationService
    executor = WorkflowExecutor(
        plugin_manager=FailingPluginManager(),  # type: ignore[arg-type]
        normalizer_registry=FakeNormalizerRegistry(),  # type: ignore[arg-type]
        execution_repository=exec_repo,
        step_repository=step_repo,
        scan_repository=scan_repo,  # type: ignore[arg-type]
        tool_result_repository=tool_results_repo,
        correlation_service=CorrelationService(finding_repo),
    )

    execution = WorkflowExecution(
        id=uuid4(), workflow_id=wf_id, project_id=uuid4(),
        initiated_by=uuid4(), status=ScanStatus.QUEUED,
    )
    await exec_repo.create(execution)
    await executor.execute(execution.id)

    updated = await exec_repo.get(execution.id)
    assert updated is not None
    assert updated.status is ScanStatus.FAILED


@pytest.mark.asyncio
async def test_tool_result_references_real_scan():
    """ToolResult.scan_id must reference a Scan created by the executor."""
    exec_repo = FakeWorkflowExecutionRepository()
    step_repo = FakeWorkflowStepRepository()
    scan_repo = FakeScanRepository()
    tool_results_repo = FakeToolResultRepository()
    finding_repo = FakeFindingRepository()

    wf_id = uuid4()
    proj_id = uuid4()
    s1 = WorkflowStep(
        id=uuid4(), workflow_id=wf_id, step_type="scan", plugin="echo",
        name="Step 1", plugin_config={"target": "10.0.0.1"},
        depends_on=[], order=0,
    )
    await step_repo.add(s1)

    from app.application.correlation_service import CorrelationService
    executor = WorkflowExecutor(
        plugin_manager=FakePluginManager(),  # type: ignore[arg-type]
        normalizer_registry=FakeNormalizerRegistry(),  # type: ignore[arg-type]
        execution_repository=exec_repo,
        step_repository=step_repo,
        scan_repository=scan_repo,  # type: ignore[arg-type]
        tool_result_repository=tool_results_repo,
        correlation_service=CorrelationService(finding_repo),
    )

    execution = WorkflowExecution(
        id=uuid4(), workflow_id=wf_id, project_id=proj_id,
        initiated_by=uuid4(), status=ScanStatus.QUEUED,
    )
    await exec_repo.create(execution)
    await executor.execute(execution.id)

    # A ToolResult was created
    assert len(tool_results_repo._results) == 1
    tr = list(tool_results_repo._results.values())[0]

    # Its scan_id must reference a Scan that exists in the repository
    scan = await scan_repo.get(tr.scan_id)
    assert scan is not None
    assert scan.plugin == "echo"
    assert scan.project_id == proj_id
    assert scan.status in (ScanStatus.COMPLETED, ScanStatus.FAILED)


@pytest.mark.asyncio
async def test_step_scan_created_with_correct_fields():
    """Each step execution creates a Scan with proper project/plugin/initiator."""
    exec_repo = FakeWorkflowExecutionRepository()
    step_repo = FakeWorkflowStepRepository()
    scan_repo = FakeScanRepository()
    tool_results_repo = FakeToolResultRepository()
    finding_repo = FakeFindingRepository()

    wf_id = uuid4()
    proj_id = uuid4()
    actor_id = uuid4()
    s1 = WorkflowStep(
        id=uuid4(), workflow_id=wf_id, step_type="scan", plugin="nmap",
        name="Step 1", plugin_config={"target": "10.0.0.1"},
        depends_on=[], order=0,
    )
    await step_repo.add(s1)

    from app.application.correlation_service import CorrelationService
    executor = WorkflowExecutor(
        plugin_manager=FakePluginManager(),  # type: ignore[arg-type]
        normalizer_registry=FakeNormalizerRegistry(),  # type: ignore[arg-type]
        execution_repository=exec_repo,
        step_repository=step_repo,
        scan_repository=scan_repo,  # type: ignore[arg-type]
        tool_result_repository=tool_results_repo,
        correlation_service=CorrelationService(finding_repo),
    )

    execution = WorkflowExecution(
        id=uuid4(), workflow_id=wf_id, project_id=proj_id,
        initiated_by=actor_id, status=ScanStatus.QUEUED,
    )
    await exec_repo.create(execution)
    await executor.execute(execution.id)

    # Exactly one Scan created
    assert len(scan_repo._scans) == 1
    scan = list(scan_repo._scans.values())[0]
    assert scan.project_id == proj_id
    assert scan.initiated_by == actor_id
    assert scan.plugin == "nmap"
    assert scan.status is ScanStatus.COMPLETED
