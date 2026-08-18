"""
Advanced workflow engine (Milestone 5).

Orchestrates multi-step plugin workflows with:
- Dependency-aware execution (topological ordering)
- Parallel execution of independent steps
- Fan-out/fan-in patterns
- Conditional step execution
- Failure recovery with retries
- Partial rerun/resume support
- Step-level result tracking

The engine is a pure domain service — it receives a PluginExecutor
protocol for actual plugin invocation, keeping it free of infrastructure
imports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID, uuid4

from app.domain.conditional_engine import ConditionalExecutionEngine
from app.domain.workflow_templates import (
    WorkflowTemplate,
    WorkflowTemplateStep,
)


class StepExecutionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"
    RETRYING = "retrying"


@dataclass(slots=True)
class StepExecutionResult:
    """Result of a single step execution."""

    step_id: str
    status: StepExecutionStatus
    plugin: str
    success: bool = False
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    normalized_payload: dict[str, Any] = field(default_factory=dict)
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    attempt: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class WorkflowExecutionState:
    """
    Mutable state for a workflow execution run.

    Tracks completed/failed/skipped steps, accumulated outputs,
    and provides the data needed for resume and partial rerun.
    """

    workflow_id: str
    execution_id: UUID
    project_id: UUID
    initiated_by: UUID
    status: str = "running"
    step_results: dict[str, StepExecutionResult] = field(default_factory=dict)
    step_outputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str | None = None

    @property
    def completed_steps(self) -> set[str]:
        return {
            sid for sid, r in self.step_results.items()
            if r.status == StepExecutionStatus.COMPLETED
        }

    @property
    def failed_steps(self) -> set[str]:
        return {
            sid for sid, r in self.step_results.items()
            if r.status == StepExecutionStatus.FAILED
        }

    @property
    def skipped_steps(self) -> set[str]:
        return {
            sid for sid, r in self.step_results.items()
            if r.status == StepExecutionStatus.SKIPPED
        }

    @property
    def running_steps(self) -> set[str]:
        return {
            sid for sid, r in self.step_results.items()
            if r.status in (StepExecutionStatus.RUNNING, StepExecutionStatus.RETRYING)
        }

    @property
    def is_complete(self) -> bool:
        """True if all steps are in a terminal state."""
        terminal = {
            StepExecutionStatus.COMPLETED,
            StepExecutionStatus.FAILED,
            StepExecutionStatus.SKIPPED,
            StepExecutionStatus.CANCELLED,
        }
        return all(r.status in terminal for r in self.step_results.values())

    @property
    def has_failures(self) -> bool:
        return len(self.failed_steps) > 0

    def get_resumable_steps(self, all_steps: list[WorkflowTemplateStep]) -> list[str]:
        """Return step IDs that can be resumed (pending with deps met)."""
        cancelled_ids = {
            sid for sid, r in self.step_results.items()
            if r.status == StepExecutionStatus.CANCELLED
        }
        resumable: list[str] = []
        for step in all_steps:
            if step.id in self.completed_steps:
                continue
            if step.id in self.failed_steps:
                continue
            if step.id in self.skipped_steps:
                continue
            if step.id in self.running_steps:
                continue
            if step.id in cancelled_ids:
                continue
            deps_met = all(dep in self.completed_steps for dep in step.depends_on)
            if deps_met:
                resumable.append(step.id)
        return resumable


class PluginExecutor(Protocol):
    """Protocol for plugin execution — allows engine to stay domain-only."""

    def execute(
        self, plugin_name: str, config: dict[str, Any], timeout_seconds: int
    ) -> tuple[bool, str, str, int | None, dict[str, Any]]:
        """
        Execute a plugin and return:
        (success, stdout, stderr, exit_code, normalized_payload)
        """
        ...


class WorkflowExecutionError(Exception):
    """Raised when a workflow execution encounters an unrecoverable error."""

    def __init__(self, workflow_id: str, reason: str) -> None:
        self.workflow_id = workflow_id
        self.reason = reason
        super().__init__(f"Workflow '{workflow_id}' execution error: {reason}")


class WorkflowEngine:
    """
    Advanced workflow execution engine.

    Orchestrates multi-step plugin workflows with parallel execution,
    conditional gates, failure recovery, and resume support.

    The engine is stateless — all state lives in WorkflowExecutionState.
    This allows the engine to be tested in isolation and the state to
    be serialized/deserialized for persistence.
    """

    def __init__(
        self,
        plugin_executor: PluginExecutor,
        condition_engine: ConditionalExecutionEngine | None = None,
    ) -> None:
        self._executor = plugin_executor
        self._conditions = condition_engine or ConditionalExecutionEngine()

    def create_execution_state(
        self,
        template: WorkflowTemplate,
        project_id: UUID,
        initiated_by: UUID,
    ) -> WorkflowExecutionState:
        """Create a fresh execution state for a template."""
        state = WorkflowExecutionState(
            workflow_id=template.id,
            execution_id=uuid4(),
            project_id=project_id,
            initiated_by=initiated_by,
            started_at=datetime.now(UTC),
        )
        # Initialize all steps as pending
        for step in template.get_enabled_steps():
            state.step_results[step.id] = StepExecutionResult(
                step_id=step.id,
                status=StepExecutionStatus.PENDING,
                plugin=step.plugin,
            )
        return state

    def get_next_steps(
        self,
        template: WorkflowTemplate,
        state: WorkflowExecutionState,
    ) -> list[WorkflowTemplateStep]:
        """
        Return steps ready to execute based on current state.

        Steps are ready when:
        1. All dependencies are completed
        2. Condition evaluates to True
        3. Step is enabled
        """
        return self._conditions.get_ready_steps(
            steps=template.get_enabled_steps(),
            step_outputs=state.step_outputs,
            completed_steps=state.completed_steps,
            failed_steps=state.failed_steps,
        )

    def execute_step(
        self,
        step: WorkflowTemplateStep,
        state: WorkflowExecutionState,
        base_config: dict[str, Any] | None = None,
    ) -> StepExecutionResult:
        """
        Execute a single workflow step.

        Returns the step execution result. Does not modify state —
        caller is responsible for updating state.step_results.
        """
        # Merge config: step overrides + base config + variable substitution
        config = dict(base_config or {})
        config.update(step.config_overrides)
        config = template_substitute(config, state)

        result = StepExecutionResult(
            step_id=step.id,
            status=StepExecutionStatus.RUNNING,
            plugin=step.plugin,
            started_at=datetime.now(UTC),
            attempt=1,
        )

        try:
            success, stdout, stderr, exit_code, normalized = self._executor.execute(
                plugin_name=step.plugin,
                config=config,
                timeout_seconds=step.timeout_seconds,
            )
            result.success = success
            result.stdout = stdout
            result.stderr = stderr
            result.exit_code = exit_code
            result.normalized_payload = normalized
            result.status = (
                StepExecutionStatus.COMPLETED if success else StepExecutionStatus.FAILED
            )
            if not success:
                result.error_message = stderr or f"Plugin '{step.plugin}' returned non-zero exit"
        except Exception as exc:
            result.status = StepExecutionStatus.FAILED
            result.error_message = str(exc)

        result.completed_at = datetime.now(UTC)
        return result

    def execute_step_with_retry(
        self,
        step: WorkflowTemplateStep,
        state: WorkflowExecutionState,
        base_config: dict[str, Any] | None = None,
    ) -> StepExecutionResult:
        """Execute a step with automatic retries on failure."""
        max_attempts = step.max_retries + 1
        last_result: StepExecutionResult | None = None

        for attempt in range(1, max_attempts + 1):
            result = self.execute_step(step, state, base_config)
            result.attempt = attempt

            if result.success:
                return result

            last_result = result
            if attempt < max_attempts:
                result.status = StepExecutionStatus.RETRYING

        return last_result or StepExecutionResult(
            step_id=step.id,
            status=StepExecutionStatus.FAILED,
            plugin=step.plugin,
            error_message="No attempts executed",
        )

    def execute_parallel_steps(
        self,
        steps: list[WorkflowTemplateStep],
        state: WorkflowExecutionState,
        base_config: dict[str, Any] | None = None,
    ) -> list[StepExecutionResult]:
        """
        Execute multiple independent steps (same dependency layer).

        In production, these would run in parallel via Celery.
        This method runs them sequentially for testing/single-process
        execution — the execution order within a layer is deterministic.
        """
        results: list[StepExecutionResult] = []
        for step in sorted(steps, key=lambda s: s.order):
            result = self.execute_step_with_retry(step, state, base_config)
            results.append(result)
        return results

    def execute_workflow(
        self,
        template: WorkflowTemplate,
        state: WorkflowExecutionState,
        base_config: dict[str, Any] | None = None,
    ) -> WorkflowExecutionState:
        """
        Execute an entire workflow from start to finish.

        Processes layers sequentially — steps within each layer can
        theoretically run in parallel but are executed sequentially
        here for simplicity and testability.

        Updates state in-place and returns it.
        """
        execution_layers = template.get_execution_order()
        if not execution_layers:
            state.status = "completed"
            state.completed_at = datetime.now(UTC)
            return state

        for layer in execution_layers:
            # Filter to enabled, pending steps in this layer
            terminal_ids = state.completed_steps | state.failed_steps | state.skipped_steps
            cancelled_ids = {
                sid for sid, r in state.step_results.items()
                if r.status == StepExecutionStatus.CANCELLED
            }
            layer_steps = [
                s for s in template.get_enabled_steps()
                if s.id in layer
                and s.id not in terminal_ids
                and s.id not in cancelled_ids
            ]

            # Evaluate conditions and skip steps that don't pass
            for step in list(layer_steps):
                if not self._conditions.evaluate_step(step, state.step_outputs):
                    state.step_results[step.id] = StepExecutionResult(
                        step_id=step.id,
                        status=StepExecutionStatus.SKIPPED,
                        plugin=step.plugin,
                    )
                    layer_steps.remove(step)

            if not layer_steps:
                continue

            # Execute the layer
            results = self.execute_parallel_steps(layer_steps, state, base_config)

            # Update state with results
            for result in results:
                state.step_results[result.step_id] = result
                if result.success:
                    state.step_outputs[result.step_id] = result.normalized_payload
                elif result.status == StepExecutionStatus.FAILED:
                    # Mark dependent steps as cancelled
                    self._cancel_dependents(template, result.step_id, state)

        # Determine final status
        if state.has_failures:
            state.status = "failed"
        elif state.is_complete:
            state.status = "completed"
        else:
            state.status = "completed"

        state.completed_at = datetime.now(UTC)
        return state

    def resume_workflow(
        self,
        template: WorkflowTemplate,
        state: WorkflowExecutionState,
        base_config: dict[str, Any] | None = None,
    ) -> WorkflowExecutionState:
        """
        Resume a previously paused/failed workflow.

        Only executes steps that haven't completed yet and whose
        dependencies are satisfied.
        """
        state.status = "running"
        state.started_at = datetime.now(UTC)

        resumable_ids = state.get_resumable_steps(template.get_enabled_steps())
        if not resumable_ids:
            state.status = "completed" if not state.has_failures else "failed"
            state.completed_at = datetime.now(UTC)
            return state

        # Build a sub-template with only resumable steps
        # and execute layer by layer
        execution_layers = template.get_execution_order()
        for layer in execution_layers:
            cancelled_ids = {
                sid for sid, r in state.step_results.items()
                if r.status == StepExecutionStatus.CANCELLED
            }
            layer_steps = [
                s for s in template.get_enabled_steps()
                if s.id in layer and s.id in resumable_ids
                and s.id not in cancelled_ids
            ]

            if not layer_steps:
                continue

            results = self.execute_parallel_steps(layer_steps, state, base_config)

            for result in results:
                state.step_results[result.step_id] = result
                if result.success:
                    state.step_outputs[result.step_id] = result.normalized_payload
                elif result.status == StepExecutionStatus.FAILED:
                    self._cancel_dependents(template, result.step_id, state)

            # Update resumable list after this layer
            resumable_ids = state.get_resumable_steps(template.get_enabled_steps())

        if state.has_failures:
            state.status = "failed"
        elif state.is_complete:
            state.status = "completed"

        state.completed_at = datetime.now(UTC)
        return state

    def _cancel_dependents(
        self,
        template: WorkflowTemplate,
        failed_step_id: str,
        state: WorkflowExecutionState,
    ) -> None:
        """Cancel all steps that transitively depend on a failed step."""
        dependents = template.get_dependents(failed_step_id)
        for dep_step in dependents:
            if dep_step.id in state.completed_steps:
                continue
            if dep_step.id not in state.failed_steps:
                state.step_results[dep_step.id] = StepExecutionResult(
                    step_id=dep_step.id,
                    status=StepExecutionStatus.CANCELLED,
                    plugin=dep_step.plugin,
                    error_message=f"Cancelled: dependency '{failed_step_id}' failed",
                )
                # Recursively cancel transitive dependents
                self._cancel_dependents(template, dep_step.id, state)


def template_substitute(
    config: dict[str, Any], state: WorkflowExecutionState
) -> dict[str, Any]:
    """Replace {{variable}} placeholders with values from state outputs."""
    result = dict(config)
    for key, value in result.items():
        if isinstance(value, str) and "{{" in value:
            for step_id, output in state.step_outputs.items():
                placeholder = "{{" + step_id + "}}"
                if placeholder in value:
                    result[key] = value.replace(placeholder, str(output))
    return result
