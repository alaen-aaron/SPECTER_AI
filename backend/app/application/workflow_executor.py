"""
Workflow Executor (Phase 2/3, hardened M7.5 Phase 1).

Runs a WorkflowExecution by walking the DAG in topological order,
respecting dependencies, conditions, retries, and parallelism.

Each step's outputs are stored in `step_results` so downstream steps
can reference them. If a step has a `condition`, the condition is
evaluated against accumulated results before running the step.

M7.5 Phase 1 hardening — each step now executes through the SAME
canonical path as an ordinary scan, instead of bypassing it:

1. **Target resolution (fail-closed)**: the step's `plugin_config`
   target/hostname/url values must resolve to REGISTERED Target rows of
   the workflow's project (via `TargetRepository.list_for_project`). A
   step that references no target, or a value that is not a registered
   Target, is rejected before anything executes and fails the workflow —
   there is no `target_ids=[]` fallback path anymore.
2. **Scope Guard + plugin-config validation**: the scan is created via
   `ScanService.create`, which runs `ScopeGuardService.validate_targets`
   (project active, authorization valid, every target in-project and
   in-scope) and `PluginManager.validate` BEFORE the row is persisted.
3. **Canonical execution**: the scan is then executed through the
   execution engine (`ExecutionEngineRun` protocol — the same engine the
   `specter.execute_scan` Celery task uses), which re-validates scope
   immediately before plugin invocation, applies the M7.1
   AuthorizedTargetRunner isolation policy from the scan's registered
   target IDs, and persists the ToolResult + correlation + assets +
   metrics + audit.
4. **Audit**: append-only `workflow.execution.*` / `workflow.step.*`
   events are written best-effort (never able to fail the run).

Each step creates its own Scan record so ToolResult.scan_id always
references a real scans row (FK-safe).
"""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

import structlog

from app.application.scan_service import ScanService
from app.domain.entities import AuditLogEntry, Target, WorkflowStep
from app.domain.exceptions import WorkflowStepTargetError
from app.domain.repositories import (
    AuditLogRepository,
    TargetRepository,
    WorkflowExecutionRepository,
    WorkflowStepRepository,
)
from app.domain.value_objects import ScanStatus

logger = structlog.get_logger(__name__)

# The only plugin_config keys that carry a host/domain/URL to scan. A step
# whose config references targets under any other key (or references no
# target at all) is rejected fail-closed: workflow steps may ONLY scan
# registered, authorization-covered Target rows.
_TARGET_CONFIG_KEYS = ("target", "hostname", "url")


class ScanExecutionEngine(Protocol):
    """Boundary for the ExecutionEngine (same-process, canonical runner).

    Satisfied structurally by `infrastructure.execution.engine.ExecutionEngine`;
    declared here so `application/` never imports from `infrastructure/`.
    `run(scan_id)` re-validates Scope Guard, invokes the plugin through the
    plugin manager (with M7.1 isolation), and persists the ToolResult,
    correlation, assets, metrics, and audit trail for the scan.
    """

    async def run(self, scan_id: UUID) -> None: ...


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


def _extract_target_values(plugin_config: dict[str, object]) -> list[str]:
    """Collect the raw target values a step's config points at."""
    values: list[str] = []
    for key in _TARGET_CONFIG_KEYS:
        raw = plugin_config.get(key)
        if raw is None or raw == "":
            continue
        if isinstance(raw, str):
            value = raw.strip()
            if value:
                values.append(value)
        elif isinstance(raw, (list, tuple)):
            for item in raw:
                if isinstance(item, str) and item.strip():
                    values.append(item.strip())
    return values


