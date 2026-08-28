"""
Autonomous Orchestration endpoints (M7.4 Phase 1).

CRUD + state machine for AI-driven scan runs. No router here ever
invokes the LLM directly — the service layer owns all state transitions.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.v1.deps import (
    get_autonomous_orchestrator,
    get_autonomous_service,
    get_current_user,
    get_planner_service,
    require_project_role,
)
from app.api.v1.schemas.autonomous import (
    AutonomousCycleResponse,
    AutonomousRunActionResponse,
    AutonomousRunResponse,
    CreateAutonomousRunRequest,
    PaginatedAutonomousRunResponse,
)
from app.application.autonomous_orchestrator import AutonomousOrchestrator
from app.application.autonomous_service import AutonomousService
from app.application.planner_service import PlannerService
from app.domain.entities import OrganizationMember, ProjectMember, User
from app.domain.value_objects import ProjectRole

router = APIRouter(tags=["autonomous"])


@router.post(
    "/projects/{project_id}/autonomous-runs",
    response_model=AutonomousRunResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new autonomous scan run (Owner/Admin only)",
)
async def create_autonomous_run(
    project_id: UUID,
    body: CreateAutonomousRunRequest,
    current_user: User = Depends(get_current_user),
    _member: ProjectMember | OrganizationMember = Depends(
        require_project_role(ProjectRole.OWNER, ProjectRole.ADMIN)
    ),
    service: AutonomousService = Depends(get_autonomous_service),
) -> AutonomousRunResponse:
    run = await service.create(
        project_id=project_id,
        initiated_by=current_user.id,
        objective=body.objective,
        max_actions=body.max_actions,
        max_runtime_seconds=body.max_runtime_seconds,
    )
    return AutonomousRunResponse.model_validate(run)


@router.get(
    "/projects/{project_id}/autonomous-runs",
    response_model=PaginatedAutonomousRunResponse,
    summary="List autonomous runs for a project (any member)",
)
async def list_autonomous_runs(
    project_id: UUID,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: datetime | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    _member: ProjectMember | OrganizationMember = Depends(require_project_role()),
    service: AutonomousService = Depends(get_autonomous_service),
) -> PaginatedAutonomousRunResponse:
    from app.domain.value_objects import AutonomousRunStatus

    status_enum = AutonomousRunStatus(status_filter) if status_filter else None
    runs = await service.list_for_project(
        project_id, status=status_enum, limit=limit, cursor=cursor
    )
    has_more = len(runs) > limit
    items = runs[:limit]
    next_cursor = items[-1].created_at if has_more and items else None
    return PaginatedAutonomousRunResponse(items=items, next_cursor=next_cursor)


@router.get(
    "/autonomous-runs/{run_id}",
    response_model=AutonomousRunResponse,
    summary="Get a single autonomous run by id",
)
async def get_autonomous_run(
    run_id: UUID,
    _member: ProjectMember | OrganizationMember = Depends(require_project_role()),
    service: AutonomousService = Depends(get_autonomous_service),
) -> AutonomousRunResponse:
    run = await service.get(run_id)
    return AutonomousRunResponse.model_validate(run)


@router.post(
    "/autonomous-runs/{run_id}/cancel",
    response_model=AutonomousRunResponse,
    summary="Cancel a running autonomous session",
)
async def cancel_autonomous_run(
    run_id: UUID,
    _member: ProjectMember | OrganizationMember = Depends(
        require_project_role(ProjectRole.OWNER, ProjectRole.ADMIN)
    ),
    service: AutonomousService = Depends(get_autonomous_service),
) -> AutonomousRunResponse:
    run = await service.cancel(run_id)
    return AutonomousRunResponse.model_validate(run)


@router.post(
    "/autonomous-runs/{run_id}/start-planning",
    response_model=AutonomousRunResponse,
    summary="Transition CREATED → PLANNING",
)
async def start_planning(
    run_id: UUID,
    _member: ProjectMember | OrganizationMember = Depends(
        require_project_role(ProjectRole.OWNER, ProjectRole.ADMIN)
    ),
    service: AutonomousService = Depends(get_autonomous_service),
) -> AutonomousRunResponse:
    run = await service.start_planning(run_id)
    return AutonomousRunResponse.model_validate(run)


@router.post(
    "/autonomous-runs/{run_id}/plan-complete",
    response_model=AutonomousRunResponse,
    summary="Transition PLANNING → AWAITING_APPROVAL",
)
async def plan_complete(
    run_id: UUID,
    _member: ProjectMember | OrganizationMember = Depends(
        require_project_role(ProjectRole.OWNER, ProjectRole.ADMIN)
    ),
    service: AutonomousService = Depends(get_autonomous_service),
) -> AutonomousRunResponse:
    run = await service.plan_complete(run_id)
    return AutonomousRunResponse.model_validate(run)


@router.post(
    "/autonomous-runs/{run_id}/cycle",
    response_model=AutonomousCycleResponse,
    summary="Run one bounded planner→validator→execution cycle (M7.4 Phase 2/3)",
    description=(
        "The controlled loop driver. One call = at most one planning burst "
        "(capped by the run budget), decision classification, policy "
        "auto-approval and execution of category-2 actions, and a single "
        "state-machine advance. An OBSERVING run is first run through the "
        "Phase 3 observation gate: it re-plans only when genuinely new "
        "persisted facts exist (tool results / assets / findings / services / "
        "technologies / a scan reaching a terminal state), otherwise it "
        "completes with stopped_because=no_progress. Category-1 actions pause "
        "the run at AWAITING_APPROVAL for a human decision; nothing is ever "
        "executed sans category-2 policy approval or an explicit human "
        "approval. Execution always flows through the existing "
        "execute_approved() → ScanService.create() → Scope Guard → Celery "
        "→ M7.1 isolated executor."
    ),
)
async def run_cycle(
    run_id: UUID,
    _member: ProjectMember | OrganizationMember = Depends(
        require_project_role(ProjectRole.OWNER, ProjectRole.ADMIN)
    ),
    orchestrator: AutonomousOrchestrator = Depends(get_autonomous_orchestrator),
) -> AutonomousCycleResponse:
    outcome = await orchestrator.cycle(run_id)
    return AutonomousCycleResponse(
        run=AutonomousRunResponse.model_validate(outcome.run),
        summary=outcome.summary,
        executed_scan_ids=list(outcome.executed_scan_ids),
        stopped_because=outcome.stopped_because,
    )


@router.post(
    "/autonomous-runs/{run_id}/approve",
    response_model=AutonomousRunResponse,
    summary="Approve all proposed actions and transition to EXECUTING",
)
async def approve_run(
    run_id: UUID,
    current_user: User = Depends(get_current_user),
    _member: ProjectMember | OrganizationMember = Depends(
        require_project_role(ProjectRole.OWNER, ProjectRole.ADMIN)
    ),
    service: AutonomousService = Depends(get_autonomous_service),
    planner: PlannerService = Depends(get_planner_service),
) -> AutonomousRunResponse:
    actions = await service.list_actions(run_id, status="proposed")
    for action in actions:
        await service.approve_action(action.id, approved_by=current_user.id)
        if action.planned_action_id is not None:
            # Approval mode == MANUAL; the M7.2 PlannedAction must match so
            # execute_approved() can route the human-approved action later.
            await planner.approve(
                action.planned_action_id, approved_by=current_user.id
            )
    run = await service.approval_granted(run_id)
    return AutonomousRunResponse.model_validate(run)


@router.post(
    "/autonomous-runs/{run_id}/execution-complete",
    response_model=AutonomousRunResponse,
    summary="Transition EXECUTING → OBSERVING",
)
async def execution_complete(
    run_id: UUID,
    _member: ProjectMember | OrganizationMember = Depends(
        require_project_role(ProjectRole.OWNER, ProjectRole.ADMIN)
    ),
    service: AutonomousService = Depends(get_autonomous_service),
) -> AutonomousRunResponse:
    run = await service.execution_complete(run_id)
    return AutonomousRunResponse.model_validate(run)


@router.post(
    "/autonomous-runs/{run_id}/observation-complete",
    response_model=AutonomousRunResponse,
    summary="Transition OBSERVING → PLANNING (re-plan) or COMPLETED",
)
async def observation_complete_endpoint(
    run_id: UUID,
    should_continue: bool = Query(default=False),
    _member: ProjectMember | OrganizationMember = Depends(
        require_project_role(ProjectRole.OWNER, ProjectRole.ADMIN)
    ),
    service: AutonomousService = Depends(get_autonomous_service),
) -> AutonomousRunResponse:
    run = await service.observation_complete(run_id, should_continue=should_continue)
    return AutonomousRunResponse.model_validate(run)


@router.post(
    "/autonomous-runs/{run_id}/heartbeat",
    response_model=AutonomousRunResponse,
    summary="Update the heartbeat timestamp",
)
async def heartbeat(
    run_id: UUID,
    _member: ProjectMember | OrganizationMember = Depends(require_project_role()),
    service: AutonomousService = Depends(get_autonomous_service),
) -> AutonomousRunResponse:
    run = await service.heartbeat(run_id)
    return AutonomousRunResponse.model_validate(run)


@router.get(
    "/autonomous-runs/{run_id}/actions",
    response_model=list[AutonomousRunActionResponse],
    summary="List actions for an autonomous run",
)
async def list_actions(
    run_id: UUID,
    status_filter: str | None = Query(default=None, alias="status"),
    _member: ProjectMember | OrganizationMember = Depends(require_project_role()),
    service: AutonomousService = Depends(get_autonomous_service),
) -> list[AutonomousRunActionResponse]:
    actions = await service.list_actions(run_id, status=status_filter)
    return [AutonomousRunActionResponse.model_validate(a) for a in actions]


@router.post(
    "/autonomous-actions/{action_id}/approve",
    response_model=AutonomousRunActionResponse,
    summary="Approve a single proposed action",
)
async def approve_action_endpoint(
    action_id: UUID,
    current_user: User = Depends(get_current_user),
    _member: ProjectMember | OrganizationMember = Depends(
        require_project_role(ProjectRole.OWNER, ProjectRole.ADMIN)
    ),
    service: AutonomousService = Depends(get_autonomous_service),
    planner: PlannerService = Depends(get_planner_service),
) -> AutonomousRunActionResponse:
    action = await service.approve_action(action_id, approved_by=current_user.id)
    if action.planned_action_id is not None:
        await planner.approve(action.planned_action_id, approved_by=current_user.id)
    return AutonomousRunActionResponse.model_validate(action)


@router.post(
    "/autonomous-actions/{action_id}/reject",
    response_model=AutonomousRunActionResponse,
    summary="Reject a proposed action",
)
async def reject_action_endpoint(
    action_id: UUID,
    current_user: User = Depends(get_current_user),
    reason: str = "",
    _member: ProjectMember | OrganizationMember = Depends(
        require_project_role(ProjectRole.OWNER, ProjectRole.ADMIN)
    ),
    service: AutonomousService = Depends(get_autonomous_service),
    planner: PlannerService = Depends(get_planner_service),
) -> AutonomousRunActionResponse:
    full_reason = reason or "Rejected by human operator"
    action = await service.reject_action(action_id, full_reason)
    if action.planned_action_id is not None:
        await planner.reject(
            action.planned_action_id,
            rejected_by=current_user.id,
            reason=full_reason,
        )
    return AutonomousRunActionResponse.model_validate(action)
