"""
Workflow Executor (Phase 2/3).

Runs a WorkflowExecution by walking the DAG in topological order,
respecting dependencies, conditions, retries, and parallelism.

Each step's outputs are stored in `step_results` so downstream steps
can reference them. If a step has a `condition`, the condition is
evaluated against accumulated results before running the step.

Each step creates its own Scan record so that ToolResult.scan_id
always references a real scans row (FK-safe).
"""

from __future__ import annotations

import contextlib
from collections import defaultdict, deque
from datetime import UTC, datetime
from uuid import UUID, uuid4

import structlog
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.application.correlation_service import CorrelationService
from app.domain.entities import Scan, ToolResult, WorkflowStep
from app.domain.repositories import (
    ScanRepository,
    ToolResultRepository,
    WorkflowExecutionRepository,
    WorkflowStepRepository,
)
from app.domain.value_objects import ScanStatus
from app.plugins.manager import PluginManager
from app.plugins.normalizer_registry import NormalizerRegistry

logger = structlog.get_logger(__name__)


def _evaluate_condition(
    condition: dict[str, object],
    step_results: dict[str, dict[str, object]],
) -> bool:
    """
    Evaluate a step condition against accumulated step results.

    Condition format:
    {
        "step_id": "<uuid-string>",
        "operator": "equals|not_equals|exists|greater_than|less_than",
        "field": "status|exit_code|...",
        "value": <expected>
    }
    """
    step_id = str(condition.get("step_id", ""))
    operator = str(condition.get("operator", "exists"))
    field_name = str(condition.get("field", "status"))
    expected = condition.get("value")

    result = step_results.get(step_id, {})
    if not result:
        return operator == "not_equals"

    actual = result.get(field_name)

    if operator == "exists":
        return actual is not None
    elif operator == "equals":
        return str(actual) == str(expected)
    elif operator == "not_equals":
        return str(actual) != str(expected)
    elif operator == "greater_than":
        try:
            return float(actual or 0) > float(expected or 0)  # type: ignore[arg-type]
        except (ValueError, TypeError):
            return False
    elif operator == "less_than":
        try:
            return float(actual or 0) < float(expected or 0)  # type: ignore[arg-type]
        except (ValueError, TypeError):
            return False

    return True