class WorkflowExecutor:
    """
    Executes a workflow's steps in dependency order.

    Designed to run inside a Celery task (sync entrypoint bridges
    into async via asyncio.run, same pattern as ExecutionEngine).
    """

    def __init__(
        self,
        scan_service: ScanService,
        execution_engine: ScanExecutionEngine,
        target_repository: TargetRepository,
        execution_repository: WorkflowExecutionRepository,
        step_repository: WorkflowStepRepository,
        audit_log_repository: AuditLogRepository,
    ) -> None:
        self._scan_service = scan_service
        self._engine = execution_engine
        self._targets = target_repository
        self._executions = execution_repository
        self._steps = step_repository
        self._audit = audit_log_repository
        self._execution_id: UUID | None = None
        self._actor_id: UUID | None = None

    async def execute(self, execution_id: UUID) -> None:
        self._execution_id = execution_id
        execution = await self._executions.get(execution_id)
        if execution is None:
            logger.error("workflow_execution_missing", execution_id=str(execution_id))
            return

        if execution.status is ScanStatus.CANCELLED:
            return

        log = logger.bind(
            execution_id=str(execution_id),
            workflow_id=str(execution.workflow_id),
        )
        self._actor_id = execution.initiated_by

        await self._executions.update_status(execution_id, ScanStatus.RUNNING)
        await self._write_audit("workflow.execution.started", {})
        log.info("workflow_execution_started")

        steps = await self._steps.list_for_workflow(execution.workflow_id)
        if not steps:
            await self._executions.update_status(execution_id, ScanStatus.COMPLETED)
            await self._write_audit("workflow.execution.completed", {"steps": 0})
            return

        step_map: dict[UUID, WorkflowStep] = {s.id: s for s in steps}
        step_results: dict[str, dict[str, object]] = dict(execution.step_results or {})
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

        ready: deque[UUID] = deque(sid for sid, deg in in_degree.items() if deg == 0)
        failed = False
        executed_steps = 0

        while ready and not failed:
            batch = list(ready)
            ready.clear()

            for step_id in batch:
                step = step_map[step_id]

                # Check cancellation
                execution = await self._executions.get(execution_id)
                if execution and execution.status is ScanStatus.CANCELLED:
                    log.info("workflow_execution_cancelled", step=step.name)
                    await self._write_audit("workflow.execution.cancelled", {"step": step.name})
                    failed = True
                    break

                # Evaluate condition
                if (
                    step.has_condition
                    and step.condition is not None
                    and not _evaluate_condition(step.condition, step_results)
                ):
                    log.info(
                        "workflow_step_skipped_condition",
                        step=step.name,
                        condition=step.condition,
                    )
                    skip_result: dict[str, object] = {
                        "status": "skipped",
                        "reason": "condition_not_met",
                        "plugin": step.plugin,
                    }
                    step_results[str(step_id)] = skip_result
                    await self._executions.set_step_result(execution_id, str(step_id), skip_result)
                    await self._write_audit(
                        "workflow.step.skipped",
                        {"step_id": str(step_id), "plugin": step.plugin},
                    )
                    executed_steps += 1
                    for neighbor in adjacency[step_id]:
                        in_degree[neighbor] -= 1
                        if in_degree[neighbor] == 0:
                            ready.append(neighbor)
                    continue

                step_result = await self._run_step(step, project_id, initiated_by)
                step_results[str(step_id)] = step_result
                await self._executions.set_step_result(execution_id, str(step_id), step_result)

                if not step_result.get("success", False):
                    failed = True
                    break

                executed_steps += 1
                for neighbor in adjacency[step_id]:
                    in_degree[neighbor] -= 1
                    if in_degree[neighbor] == 0:
                        ready.append(neighbor)

        # Update final status
        execution = await self._executions.get(execution_id)
        if execution and execution.status is ScanStatus.CANCELLED:
            return

        if failed:
            await self._executions.update_status(execution_id, ScanStatus.FAILED)
            await self._write_audit(
                "workflow.execution.failed",
                {"steps_completed": executed_steps},
            )
            log.warning("workflow_execution_failed")
        else:
            await self._executions.update_status(execution_id, ScanStatus.COMPLETED)
            await self._write_audit(
                "workflow.execution.completed",
                {"steps_completed": executed_steps},
            )
            log.info("workflow_execution_completed")

    # --- step execution through the canonical path --------------------------

    async def _run_step(
        self,
        step: WorkflowStep,
        project_id: UUID,
        initiated_by: UUID,
    ) -> dict[str, object]:
        log = logger.bind(step=step.name, step_id=str(step.id), plugin=step.plugin)
        try:
            target_ids = await self._resolve_targets(project_id, step)
        except WorkflowStepTargetError as exc:
            log.warning("workflow_step_target_rejected", reason=str(exc))
            await self._write_audit(
                "workflow.step.failed",
                {
                    "step_id": str(step.id),
                    "plugin": step.plugin,
                    "reason": str(exc),
                },
            )
            return {
                "status": "failed",
                "success": False,
                "plugin": step.plugin,
                "error": str(exc),
            }

        last_error = ""
        for attempt in range(step.max_retries + 1):
            if attempt > 0:
                log.info(
                    "workflow_step_retry",
                    step=step.name,
                    attempt=attempt,
                    target_ids=[str(t) for t in target_ids],
                )

            try:
                scan = await self._scan_service.create(
                    project_id,
                    step.plugin,
                    step.plugin_config,
                    target_ids,
                    initiated_by,
                )
            except Exception as exc:  # noqa: BLE001 - Scope Guard / plugin-config rejection
                last_error = str(exc)
                log.warning(
                    "workflow_step_scan_create_rejected",
                    step=step.name,
                    error=last_error,
                    attempt=attempt,
                )
                continue

            try:
                await self._engine.run(scan.id)
            except Exception as exc:  # noqa: BLE001 - engine failures fail the step
                last_error = str(exc)
                log.warning(
                    "workflow_step_execution_error",
                    step=step.name,
                    scan_id=str(scan.id),
                    error=last_error,
                    attempt=attempt,
                )
                continue

            try:
                scan = await self._scan_service.get(scan.id)
            except Exception:  # noqa: BLE001 - treat a vanished scan as an error
                last_error = "scan record vanished after engine run"
                log.error("workflow_step_scan_missing", scan_id=str(scan.id))
                continue

            if scan.status is ScanStatus.COMPLETED:
                step_result: dict[str, object] = {
                    "status": "completed",
                    "success": True,
                    "plugin": step.plugin,
                    "scan_id": str(scan.id),
                    "exit_code": scan.exit_code,
                }
                await self._write_audit(
                    "workflow.step.completed",
                    {
                        "step_id": str(step.id),
                        "plugin": step.plugin,
                        "scan_id": str(scan.id),
                        "target_ids": [str(t) for t in target_ids],
                    },
                )
                return step_result

            last_error = scan.error_message or "step scan did not complete"
            log.warning(
                "workflow_step_failed",
                step=step.name,
                scan_id=str(scan.id),
                status=scan.status.value,
                error=last_error,
                attempt=attempt,
            )

        fail_result: dict[str, object] = {
            "status": "failed",
            "success": False,
            "plugin": step.plugin,
            "error": last_error,
        }
        await self._write_audit(
            "workflow.step.failed",
            {"step_id": str(step.id), "plugin": step.plugin, "error": last_error},
        )
        return fail_result

    async def _resolve_targets(self, project_id: UUID, step: WorkflowStep) -> list[UUID]:
        """Map a step's raw target values to registered Project Target rows.

        Fail-closed, by design:
        - no target value in the config       -> rejected
        - value is not a registered Target    -> rejected
        Only registered Target IDs (which the canonical Scope Guard will
        further re-check for in-scope authorization) are ever returned.
        """
        values = _extract_target_values(step.plugin_config)
        if not values:
            raise WorkflowStepTargetError(
                step.id,
                step.plugin,
                "the step references no target — plugin_config must include "
                "a 'target', 'hostname', or 'url' key.",
            )

        registered = await self._targets.list_for_project(project_id)
        by_value: dict[str, Target] = {}
        for registered_target in registered:
            by_value.setdefault(registered_target.value, registered_target)

        target_ids: list[UUID] = []
        for value in values:
            target = by_value.get(value)
            if target is None:
                raise WorkflowStepTargetError(
                    step.id,
                    step.plugin,
                    f"target '{value}' is not a registered Target of this "
                    "project — register it (and keep it in scope) before "
                    "running the workflow.",
                )
            target_ids.append(target.id)
        return target_ids

    # --- audit ---------------------------------------------------------------

    async def _write_audit(self, action: str, extra: dict[str, object]) -> None:
        if self._actor_id is None or self._execution_id is None:
            return
        try:
            await self._audit.add(
                AuditLogEntry(
                    id=uuid4(),
                    organization_id=None,
                    actor_id=self._actor_id,
                    action=action,
                    target_type="workflow_execution",
                    target_id=self._execution_id,
                    ip_address=None,
                    created_at=datetime.now(UTC),
                    after_state=extra,
                )
            )
        except Exception:  # noqa: BLE001 - audit is best-effort, never fails the run
            logger.warning("workflow_audit_write_failed", action=action)
