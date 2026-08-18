"""
Conditional execution engine (Milestone 5).

Evaluates step conditions against accumulated workflow outputs to
determine whether a step should run, skip, or fail. Conditions are
declarative (StepCondition dataclasses) and evaluated purely — no I/O,
no side effects — so they can be tested and reasoned about in isolation.

The engine is stateless: it receives the current step's condition and
the full history of step outputs, and returns a boolean decision.
"""

from __future__ import annotations

from typing import Any

from app.domain.workflow_templates import (
    WorkflowTemplateStep,
)


class ConditionalExecutionEngine:
    """
    Evaluates conditions for workflow step execution.

    Stateless — all state comes from the step_outputs dict passed to
    evaluate(). Can be instantiated once and reused across workflow
    executions.
    """

    def evaluate_step(
        self,
        step: WorkflowTemplateStep,
        step_outputs: dict[str, dict[str, Any]],
    ) -> bool:
        """
        Evaluate whether a step should execute based on its condition.

        Returns True if:
        - Step has no condition (unconditional)
        - Step's condition evaluates to True against accumulated outputs

        Returns False only if the condition explicitly evaluates to False.
        """
        if step.condition is None:
            return True

        return step.condition.evaluate(step_outputs)

    def evaluate_all_pending(
        self,
        steps: list[WorkflowTemplateStep],
        step_outputs: dict[str, dict[str, Any]],
        completed_steps: set[str],
    ) -> dict[str, bool]:
        """
        Evaluate conditions for all pending steps.

        Returns dict mapping step_id → should_execute.
        """
        results: dict[str, bool] = {}
        for step in steps:
            if step.id in completed_steps:
                continue
            results[step.id] = self.evaluate_step(step, step_outputs)
        return results

    def get_ready_steps(
        self,
        steps: list[WorkflowTemplateStep],
        step_outputs: dict[str, dict[str, Any]],
        completed_steps: set[str],
        failed_steps: set[str],
    ) -> list[WorkflowTemplateStep]:
        """
        Return steps that are ready to execute:

        1. All dependencies are completed (not failed)
        2. Condition evaluates to True
        3. Step is enabled
        """
        ready: list[WorkflowTemplateStep] = []
        for step in steps:
            if not step.enabled:
                continue
            if step.id in completed_steps or step.id in failed_steps:
                continue

            # Check all dependencies are completed
            deps_met = all(dep in completed_steps for dep in step.depends_on)
            if not deps_met:
                continue

            # Evaluate condition
            if not self.evaluate_step(step, step_outputs):
                continue

            ready.append(step)

        return ready

    def get_skipped_steps(
        self,
        steps: list[WorkflowTemplateStep],
        step_outputs: dict[str, dict[str, Any]],
        completed_steps: set[str],
        failed_steps: set[str],
    ) -> list[WorkflowTemplateStep]:
        """
        Return steps that should be skipped because their condition
        evaluated to False and all dependencies are met.
        """
        skipped: list[WorkflowTemplateStep] = []
        for step in steps:
            if not step.enabled:
                continue
            if step.id in completed_steps or step.id in failed_steps:
                continue

            deps_met = all(dep in completed_steps for dep in step.depends_on)
            if not deps_met:
                continue

            if not self.evaluate_step(step, step_outputs):
                skipped.append(step)

        return skipped
