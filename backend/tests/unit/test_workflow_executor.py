"""Unit tests for the M7.5 hardened WorkflowExecutor.

Every step goes through the canonical ScanService.create -> ExecutionEngine
path, so these tests are built around: a real ScopeGuardService backed by
in-memory fakes, a real ScanService with a real PluginManager (echo), and a
FakeExecutionEngine that completes scans (with failure injection for retries).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest

from app.application.scan_service import NullScanTaskDispatcher, ScanService
from app.application.scope_guard_service import ScopeGuardService
from app.application.workflow_executor import WorkflowExecutor, _evaluate_condition
from app.domain.entities import (
    AuthorizationRecord,
    Project,
    Target,
    WorkflowExecution,
    WorkflowStep,
)
from app.domain.value_objects import (
    AuthorizationStatus,
    ProjectState,
    ScanStatus,
    TargetType,
)
from app.plugins.echo_plugin import EchoPlugin
from app.plugins.manager import PluginManager
from app.plugins.registry import PluginRegistry
from tests.fakes import (
    FakeAuditLogRepository,
    FakeAuthorizationRecordRepository,
    FakeExecutionEngine,
    FakeProjectRepository,
    FakeScanRepository,
    FakeTargetRepository,
    FakeWorkflowExecutionRepository,
    FakeWorkflowStepRepository,
)


class _Harness:
    """A fully-wired executor with real ScopeGuard + ScanService + fakes."""

    def __init__(self, project: Project) -> None:
        self.projects = FakeProjectRepository()
        self.targets = FakeTargetRepository()
        self.authorizations = FakeAuthorizationRecordRepository()
        self.scans = FakeScanRepository()
        self.executions = FakeWorkflowExecutionRepository()
        self.steps = FakeWorkflowStepRepository()
        self.audit = FakeAuditLogRepository()
        self.project = project

        self.scope_guard = ScopeGuardService(
            project_repository=self.projects,
            target_repository=self.targets,
            authorization_repository=self.authorizations,
        )
        self.engine = FakeExecutionEngine(self.scans)

        registry = PluginRegistry()
        registry.register(EchoPlugin())
        self.scan_service = ScanService(
            scan_repository=self.scans,
            scope_guard=self.scope_guard,
            plugin_manager=PluginManager(registry),
            task_dispatcher=NullScanTaskDispatcher(),
        )
        self.executor = WorkflowExecutor(
            scan_service=self.scan_service,
            execution_engine=self.engine,  # type: ignore[arg-type]
            target_repository=self.targets,
            execution_repository=self.executions,
            step_repository=self.steps,
            audit_log_repository=self.audit,
        )

    async def seed(self) -> None:
        await self.projects.add(self.project)
        await self.authorizations.add(
            _make_authorization_record(self.project.id, ["10.0.0.1", "10.0.0.2"])
        )

    async def add_in_scope_target(self, value: str) -> Target:
        target = _make_target(self.project.id, value)
        await self.targets.add(target)
        return target

    def add_step(
        self,
        workflow_id,
        *,
        plugin_config,
        depends_on=(),
        max_retries=0,
        condition=None,
    ) -> WorkflowStep:
        step = WorkflowStep(
            id=uuid4(),
            workflow_id=workflow_id,
            step_type="scan",
            plugin="echo",
            name=f"Step {len(depends_on)}",
            plugin_config=plugin_config,
            depends_on=list(depends_on),
            condition=condition,
            max_retries=max_retries,
            order=0,
        )
        return step


def _make_project(state: ProjectState = ProjectState.ACTIVE) -> Project:
    now = datetime.now(UTC)
    return Project(
        id=uuid4(),
        organization_id=uuid4(),
        name="Test Project",
        description=None,
        state=state,
        tags=[],
        client_metadata={},
        created_at=now,
        updated_at=now,
    )


def _make_target(project_id: object, value: str = "10.0.0.5") -> Target:
    now = datetime.now(UTC)
    return Target(
        id=uuid4(),
        project_id=project_id,  # type: ignore[arg-type]
        value=value,
        target_type=TargetType.IP,
        in_scope=True,
        created_at=now,
        updated_at=now,
    )


def _make_authorization_record(
    project_id: object, allowed_targets: list[str]
) -> AuthorizationRecord:
    today = date.today()
    return AuthorizationRecord(
        id=uuid4(),
        project_id=project_id,  # type: ignore[arg-type]
        client_name="Acme",
        document_reference="doc.pdf",
        authorized_from=today - timedelta(days=1),
        authorized_to=today + timedelta(days=30),
        allowed_targets=allowed_targets,
        approved_by=uuid4(),
        status=AuthorizationStatus.ACTIVE,
        scope_notes=None,
        evidence_pointer=None,
        created_at=datetime.now(UTC),
    )


async def _add_execution(harness: _Harness, workflow_id, initiated_by=None) -> WorkflowExecution:
    execution = WorkflowExecution(
        id=uuid4(),
        workflow_id=workflow_id,
        project_id=harness.project.id,
        initiated_by=initiated_by or uuid4(),
        status=ScanStatus.QUEUED,
    )
    await harness.executions.create(execution)
    return execution


# --- Condition evaluation (unchanged behaviour) -----------------------------


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


# --- Canonical-path execution -----------------------------------------------


@pytest.mark.asyncio
async def test_execute_linear_workflow_through_canonical_path():
    harness = _Harness(_make_project())
    await harness.seed()
    t1 = await harness.add_in_scope_target("10.0.0.1")
    t2 = await harness.add_in_scope_target("10.0.0.2")

    wf_id = uuid4()
    s1 = harness.add_step(wf_id, plugin_config={"target": t1.value})
    await harness.steps.add(s1)
    s2 = harness.add_step(wf_id, plugin_config={"target": t2.value}, depends_on=[s1.id])
    await harness.steps.add(s2)

    execution = await _add_execution(harness, wf_id)
    await harness.executor.execute(execution.id)

    updated = await harness.executions.get(execution.id)
    assert updated is not None
    assert updated.status is ScanStatus.COMPLETED
    # Both steps ran through ScanService.create + the engine: each left a real Scan.
    assert len(harness.scans._scans) == 2
    assert all(scan.status is ScanStatus.COMPLETED for scan in harness.scans._scans.values())
    # Step results reference real scan rows.
    assert updated.step_results[str(s1.id)]["success"] is True
    assert updated.step_results[str(s2.id)]["success"] is True
    first_scan = list(harness.scans._scans.values())[0]
    assert first_scan.project_id == harness.project.id
    assert first_scan.initiated_by == execution.initiated_by


@pytest.mark.asyncio
async def test_execute_workflow_condition_skip():
    harness = _Harness(_make_project())
    await harness.seed()
    t1 = await harness.add_in_scope_target("10.0.0.1")

    wf_id = uuid4()
    s1 = harness.add_step(wf_id, plugin_config={"target": t1.value})
    await harness.steps.add(s1)
    s2 = harness.add_step(
        wf_id,
        plugin_config={"target": t1.value},
        depends_on=[s1.id],
        condition={
            "step_id": str(s1.id),
            "operator": "equals",
            "field": "status",
            "value": "failed",
        },
    )
    await harness.steps.add(s2)

    execution = await _add_execution(harness, wf_id)
    await harness.executor.execute(execution.id)

    updated = await harness.executions.get(execution.id)
    assert updated is not None
    assert updated.status is ScanStatus.COMPLETED
    assert updated.step_results[str(s2.id)]["status"] == "skipped"
    # Only step 1 created a Scan.
    assert len(harness.scans._scans) == 1


@pytest.mark.asyncio
async def test_step_retry_then_success():
    harness = _Harness(_make_project())
    await harness.seed()
    t1 = await harness.add_in_scope_target("10.0.0.1")

    wf_id = uuid4()
    s1 = harness.add_step(wf_id, plugin_config={"target": t1.value}, max_retries=1)
    await harness.steps.add(s1)

    execution = await _add_execution(harness, wf_id)
    # First engine attempt fails, second succeeds — retry budget of 1 absorbs it.
    harness.engine.failures.insert(0, RuntimeError("boom"))

    await harness.executor.execute(execution.id)

    updated = await harness.executions.get(execution.id)
    assert updated is not None
    assert updated.status is ScanStatus.COMPLETED
    assert updated.step_results[str(s1.id)]["success"] is True
    # Two Scan rows created: attempt 0 failed, attempt 1 completed.
    scans = sorted(
        (s for s in harness.scans._scans.values()),
        key=lambda s: s.created_at,
    )
    assert len(scans) == 2
    assert scans[0].status is ScanStatus.FAILED
    assert scans[1].status is ScanStatus.COMPLETED


@pytest.mark.asyncio
async def test_step_failure_exhausts_retries_fails_workflow():
    harness = _Harness(_make_project())
    await harness.seed()
    t1 = await harness.add_in_scope_target("10.0.0.1")

    wf_id = uuid4()
    s1 = harness.add_step(wf_id, plugin_config={"target": t1.value}, max_retries=1)
    await harness.steps.add(s1)

    execution = await _add_execution(harness, wf_id)
    harness.engine.failures.extend([RuntimeError("boom"), RuntimeError("boom again")])

    await harness.executor.execute(execution.id)

    updated = await harness.executions.get(execution.id)
    assert updated is not None
    assert updated.status is ScanStatus.FAILED
    assert updated.step_results[str(s1.id)]["success"] is False


@pytest.mark.asyncio
async def test_step_referencing_no_target_fails_closed():
    harness = _Harness(_make_project())
    await harness.seed()

    wf_id = uuid4()
    s1 = harness.add_step(wf_id, plugin_config={})
    await harness.steps.add(s1)

    execution = await _add_execution(harness, wf_id)
    await harness.executor.execute(execution.id)

    updated = await harness.executions.get(execution.id)
    assert updated is not None
    assert updated.status is ScanStatus.FAILED
    assert updated.step_results[str(s1.id)]["success"] is False
    # Fail-closed: NO scan row was ever created.
    assert len(harness.scans._scans) == 0


@pytest.mark.asyncio
async def test_step_referencing_unregistered_target_fails_closed():
    harness = _Harness(_make_project())
    await harness.seed()

    wf_id = uuid4()
    s1 = harness.add_step(wf_id, plugin_config={"target": "9.9.9.9"})
    await harness.steps.add(s1)

    execution = await _add_execution(harness, wf_id)
    await harness.executor.execute(execution.id)

    updated = await harness.executions.get(execution.id)
    assert updated is not None
    assert updated.status is ScanStatus.FAILED
    assert "not a registered Target" in updated.step_results[str(s1.id)]["error"]
    assert len(harness.scans._scans) == 0


@pytest.mark.asyncio
async def test_scope_guard_revalidated_at_execution_time():
    """Registered but out-of-scope Target -> canonical Scope Guard rejects the
    step at execution time (not at schedule/creation time)."""
    harness = _Harness(_make_project())
    await harness.seed()
    # Registered as a project Target, but NOT covered by the auth record.
    rogue = await harness.add_in_scope_target("10.0.0.99")

    wf_id = uuid4()
    s1 = harness.add_step(wf_id, plugin_config={"target": rogue.value})
    await harness.steps.add(s1)

    execution = await _add_execution(harness, wf_id)
    await harness.executor.execute(execution.id)

    updated = await harness.executions.get(execution.id)
    assert updated is not None
    assert updated.status is ScanStatus.FAILED
    assert updated.step_results[str(s1.id)]["success"] is False
    # Scope Guard rejects before persistence -> no scan row.
    assert len(harness.scans._scans) == 0
