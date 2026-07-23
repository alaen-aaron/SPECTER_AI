"""Unit tests for Workflow domain: DAG validation and WorkflowService."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.application.workflow_service import (
    NullWorkflowTaskDispatcher,
    WorkflowService,
    validate_dag,
)
from app.domain.entities import WorkflowStep
from app.domain.exceptions import (
    WorkflowEmptyError,
    WorkflowHasCyclesError,
    WorkflowNotExecutableError,
    WorkflowNotFoundError,
    WorkflowStepDependencyError,
)
from app.domain.value_objects import WorkflowStatus
from tests.fakes import (
    FakeWorkflowExecutionRepository,
    FakeWorkflowRepository,
    FakeWorkflowStepRepository,
)


def _make_step(
    workflow_id: UUID,
    *,
    order: int = 0,
    depends_on: list[UUID] | None = None,
    plugin: str = "echo",
    name: str | None = None,
) -> WorkflowStep:
    return WorkflowStep(
        id=uuid4(),
        workflow_id=workflow_id,
        step_type="scan",
        plugin=plugin,
        name=name or f"step-{order}",
        plugin_config={},
        depends_on=depends_on or [],
        order=order,
    )


# --- DAG validation tests ---


def test_empty_dag_returns_empty_order():
    assert validate_dag([]) == []


def test_single_step_dag():
    wid = uuid4()
    step = _make_step(wid, order=0)
    order = validate_dag([step])
    assert order == [step.id]


def test_linear_chain():
    wid = uuid4()
    s1 = _make_step(wid, order=0)
    s2 = _make_step(wid, order=1, depends_on=[s1.id])
    s3 = _make_step(wid, order=2, depends_on=[s2.id])

    order = validate_dag([s1, s2, s3])
    assert order.index(s1.id) < order.index(s2.id)
    assert order.index(s2.id) < order.index(s3.id)


def test_diamond_dag():
    wid = uuid4()
    s1 = _make_step(wid, order=0)
    s2 = _make_step(wid, order=1, depends_on=[s1.id])
    s3 = _make_step(wid, order=2, depends_on=[s1.id])
    s4 = _make_step(wid, order=3, depends_on=[s2.id, s3.id])

    order = validate_dag([s1, s2, s3, s4])
    assert order.index(s1.id) < order.index(s2.id)
    assert order.index(s1.id) < order.index(s3.id)
    assert order.index(s2.id) < order.index(s4.id)
    assert order.index(s3.id) < order.index(s4.id)


def test_parallel_steps_no_deps():
    wid = uuid4()
    s1 = _make_step(wid, order=0)
    s2 = _make_step(wid, order=1)
    s3 = _make_step(wid, order=2)

    order = validate_dag([s1, s2, s3])
    assert len(order) == 3


def test_cycle_detection_simple():
    wid = uuid4()
    s1 = _make_step(wid, depends_on=[uuid4()])
    s1.depends_on = [s1.id]

    with pytest.raises(WorkflowHasCyclesError):
        validate_dag([s1])


def test_cycle_detection_indirect():
    wid = uuid4()
    s1 = _make_step(wid, order=0)
    s2 = _make_step(wid, order=1, depends_on=[s1.id])
    s3 = _make_step(wid, order=2, depends_on=[s2.id])
    # Make s1 depend on s3 -> cycle
    s1.depends_on = [s3.id]

    with pytest.raises(WorkflowHasCyclesError):
        validate_dag([s1, s2, s3])


def test_missing_dependency_raises():
    wid = uuid4()
    missing_id = uuid4()
    s1 = _make_step(wid, depends_on=[missing_id])

    with pytest.raises(WorkflowStepDependencyError) as exc_info:
        validate_dag([s1])
    assert exc_info.value.missing_dep == missing_id


# --- WorkflowService CRUD tests ---


@pytest.mark.asyncio
async def test_create_workflow():
    repo = FakeWorkflowRepository()
    step_repo = FakeWorkflowStepRepository()
    exec_repo = FakeWorkflowExecutionRepository()
    service = WorkflowService(repo, step_repo, exec_repo, NullWorkflowTaskDispatcher())

    wf = await service.create(uuid4(), "Test WF", "desc", uuid4())
    assert wf.name == "Test WF"
    assert wf.status is WorkflowStatus.DRAFT


@pytest.mark.asyncio
async def test_get_workflow_not_found():
    service = WorkflowService(
        FakeWorkflowRepository(),
        FakeWorkflowStepRepository(),
        FakeWorkflowExecutionRepository(),
        NullWorkflowTaskDispatcher(),
    )
    with pytest.raises(WorkflowNotFoundError):
        await service.get(uuid4())


@pytest.mark.asyncio
async def test_activate_workflow_validates_dag():
    repo = FakeWorkflowRepository()
    step_repo = FakeWorkflowStepRepository()
    exec_repo = FakeWorkflowExecutionRepository()
    service = WorkflowService(repo, step_repo, exec_repo, NullWorkflowTaskDispatcher())

    wf = await service.create(uuid4(), "Test", None, uuid4())
    # No steps -> activation fails
    with pytest.raises(WorkflowEmptyError):
        await service.activate(wf.id)


@pytest.mark.asyncio
async def test_activate_workflow_with_valid_dag():
    repo = FakeWorkflowRepository()
    step_repo = FakeWorkflowStepRepository()
    exec_repo = FakeWorkflowExecutionRepository()
    service = WorkflowService(repo, step_repo, exec_repo, NullWorkflowTaskDispatcher())

    wf = await service.create(uuid4(), "Test", None, uuid4())
    await service.add_step(wf.id, "echo", "Step 1", order=0)

    activated = await service.activate(wf.id)
    assert activated.status is WorkflowStatus.ACTIVE


@pytest.mark.asyncio
async def test_add_step_validates_dependency():
    repo = FakeWorkflowRepository()
    step_repo = FakeWorkflowStepRepository()
    exec_repo = FakeWorkflowExecutionRepository()
    service = WorkflowService(repo, step_repo, exec_repo, NullWorkflowTaskDispatcher())

    wf = await service.create(uuid4(), "Test", None, uuid4())
    missing = uuid4()
    with pytest.raises(WorkflowStepDependencyError):
        await service.add_step(wf.id, "echo", "Step", depends_on=[missing])


@pytest.mark.asyncio
async def test_execute_workflow_not_active():
    repo = FakeWorkflowRepository()
    step_repo = FakeWorkflowStepRepository()
    exec_repo = FakeWorkflowExecutionRepository()
    service = WorkflowService(repo, step_repo, exec_repo, NullWorkflowTaskDispatcher())

    wf = await service.create(uuid4(), "Test", None, uuid4())
    with pytest.raises(WorkflowNotExecutableError):
        await service.execute(wf.id, uuid4())


@pytest.mark.asyncio
async def test_delete_workflow():
    repo = FakeWorkflowRepository()
    step_repo = FakeWorkflowStepRepository()
    exec_repo = FakeWorkflowExecutionRepository()
    service = WorkflowService(repo, step_repo, exec_repo, NullWorkflowTaskDispatcher())

    wf = await service.create(uuid4(), "Test", None, uuid4())
    await service.delete(wf.id)
    assert await repo.get(wf.id) is None


@pytest.mark.asyncio
async def test_activate_empty_workflow_raises_workflow_empty_error():
    """Test 1: Activating a workflow with no steps raises WorkflowEmptyError."""
    repo = FakeWorkflowRepository()
    step_repo = FakeWorkflowStepRepository()
    exec_repo = FakeWorkflowExecutionRepository()
    service = WorkflowService(repo, step_repo, exec_repo, NullWorkflowTaskDispatcher())

    wf = await service.create(uuid4(), "Empty WF", None, uuid4())
    with pytest.raises(WorkflowEmptyError):
        await service.activate(wf.id)


@pytest.mark.asyncio
async def test_activate_empty_workflow_error_has_correct_message():
    """Test 2: WorkflowEmptyError carries a clear, structured message."""
    repo = FakeWorkflowRepository()
    step_repo = FakeWorkflowStepRepository()
    exec_repo = FakeWorkflowExecutionRepository()
    service = WorkflowService(repo, step_repo, exec_repo, NullWorkflowTaskDispatcher())

    wf = await service.create(uuid4(), "Empty WF", None, uuid4())
    with pytest.raises(WorkflowEmptyError, match=str(wf.id)):
        await service.activate(wf.id)


@pytest.mark.asyncio
async def test_activate_empty_workflow_status_remains_draft():
    """Test 3: Workflow status stays DRAFT when activation is rejected."""
    repo = FakeWorkflowRepository()
    step_repo = FakeWorkflowStepRepository()
    exec_repo = FakeWorkflowExecutionRepository()
    service = WorkflowService(repo, step_repo, exec_repo, NullWorkflowTaskDispatcher())

    wf = await service.create(uuid4(), "Empty WF", None, uuid4())
    with pytest.raises(WorkflowEmptyError):
        await service.activate(wf.id)

    stored = await repo.get(wf.id)
    assert stored is not None
    assert stored.status is WorkflowStatus.DRAFT


@pytest.mark.asyncio
async def test_activate_workflow_with_steps_still_works():
    """Test 4: Existing valid-activation path is not broken."""
    repo = FakeWorkflowRepository()
    step_repo = FakeWorkflowStepRepository()
    exec_repo = FakeWorkflowExecutionRepository()
    service = WorkflowService(repo, step_repo, exec_repo, NullWorkflowTaskDispatcher())

    wf = await service.create(uuid4(), "Valid WF", None, uuid4())
    await service.add_step(wf.id, "echo", "Step 1", order=0)

    activated = await service.activate(wf.id)
    assert activated.status is WorkflowStatus.ACTIVE
