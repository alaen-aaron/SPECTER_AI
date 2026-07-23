"""
Workflow endpoints (Phase 2/3).

Every route delegates to WorkflowService — no router here ever imports
PluginManager, Celery, or ExecutionEngine directly.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.api.v1.deps import (
    get_current_user,
    get_workflow_service,
    require_project_role,
)
from app.api.v1.schemas.workflows import (
    CreateWorkflowRequest,
    CreateWorkflowStepRequest,
    UpdateWorkflowRequest,
    UpdateWorkflowStepRequest,
    WorkflowExecutionListResponse,
    WorkflowExecutionResponse,
    WorkflowListResponse,
    WorkflowResponse,
    WorkflowStepListResponse,
    WorkflowStepResponse,
)
from app.application.workflow_service import WorkflowService
from app.domain.entities import (
    ProjectMember,
    User,
    Workflow,
    WorkflowExecution,
    WorkflowStep,
)

router = APIRouter(tags=["workflows"])


# --- Workflow CRUD -----------------------------------------------------------

@router.post(
    "/projects/{project_id}/workflows",
    response_model=WorkflowResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a workflow (any project member)",
)
async def create_workflow(
    project_id: UUID,
    body: CreateWorkflowRequest,
    current_user: User = Depends(get_current_user),
    _member: ProjectMember = Depends(require_project_role()),
    service: WorkflowService = Depends(get_workflow_service),
) -> Workflow:
    return await service.create(
        project_id=project_id,
        name=body.name,
        description=body.description,
        created_by=current_user.id,
    )


@router.get(
    "/projects/{project_id}/workflows",
    response_model=WorkflowListResponse,
    summary="List workflows for a project",
)
async def list_workflows(
    project_id: UUID,
    _member: ProjectMember = Depends(require_project_role()),
    service: WorkflowService = Depends(get_workflow_service),
) -> WorkflowListResponse:
    workflows = await service.list_for_project(project_id)
    return WorkflowListResponse(items=workflows)


@router.get(
    "/workflows/{workflow_id}",
    response_model=WorkflowResponse,
    summary="Get a workflow by id",
)
async def get_workflow(
    workflow_id: UUID,
    _member: ProjectMember = Depends(require_project_role()),
    service: WorkflowService = Depends(get_workflow_service),
) -> Workflow:
    return await service.get(workflow_id)


@router.patch(
    "/workflows/{workflow_id}",
    response_model=WorkflowResponse,
    summary="Update workflow name/description",
)
async def update_workflow(
    workflow_id: UUID,
    body: UpdateWorkflowRequest,
    _member: ProjectMember = Depends(require_project_role()),
    service: WorkflowService = Depends(get_workflow_service),
) -> Workflow:
    return await service.update(
        workflow_id,
        name=body.name,
        description=body.description,
    )


@router.delete(
    "/workflows/{workflow_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Delete a workflow and its steps",
)
async def delete_workflow(
    workflow_id: UUID,
    _member: ProjectMember = Depends(require_project_role()),
    service: WorkflowService = Depends(get_workflow_service),
) -> None:
    await service.delete(workflow_id)


@router.post(
    "/workflows/{workflow_id}/activate",
    response_model=WorkflowResponse,
    summary="Validate DAG and activate a workflow",
)
async def activate_workflow(
    workflow_id: UUID,
    _member: ProjectMember = Depends(require_project_role()),
    service: WorkflowService = Depends(get_workflow_service),
) -> Workflow:
    return await service.activate(workflow_id)


@router.post(
    "/workflows/{workflow_id}/archive",
    response_model=WorkflowResponse,
    summary="Archive a workflow",
)
async def archive_workflow(
    workflow_id: UUID,
    _member: ProjectMember = Depends(require_project_role()),
    service: WorkflowService = Depends(get_workflow_service),
) -> Workflow:
    return await service.archive(workflow_id)


# --- Workflow Steps ----------------------------------------------------------

@router.post(
    "/workflows/{workflow_id}/steps",
    response_model=WorkflowStepResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a step to a workflow",
)
async def add_step(
    workflow_id: UUID,
    body: CreateWorkflowStepRequest,
    _member: ProjectMember = Depends(require_project_role()),
    service: WorkflowService = Depends(get_workflow_service),
) -> WorkflowStep:
    return await service.add_step(
        workflow_id=workflow_id,
        plugin=body.plugin,
        name=body.name,
        plugin_config=body.plugin_config,
        depends_on=body.depends_on,
        condition=body.condition,
        timeout_seconds=body.timeout_seconds,
        max_retries=body.max_retries,
        order=body.order,
    )


@router.get(
    "/workflows/{workflow_id}/steps",
    response_model=WorkflowStepListResponse,
    summary="List steps for a workflow",
)
async def list_steps(
    workflow_id: UUID,
    _member: ProjectMember = Depends(require_project_role()),
    service: WorkflowService = Depends(get_workflow_service),
) -> WorkflowStepListResponse:
    steps = await service.list_steps(workflow_id)
    return WorkflowStepListResponse(items=steps)


@router.patch(
    "/workflows/steps/{step_id}",
    response_model=WorkflowStepResponse,
    summary="Update a workflow step",
)
async def update_step(
    step_id: UUID,
    body: UpdateWorkflowStepRequest,
    _member: ProjectMember = Depends(require_project_role()),
    service: WorkflowService = Depends(get_workflow_service),
) -> WorkflowStep:
    return await service.update_step(
        step_id,
        plugin=body.plugin,
        name=body.name,
        plugin_config=body.plugin_config,
        depends_on=body.depends_on,
        condition=body.condition,
        timeout_seconds=body.timeout_seconds,
        max_retries=body.max_retries,
        order=body.order,
    )


@router.delete(
    "/workflows/steps/{step_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Delete a workflow step",
)
async def delete_step(
    step_id: UUID,
    _member: ProjectMember = Depends(require_project_role()),
    service: WorkflowService = Depends(get_workflow_service),
) -> None:
    await service.delete_step(step_id)


# --- Workflow Execution ------------------------------------------------------

@router.post(
    "/workflows/{workflow_id}/execute",
    response_model=WorkflowExecutionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Execute a workflow (must be active, DAG validated)",
)
async def execute_workflow(
    workflow_id: UUID,
    current_user: User = Depends(get_current_user),
    _member: ProjectMember = Depends(require_project_role()),
    service: WorkflowService = Depends(get_workflow_service),
) -> WorkflowExecution:
    return await service.execute(workflow_id, current_user.id)


@router.get(
    "/workflows/{workflow_id}/executions",
    response_model=WorkflowExecutionListResponse,
    summary="List executions for a workflow",
)
async def list_executions(
    workflow_id: UUID,
    _member: ProjectMember = Depends(require_project_role()),
    service: WorkflowService = Depends(get_workflow_service),
) -> WorkflowExecutionListResponse:
    executions = await service.list_executions(workflow_id)
    return WorkflowExecutionListResponse(items=executions)


@router.get(
    "/workflow-executions/{execution_id}",
    response_model=WorkflowExecutionResponse,
    summary="Get a workflow execution by id",
)
async def get_execution(
    execution_id: UUID,
    _member: ProjectMember = Depends(require_project_role()),
    service: WorkflowService = Depends(get_workflow_service),
) -> WorkflowExecution:
    return await service.get_execution(execution_id)


@router.delete(
    "/workflow-executions/{execution_id}",
    response_model=WorkflowExecutionResponse,
    summary="Cancel a running/queued workflow execution",
)
async def cancel_execution(
    execution_id: UUID,
    _member: ProjectMember = Depends(require_project_role()),
    service: WorkflowService = Depends(get_workflow_service),
) -> WorkflowExecution:
    return await service.cancel_execution(execution_id)