class WorkflowExecutor:
    """
    Executes a workflow's steps in dependency order.

    Designed to run inside a Celery task (sync entrypoint bridges
    into async via asyncio.run, same pattern as ExecutionEngine).
    """

    def __init__(
        self,
        plugin_manager: PluginManager,
        normalizer_registry: NormalizerRegistry,
        execution_repository: WorkflowExecutionRepository,
        step_repository: WorkflowStepRepository,
        scan_repository: ScanRepository,
        tool_result_repository: ToolResultRepository,
        correlation_service: CorrelationService,
        default_timeout_seconds: int = 120,
    ) -> None:
        self._plugin_manager = plugin_manager
        self._normalizers = normalizer_registry
        self._executions = execution_repository
        self._steps = step_repository
        self._scans = scan_repository
        self._tool_results = tool_result_repository
        self._correlation = correlation_service
        self._default_timeout = default_timeout_seconds

    async def execute(self, execution_id: UUID) -> None:
        execution = await self._executions.get(execution_id)
        if execution is None:
            logger.error(
                "workflow_execution_missing", execution_id=str(execution_id)
            )
            return

        if execution.status is ScanStatus.CANCELLED:
            return

        log = logger.bind(
            execution_id=str(execution_id),
            workflow_id=str(execution.workflow_id),
        )

        await self._executions.update_status(execution_id, ScanStatus.RUNNING)
        log.info("workflow_execution_started")

        steps = await self._steps.list_for_workflow(execution.workflow_id)
        if not steps:
            await self._executions.update_status(
                execution_id, ScanStatus.COMPLETED
            )
            return

        step_map: dict[UUID, WorkflowStep] = {s.id: s for s in steps}
        step_results: dict[str, dict[str, object]] = dict(
            execution.step_results or {}
        )
        project_id = execution.project_id
        initiated_by = execution.initiated_by

        # Topological execution via BFS
        in_degree: dict[UUID, int] = {s.id: 0 for s in steps}
        adjacency: dict[UUID, list[UUID]] = defaultdict(list)
        for step in steps:
            for dep_id in step.depends_on:
                if dep_id in step_map:
                    adjacency[dep_id].append(step.id)
                    in_degree[step.id] += 1

        ready: deque[UUID] = deque(
            sid for sid, deg in in_degree.items() if deg == 0
        )
        completed_ids: set[UUID] = set()
        failed = False

        while ready and not failed:
            batch = list(ready)
            ready.clear()

            for step_id in batch:
                step = step_map[step_id]

                # Check cancellation
                execution = await self._executions.get(execution_id)
                if execution and execution.status is ScanStatus.CANCELLED:
                    log.info("workflow_execution_cancelled", step=step.name)
                    failed = True
                    break

                # Evaluate condition
                if step.has_condition and step.condition is not None and not _evaluate_condition(
                    step.condition, step_results
                ):
                    log.info(
                        "workflow_step_skipped_condition",
                        step=step.name,
                        condition=step.condition,
                    )
                    skip_result: dict[str, object] = {
                        "status": "skipped",
                        "reason": "condition_not_met",
                    }
                    step_results[str(step_id)] = skip_result
                    await self._executions.set_step_result(
                        execution_id, str(step_id), skip_result
                    )
                    completed_ids.add(step_id)
                    for neighbor in adjacency[step_id]:
                        in_degree[neighbor] -= 1
                        if in_degree[neighbor] == 0:
                            ready.append(neighbor)
                    continue

                # Execute with retries
                success = False
                last_error = ""
                for attempt in range(step.max_retries + 1):
                    if attempt > 0:
                        log.info(
                            "workflow_step_retry",
                            step=step.name,
                            attempt=attempt,
                        )

                    # Create a Scan for this step execution
                    step_scan_id = uuid4()
                    now = datetime.now(UTC)
                    step_scan = Scan(
                        id=step_scan_id,
                        project_id=project_id,
                        initiated_by=initiated_by,
                        plugin=step.plugin,
                        status=ScanStatus.RUNNING,
                        target_ids=[],
                        plugin_config=step.plugin_config,
                        created_at=now,
                        started_at=now,
                    )
                    try:
                        await self._scans.create(step_scan)
                    except (IntegrityError, SQLAlchemyError) as exc:
                        log.warning(
                            "workflow_step_scan_create_failed",
                            step=step.name,
                            error=str(exc),
                        )
                        last_error = str(exc)
                        break

                    try:
                        result = self._plugin_manager.run(
                            step.plugin,
                            step.plugin_config,
                            step.timeout_seconds or self._default_timeout,
                        )

                        # Normalize
                        normalized: dict[str, object] = {}
                        normalizer = self._normalizers.get(step.plugin)
                        if normalizer is not None:
                            with contextlib.suppress(Exception):
                                normalized = normalizer.normalize(
                                    result.stdout,
                                    result.stderr,
                                    step.plugin_config,
                                )

                        # Persist ToolResult referencing the step Scan
                        await self._tool_results.add(
                            ToolResult(
                                id=uuid4(),
                                scan_id=step_scan_id,
                                plugin=step.plugin,
                                target=str(
                                    step.plugin_config.get(
                                        "target",
                                        step.plugin_config.get(
                                            "hostname", ""
                                        ),
                                    )
                                ),
                                normalized_payload=normalized,
                                created_at=datetime.now(UTC),
                            )
                        )

                        if result.success:
                            await self._scans.complete(
                                step_scan_id,
                                result.exit_code or 0,
                                None,
                            )
                        else:
                            await self._scans.fail(
                                step_scan_id,
                                result.stderr or "Plugin reported failure",
                                result.exit_code,
                            )

                        step_result: dict[str, object] = {
                            "status": (
                                "completed" if result.success else "failed"
                            ),
                            "exit_code": result.exit_code,
                            "success": result.success,
                            "plugin": step.plugin,
                        }
                        step_results[str(step_id)] = step_result
                        await self._executions.set_step_result(
                            execution_id, str(step_id), step_result
                        )
                        success = result.success
                        last_error = (
                            result.stderr if not result.success else ""
                        )
                        break

                    except Exception as exc:
                        last_error = str(exc)
                        log.warning(
                            "workflow_step_error",
                            step=step.name,
                            error=last_error,
                            attempt=attempt,
                        )
                        # Mark the step Scan as failed
                        with contextlib.suppress(Exception):
                            await self._scans.fail(
                                step_scan_id, last_error, None
                            )

                if not success:
                    fail_result: dict[str, object] = {
                        "status": "failed",
                        "error": last_error,
                    }
                    step_results[str(step_id)] = fail_result
                    await self._executions.set_step_result(
                        execution_id, str(step_id), fail_result
                    )
                    failed = True
                    break

                completed_ids.add(step_id)
                for neighbor in adjacency[step_id]:
                    in_degree[neighbor] -= 1
                    if in_degree[neighbor] == 0:
                        ready.append(neighbor)

        # Update final status
        execution = await self._executions.get(execution_id)
        if execution and execution.status is ScanStatus.CANCELLED:
            return

        if failed:
            await self._executions.update_status(
                execution_id, ScanStatus.FAILED
            )
            log.warning("workflow_execution_failed")
        else:
            await self._executions.update_status(
                execution_id, ScanStatus.COMPLETED
            )
            log.info("workflow_execution_completed")
