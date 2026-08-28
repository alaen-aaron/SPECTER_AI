"""
Autonomous Orchestration Service (M7.4 Phase 1).

CRUD + state machine for autonomous scan runs. No LLM integration yet —
that arrives in Phase 2. This service owns:
  - Creating runs (with concurrency guard: one active run per project)
  - State transitions (enforced via VALID_AUTONOMOUS_TRANSITIONS)
  - Action lifecycle (propose / approve / reject / record scan)
  - Budget enforcement (max_actions, max_runtime)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from app.domain.entities import AutonomousRun, AutonomousRunAction
from app.domain.exceptions import (
    AutonomousActionNotApprovableError,
    AutonomousRunActiveExistsError,
    AutonomousRunBudgetExceededError,
    AutonomousRunInvalidTransitionError,
    AutonomousRunNotCancellableError,
    AutonomousRunNotFoundError,
)
from app.domain.repositories import (
    AutonomousRunActionRepository,
    AutonomousRunRepository,
)
from app.domain.value_objects import (
    ActionCategory,
    AutonomousRunStatus,
)


class _Clock(Protocol):
    def utcnow(self) -> datetime: ...


class _IdGenerator(Protocol):
    def __call__(self) -> UUID: ...


class AutonomousService:
    """Use-case service for autonomous scan orchestration."""

    def __init__(
        self,
        run_repo: AutonomousRunRepository,
        action_repo: AutonomousRunActionRepository,
        *,
        clock: _Clock | None = None,
        id_factory: _IdGenerator | None = None,
    ) -> None:
        self._run_repo = run_repo
        self._action_repo = action_repo
        self._clock = clock or _SystemClock()
        self._id = id_factory or uuid4

    # ── CRUD ──────────────────────────────────────────────────────────────

    async def create(
        self,
        *,
        project_id: UUID,
        initiated_by: UUID,
        objective: str = "",
        max_actions: int = 20,
        max_runtime_seconds: int = 1800,
    ) -> AutonomousRun:
        """Create a new autonomous run. Rejects if one is already active."""
        existing = await self._run_repo.get_active_for_project(project_id)
        if existing is not None:
            raise AutonomousRunActiveExistsError(project_id)

        now = self._clock.utcnow()
        run = AutonomousRun(
            id=self._id(),
            project_id=project_id,
            initiated_by=initiated_by,
            status=AutonomousRunStatus.CREATED,
            objective=objective,
            max_actions=max_actions,
            max_runtime_seconds=max_runtime_seconds,
            created_at=now,
        )
        await self._run_repo.create(run)
        return run

    async def get(self, run_id: UUID) -> AutonomousRun:
        run = await self._run_repo.get(run_id)
        if run is None:
            raise AutonomousRunNotFoundError(run_id)
        return run

    async def list_for_project(
        self,
        project_id: UUID,
        status: AutonomousRunStatus | None = None,
        limit: int = 20,
        cursor: datetime | None = None,
    ) -> list[AutonomousRun]:
        return await self._run_repo.list_for_project(
            project_id, status=status, limit=limit, cursor=cursor
        )

    async def cancel(self, run_id: UUID) -> AutonomousRun:
        """Cancel a run that hasn't reached a terminal state."""
        run = await self.get(run_id)
        if run.is_terminal:
            raise AutonomousRunNotCancellableError(run_id, run.status.value)
        await self._transition(run, AutonomousRunStatus.CANCELLED)
        return run

    async def get_active_for_project(self, project_id: UUID) -> AutonomousRun | None:
        return await self._run_repo.get_active_for_project(project_id)

    # ── State Machine ─────────────────────────────────────────────────────

    async def start_planning(self, run_id: UUID) -> AutonomousRun:
        """CREATED → PLANNING."""
        run = await self.get(run_id)
        if run.status != AutonomousRunStatus.CREATED:
            raise AutonomousRunInvalidTransitionError(run.status.value, "planning")
        now = self._clock.utcnow()
        run.started_at = run.started_at or now
        await self._transition(run, AutonomousRunStatus.PLANNING)
        return run

    async def plan_complete(self, run_id: UUID) -> AutonomousRun:
        """PLANNING → AWAITING_APPROVAL."""
        run = await self.get(run_id)
        if run.status != AutonomousRunStatus.PLANNING:
            raise AutonomousRunInvalidTransitionError(run.status.value, "awaiting_approval")
        await self._transition(run, AutonomousRunStatus.AWAITING_APPROVAL)
        return run

    async def approval_granted(self, run_id: UUID) -> AutonomousRun:
        """AWAITING_APPROVAL → EXECUTING."""
        run = await self.get(run_id)
        if run.status != AutonomousRunStatus.AWAITING_APPROVAL:
            raise AutonomousRunInvalidTransitionError(run.status.value, "executing")
        await self._transition(run, AutonomousRunStatus.EXECUTING)
        return run

    async def begin_execution(self, run_id: UUID) -> AutonomousRun:
        """PLANNING → EXECUTING.

        M7.4 Phase 2 additive: a bounded cycle whose decisions were all
        auto-approved by policy advances PLANNING → EXECUTING without a
        manual approval hop (approval_mode=AUTO_POLICY on each action).
        """
        run = await self.get(run_id)
        if run.status != AutonomousRunStatus.PLANNING:
            raise AutonomousRunInvalidTransitionError(run.status.value, "executing")
        await self._transition(run, AutonomousRunStatus.EXECUTING)
        return run

    async def complete(self, run_id: UUID) -> AutonomousRun:
        """PLANNING → COMPLETED.

        M7.4 Phase 2 additive: the planner produced nothing executable
        (or the budget is spent with nothing left to do) so the bounded
        cycle ends directly instead of fabricating executing/observing.
        """
        run = await self.get(run_id)
        if run.status != AutonomousRunStatus.PLANNING:
            raise AutonomousRunInvalidTransitionError(run.status.value, "completed")
        run.completed_at = self._clock.utcnow()
        await self._transition(run, AutonomousRunStatus.COMPLETED)
        return run

    async def execution_complete(self, run_id: UUID) -> AutonomousRun:
        """EXECUTING → OBSERVING."""
        run = await self.get(run_id)
        if run.status != AutonomousRunStatus.EXECUTING:
            raise AutonomousRunInvalidTransitionError(run.status.value, "observing")
        await self._transition(run, AutonomousRunStatus.OBSERVING)
        return run

    async def observation_complete(
        self, run_id: UUID, *, should_continue: bool = False
    ) -> AutonomousRun:
        """OBSERVING → PLANNING (re-plan) or COMPLETED."""
        run = await self.get(run_id)
        if run.status != AutonomousRunStatus.OBSERVING:
            raise AutonomousRunInvalidTransitionError(run.status.value, "planning/completed")

        if should_continue:
            # Check budget before re-planning
            self._check_budget(run)
            run.current_cycle += 1
            await self._transition(run, AutonomousRunStatus.PLANNING)
        else:
            run.completed_at = self._clock.utcnow()
            await self._transition(run, AutonomousRunStatus.COMPLETED)
        return run

    async def fail(self, run_id: UUID, error_message: str) -> AutonomousRun:
        """Transition to FAILED from any non-terminal state."""
        run = await self.get(run_id)
        if run.is_terminal:
            raise AutonomousRunInvalidTransitionError(run.status.value, "failed")
        run.error_message = error_message
        run.completed_at = self._clock.utcnow()
        await self._transition(run, AutonomousRunStatus.FAILED)
        return run

    async def heartbeat(self, run_id: UUID) -> AutonomousRun:
        """Update the heartbeat timestamp (called by the executor loop)."""
        run = await self.get(run_id)
        run.last_heartbeat_at = self._clock.utcnow()
        await self._run_repo.update(run)
        return run

    # ── Actions ───────────────────────────────────────────────────────────

    async def propose_action(
        self,
        *,
        run_id: UUID,
        cycle: int,
        action_type: str,
        plugin: str | None = None,
        title: str = "",
        description: str = "",
        justification: str = "",
        plugin_config: dict[str, object] | None = None,
        target_ids: list[UUID] | None = None,
        category: ActionCategory = ActionCategory.CATEGORY_0,
    ) -> AutonomousRunAction:
        """Record a proposed action from the AI planner."""
        await self.get(run_id)  # validates run exists
        action = AutonomousRunAction(
            id=self._id(),
            run_id=run_id,
            project_id=(await self._run_repo.get(run_id)).project_id,  # type: ignore[union-attr]
            cycle=cycle,
            action_type=action_type,
            plugin=plugin,
            title=title,
            description=description,
            justification=justification,
            plugin_config=plugin_config or {},
            target_ids=target_ids or [],
            category=category,
            status="proposed",
            created_at=self._clock.utcnow(),
        )
        await self._action_repo.create(action)
        return action

    async def approve_action(
        self, action_id: UUID, approved_by: UUID
    ) -> AutonomousRunAction:
        """Approve a proposed action (an explicit, manual human decision)."""
        action = await self._get_action(action_id)
        if action.status != "proposed":
            raise AutonomousActionNotApprovableError(action_id, action.status)
        action.status = "approved"
        action.approved_by = approved_by
        action.approved_at = self._clock.utcnow()
        # A human clicked approve -> this is NOT a policy-granted approval.
        action.approval_mode = "manual"
        await self._action_repo.update(action)
        return action

    async def reject_action(
        self, action_id: UUID, reason: str
    ) -> AutonomousRunAction:
        """Reject a proposed action."""
        action = await self._get_action(action_id)
        if action.status != "proposed":
            raise AutonomousActionNotApprovableError(action_id, action.status)
        action.status = "rejected"
        action.rejection_reason = reason
        await self._action_repo.update(action)
        return action

    async def record_action_execution(
        self,
        action_id: UUID,
        scan_id: UUID,
    ) -> AutonomousRunAction:
        """Record that an action was dispatched as a scan."""
        action = await self._get_action(action_id)
        action.scan_id = scan_id
        action.status = "executed"
        await self._action_repo.update(action)

        # Increment the run's completed counter
        run = await self.get(action.run_id)
        run.actions_completed += 1
        await self._run_repo.update(run)
        return action

    # ── Phase 2 additive lifecycle granularity ─────────────────────────────
    # Narrow, intent-revealing transitions used by the bounded cycle. Each
    # mirrors Phase 1's approve/reject granularity for the new statuses.

    async def attach_planned_action(
        self, action_id: UUID, planned_action_id: UUID
    ) -> AutonomousRunAction:
        """Link an autonomous action to the M7.2 PlannedAction that routes it."""
        action = await self._get_action(action_id)
        action.planned_action_id = planned_action_id
        await self._action_repo.update(action)
        return action

    async def mark_action_blocked(
        self, action_id: UUID, *, reason: str
    ) -> AutonomousRunAction:
        """Mark a proposed action blocked (rejected by validator or category 0)."""
        action = await self._get_action(action_id)
        if action.status != "proposed":
            raise AutonomousActionNotApprovableError(action_id, action.status)
        action.status = "blocked"
        action.rejection_reason = reason
        await self._action_repo.update(action)
        return action

    async def mark_action_duplicate(self, action_id: UUID) -> AutonomousRunAction:
        """Mark a proposed action as a within-run repetition (deduped)."""
        action = await self._get_action(action_id)
        if action.status == "proposed":
            action.status = "duplicate"
            await self._action_repo.update(action)
        return action

    async def mark_action_auto_approved(
        self, action_id: UUID, *, approved_by: UUID
    ) -> AutonomousRunAction:
        """Grant policy-based approval (AUTO_POLICY) attributed to `approved_by`.

        This is NOT a human click — `approval_mode` is recorded as
        auto_policy so the audit trail can never be misread as a manual
        approval. `approved_by` carries the responsible initiator for
        attribution only.
        """
        action = await self._get_action(action_id)
        if action.status != "proposed":
            raise AutonomousActionNotApprovableError(action_id, action.status)
        action.status = "approved"
        action.approval_mode = "auto_policy"
        action.approved_by = approved_by
        action.approved_at = self._clock.utcnow()
        await self._action_repo.update(action)
        return action

    async def mark_action_failed(
        self, action_id: UUID, *, reason: str
    ) -> AutonomousRunAction:
        """Record that execution failed (e.g., execution-time re-validation)."""
        action = await self._get_action(action_id)
        action.status = "failed"
        action.rejection_reason = reason
        await self._action_repo.update(action)
        return action

    async def list_actions(
        self, run_id: UUID, status: str | None = None
    ) -> list[AutonomousRunAction]:
        return await self._action_repo.list_for_run(run_id, status=status)

    # ── Internals ─────────────────────────────────────────────────────────

    async def _transition(self, run: AutonomousRun, new_status: AutonomousRunStatus) -> None:
        if not run.can_transition_to(new_status):
            raise AutonomousRunInvalidTransitionError(run.status.value, new_status.value)
        run.status = new_status
        await self._run_repo.update(run)

    def _check_budget(self, run: AutonomousRun) -> None:
        """Validate budget constraints before allowing re-plan."""
        if run.actions_completed >= run.max_actions:
            raise AutonomousRunBudgetExceededError(run.id, "max_actions")
        if run.started_at is not None:
            elapsed = (self._clock.utcnow() - run.started_at).total_seconds()
            if elapsed >= run.max_runtime_seconds:
                raise AutonomousRunBudgetExceededError(run.id, "max_runtime")

    async def _get_action(self, action_id: UUID) -> AutonomousRunAction:
        action = await self._action_repo.get(action_id)
        if action is None:
            from app.domain.exceptions import PlannedActionNotFoundError
            raise PlannedActionNotFoundError(action_id)
        return action


class _SystemClock:
    def utcnow(self) -> datetime:
        return datetime.now(UTC)
