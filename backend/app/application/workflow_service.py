"""
Workflow use-case service (Phase 2/3).

Handles workflow CRUD, DAG validation, and execution dispatch.
Cycles are detected via topological sort before any workflow is
activated or executed.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from app.domain.entities import Workflow, WorkflowExecution, WorkflowStep
from app.domain.exceptions import (
    WorkflowEmptyError,
    WorkflowExecutionNotCancellableError,
    WorkflowExecutionNotFoundError,
    WorkflowHasCyclesError,
    WorkflowNotExecutableError,
    WorkflowNotFoundError,
    WorkflowStepDependencyError,
)
from app.domain.repositories import (
    WorkflowExecutionRepository,
    WorkflowRepository,
    WorkflowStepRepository,
)
from app.domain.value_objects import ScanStatus, WorkflowStatus, WorkflowStepType


class WorkflowTaskDispatcher(Protocol):
    """Boundary for Celery — same pattern as ScanTaskDispatcher."""

    def dispatch_workflow(self, execution_id: UUID) -> None: ...


@dataclass(frozen=True, slots=True)
class NullWorkflowTaskDispatcher:
    """Test double that does nothing."""

    def dispatch_workflow(self, execution_id: UUID) -> None:
        return None


def validate_dag(steps: list[WorkflowStep]) -> list[UUID]:
    """
    Topological sort of steps via Kahn's algorithm.

    Returns the execution order (list of step IDs) or raises
    `WorkflowHasCyclesError` if a cycle exists. Also validates that
    every `depends_on` reference points to an existing step within
    the same workflow.
    """
    step_map: dict[UUID, WorkflowStep] = {s.id: s for s in steps}
    in_degree: dict[UUID, int] = {s.id: 0 for s in steps}
    adjacency: dict[UUID, list[UUID]] = defaultdict(list)

    for step in steps:
        for dep_id in step.depends_on:
            if dep_id not in step_map:
                raise WorkflowStepDependencyError(step.id, dep_id)
            adjacency[dep_id].append(step.id)
            in_degree[step.id] += 1

    queue: deque[UUID] = deque(
        sid for sid, deg in in_degree.items() if deg == 0
    )
    order: list[UUID] = []

    while queue:
        sid = queue.popleft()
        order.append(sid)
        for neighbor in adjacency[sid]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if len(order) != len(steps):
        cycle_nodes = [str(sid) for sid, deg in in_degree.items() if deg > 0]
        raise WorkflowHasCyclesError(" -> ".join(cycle_nodes))

    return order


class WorkflowService:
    def __init__(
        self,
        workflow_repository: WorkflowRepository,
        step_repository: WorkflowStepRepository,
        execution_repository: WorkflowExecutionRepository,
        task_dispatcher: WorkflowTaskDispatcher,
    ) -> None:
        self._workflows = workflow_repository
        self._steps = step_repository
        self._executions = execution_repository
        self._dispatcher = task_dispatcher

    async def create(
        self,
        project_id: UUID,
        name: str,
        description: str | None,
        created_by: UUID,
    ) -> Workflow:
        workflow = Workflow(
            id=uuid4(),
            project_id=project_id,
            name=name,
            description=description,
            status=WorkflowStatus.DRAFT,
            created_by=created_by,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        await self._workflows.create(workflow)
        return workflow

    async def get(self, workflow_id: UUID) -> Workflow:
        workflow = await self._workflows.get(workflow_id)
        if workflow is None:
            raise WorkflowNotFoundError(workflow_id)
        return workflow

    async def list_for_project(self, project_id: UUID) -> list[Workflow]:
        return await self._workflows.list_for_project(project_id)

    async def update(
        self,
        workflow_id: UUID,
        name: str | None = None,
        description: str | None = None,
    ) -> Workflow:
        workflow = await self.get(workflow_id)
        if name is not None:
            workflow.name = name
        if description is not None:
            workflow.description = description
        workflow.updated_at = datetime.now(UTC)
        await self._workflows.update(workflow)
        return workflow

    async def delete(self, workflow_id: UUID) -> None:
        await self.get(workflow_id)
        await self._steps.delete_for_workflow(workflow_id)
        await self._workflows.delete(workflow_id)

    async def activate(self, workflow_id: UUID) -> Workflow:
        """Validate the DAG then set status to ACTIVE."""
        workflow = await self.get(workflow_id)
        steps = await self._steps.list_for_workflow(workflow_id)
        if not steps:
            raise WorkflowEmptyError(workflow_id)
        validate_dag(steps)
        workflow.status = WorkflowStatus.ACTIVE
        workflow.updated_at = datetime.now(UTC)
        await self._workflows.update(workflow)
        return workflow

    async def archive(self, workflow_id: UUID) -> Workflow:
        workflow = await self.get(workflow_id)
        workflow.status = WorkflowStatus.ARCHIVED
        workflow.updated_at = datetime.now(UTC)
        await self._workflows.update(workflow)
        return workflow

    async def add_step(
        self,
        workflow_id: UUID,
        plugin: str,
        name: str,
        plugin_config: dict[str, object] | None = None,
        depends_on: list[UUID] | None = None,
        condition: dict[str, object] | None = None,
        timeout_seconds: int = 120,
        max_retries: int = 0,
        order: int = 0,
    ) -> WorkflowStep:
        await self.get(workflow_id)
        if depends_on:
            existing_steps = await self._steps.list_for_workflow(workflow_id)
            existing_ids = {s.id for s in existing_steps}
            for dep_id in depends_on:
                if dep_id not in existing_ids:
                    raise WorkflowStepDependencyError(uuid4(), dep_id)

        step = WorkflowStep(
            id=uuid4(),
            workflow_id=workflow_id,
            step_type=WorkflowStepType.SCAN,
            plugin=plugin,
            name=name,
            plugin_config=plugin_config or {},
            depends_on=depends_on or [],
            condition=condition,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            order=order,
        )
        await self._steps.add(step)
        return step

    async def update_step(
        self,
        step_id: UUID,
        plugin: str | None = None,
        name: str | None = None,
        plugin_config: dict[str, object] | None = None,
        depends_on: list[UUID] | None = None,
        condition: dict[str, object] | None = None,
        timeout_seconds: int | None = None,
        max_retries: int | None = None,
        order: int | None = None,
    ) -> WorkflowStep:
        step = await self._steps.get(step_id)
        if step is None:
            from app.domain.exceptions import DomainError
            raise DomainError(f"Workflow step {step_id} not found.")
        if plugin is not None:
            step.plugin = plugin
        if name is not None:
            step.name = name
        if plugin_config is not None:
            step.plugin_config = plugin_config
        if depends_on is not None:
            step.depends_on = depends_on
        if condition is not None:
            step.condition = condition
        if timeout_seconds is not None:
            step.timeout_seconds = timeout_seconds
        if max_retries is not None:
            step.max_retries = max_retries
        if order is not None:
            step.order = order
        await self._steps.update(step)
        return step

    async def delete_step(self, step_id: UUID) -> None:
        await self._steps.delete(step_id)

    async def list_steps(self, workflow_id: UUID) -> list[WorkflowStep]:
        return await self._steps.list_for_workflow(workflow_id)

    async def execute(
        self, workflow_id: UUID, initiated_by: UUID
    ) -> WorkflowExecution:
        workflow = await self.get(workflow_id)
        if not workflow.is_executable:
            raise WorkflowNotExecutableError(workflow_id, workflow.status.value)

        steps = await self._steps.list_for_workflow(workflow_id)
        if not steps:
            raise ValueError("Cannot execute a workflow with no steps.")

        validate_dag(steps)

        execution = WorkflowExecution(
            id=uuid4(),
            workflow_id=workflow_id,
            project_id=workflow.project_id,
            initiated_by=initiated_by,
            status=ScanStatus.QUEUED,
            created_at=datetime.now(UTC),
        )
        await self._executions.create(execution)
        self._dispatcher.dispatch_workflow(execution.id)
        return execution

    async def get_execution(self, execution_id: UUID) -> WorkflowExecution:
        execution = await self._executions.get(execution_id)
        if execution is None:
            raise WorkflowExecutionNotFoundError(execution_id)
        return execution

    async def list_executions(self, workflow_id: UUID) -> list[WorkflowExecution]:
        return await self._executions.list_for_workflow(workflow_id)

    async def cancel_execution(self, execution_id: UUID) -> WorkflowExecution:
        execution = await self.get_execution(execution_id)
        if not execution.is_cancellable:
            raise WorkflowExecutionNotCancellableError(
                execution_id, execution.status.value
            )
        await self._executions.update_status(execution_id, ScanStatus.CANCELLED)
        execution.status = ScanStatus.CANCELLED
        return execution
