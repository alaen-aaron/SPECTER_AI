"""
SQLAlchemy implementation of Workflow, WorkflowStep, WorkflowExecution
repositories (Phase 2/3).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession as SqlAsyncSession

from app.domain.entities import Schedule, Workflow, WorkflowExecution, WorkflowStep
from app.domain.value_objects import ScanStatus, ScheduleFrequency, WorkflowStatus, WorkflowStepType
from app.infrastructure.db.models.workflow import (
    ScheduleModel,
    WorkflowExecutionModel,
    WorkflowModel,
    WorkflowStepModel,
)


def _workflow_to_entity(row: WorkflowModel) -> Workflow:
    return Workflow(
        id=row.id,
        project_id=row.project_id,
        name=row.name,
        description=row.description,
        status=WorkflowStatus(row.status),
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _step_to_entity(row: WorkflowStepModel) -> WorkflowStep:
    dep_ids: list[UUID] = []
    raw_deps = row.depends_on or []
    for d in raw_deps:
        if isinstance(d, str):
            dep_ids.append(UUID(d))
        elif isinstance(d, dict) and "id" in d:
            dep_ids.append(UUID(d["id"]))
    return WorkflowStep(
        id=row.id,
        workflow_id=row.workflow_id,
        step_type=WorkflowStepType(row.step_type),
        plugin=row.plugin,
        name=row.name,
        plugin_config=dict(row.plugin_config or {}),
        depends_on=dep_ids,
        condition=dict(row.condition) if row.condition else None,
        timeout_seconds=row.timeout_seconds,
        max_retries=row.max_retries,
        order=row.order,
    )


def _execution_to_entity(row: WorkflowExecutionModel) -> WorkflowExecution:
    return WorkflowExecution(
        id=row.id,
        workflow_id=row.workflow_id,
        project_id=row.project_id,
        initiated_by=row.initiated_by,
        status=ScanStatus(row.status),
        step_results=dict(row.step_results) if row.step_results else {},  # type: ignore[arg-type]
        created_at=row.created_at,
        started_at=row.started_at,
        completed_at=row.completed_at,
        error_message=row.error_message,
    )


class SqlAlchemyWorkflowRepository:
    def __init__(self, session: SqlAsyncSession) -> None:
        self._session = session

    async def create(self, workflow: Workflow) -> None:
        model = WorkflowModel(
            id=workflow.id,
            project_id=workflow.project_id,
            name=workflow.name,
            description=workflow.description,
            status=workflow.status.value,
            created_by=workflow.created_by,
        )
        self._session.add(model)
        await self._session.flush()

    async def get(self, workflow_id: UUID) -> Workflow | None:
        row = await self._session.get(WorkflowModel, workflow_id)
        return _workflow_to_entity(row) if row else None

    async def list_for_project(self, project_id: UUID) -> list[Workflow]:
        stmt = (
            select(WorkflowModel)
            .where(WorkflowModel.project_id == project_id)
            .order_by(WorkflowModel.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return [_workflow_to_entity(row) for row in result.scalars().all()]

    async def update(self, workflow: Workflow) -> None:
        stmt = (
            update(WorkflowModel)
            .where(WorkflowModel.id == workflow.id)
            .values(
                name=workflow.name,
                description=workflow.description,
                status=workflow.status.value,
            )
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def delete(self, workflow_id: UUID) -> None:
        stmt = delete(WorkflowModel).where(WorkflowModel.id == workflow_id)
        await self._session.execute(stmt)
        await self._session.flush()


class SqlAlchemyWorkflowStepRepository:
    def __init__(self, session: SqlAsyncSession) -> None:
        self._session = session

    async def add(self, step: WorkflowStep) -> None:
        model = WorkflowStepModel(
            id=step.id,
            workflow_id=step.workflow_id,
            step_type=step.step_type,
            plugin=step.plugin,
            name=step.name,
            plugin_config=step.plugin_config,
            depends_on=[str(d) for d in step.depends_on],
            condition=step.condition,
            timeout_seconds=step.timeout_seconds,
            max_retries=step.max_retries,
            order=step.order,
        )
        self._session.add(model)
        await self._session.flush()

    async def get(self, step_id: UUID) -> WorkflowStep | None:
        row = await self._session.get(WorkflowStepModel, step_id)
        return _step_to_entity(row) if row else None

    async def list_for_workflow(self, workflow_id: UUID) -> list[WorkflowStep]:
        stmt = (
            select(WorkflowStepModel)
            .where(WorkflowStepModel.workflow_id == workflow_id)
            .order_by(WorkflowStepModel.order)
        )
        result = await self._session.execute(stmt)
        return [_step_to_entity(row) for row in result.scalars().all()]

    async def update(self, step: WorkflowStep) -> None:
        stmt = (
            update(WorkflowStepModel)
            .where(WorkflowStepModel.id == step.id)
            .values(
                plugin=step.plugin,
                name=step.name,
                plugin_config=step.plugin_config,
                depends_on=[str(d) for d in step.depends_on],
                condition=step.condition,
                timeout_seconds=step.timeout_seconds,
                max_retries=step.max_retries,
                order=step.order,
            )
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def delete(self, step_id: UUID) -> None:
        stmt = delete(WorkflowStepModel).where(WorkflowStepModel.id == step_id)
        await self._session.execute(stmt)
        await self._session.flush()

    async def delete_for_workflow(self, workflow_id: UUID) -> None:
        stmt = delete(WorkflowStepModel).where(
            WorkflowStepModel.workflow_id == workflow_id
        )
        await self._session.execute(stmt)
        await self._session.flush()


class SqlAlchemyWorkflowExecutionRepository:
    def __init__(self, session: SqlAsyncSession) -> None:
        self._session = session

    async def create(self, execution: WorkflowExecution) -> None:
        model = WorkflowExecutionModel(
            id=execution.id,
            workflow_id=execution.workflow_id,
            project_id=execution.project_id,
            initiated_by=execution.initiated_by,
            status=execution.status.value,
            step_results=execution.step_results,
        )
        self._session.add(model)
        await self._session.flush()

    async def get(self, execution_id: UUID) -> WorkflowExecution | None:
        row = await self._session.get(WorkflowExecutionModel, execution_id)
        return _execution_to_entity(row) if row else None

    async def list_for_workflow(self, workflow_id: UUID) -> list[WorkflowExecution]:
        stmt = (
            select(WorkflowExecutionModel)
            .where(WorkflowExecutionModel.workflow_id == workflow_id)
            .order_by(WorkflowExecutionModel.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return [_execution_to_entity(row) for row in result.scalars().all()]

    async def list_for_project(self, project_id: UUID) -> list[WorkflowExecution]:
        stmt = (
            select(WorkflowExecutionModel)
            .where(WorkflowExecutionModel.project_id == project_id)
            .order_by(WorkflowExecutionModel.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return [_execution_to_entity(row) for row in result.scalars().all()]

    async def update_status(self, execution_id: UUID, status: ScanStatus) -> None:
        from datetime import UTC, datetime

        values: dict[str, object] = {"status": status.value}
        if status is ScanStatus.RUNNING:
            values["started_at"] = datetime.now(UTC)
        elif status in (ScanStatus.COMPLETED, ScanStatus.FAILED, ScanStatus.CANCELLED):
            values["completed_at"] = datetime.now(UTC)
        stmt = update(WorkflowExecutionModel).where(
            WorkflowExecutionModel.id == execution_id
        ).values(**values)
        await self._session.execute(stmt)
        await self._session.flush()

    async def set_step_result(
        self,
        execution_id: UUID,
        step_id: str,
        result: dict[str, object],
    ) -> None:
        row = await self._session.get(WorkflowExecutionModel, execution_id)
        if row is not None:
            results = dict(row.step_results or {})
            results[step_id] = result
            stmt = update(WorkflowExecutionModel).where(
                WorkflowExecutionModel.id == execution_id
            ).values(step_results=results)
            await self._session.execute(stmt)
            await self._session.flush()


class SqlAlchemyScheduleRepository:
    def __init__(self, session: SqlAsyncSession) -> None:
        self._session = session

    async def create(self, schedule: Schedule) -> None:

        model = ScheduleModel(
            id=schedule.id,
            workflow_id=schedule.workflow_id,
            project_id=schedule.project_id,
            frequency=schedule.frequency.value,
            cron_expression=schedule.cron_expression,
            is_active=schedule.is_active,
            last_run_at=schedule.last_run_at,
            next_run_at=schedule.next_run_at,
            created_by=schedule.created_by,
        )
        self._session.add(model)
        await self._session.flush()

    async def get(self, schedule_id: UUID) -> Schedule | None:
        row = await self._session.get(ScheduleModel, schedule_id)
        if row is None:
            return None
        return Schedule(
            id=row.id,
            workflow_id=row.workflow_id,
            project_id=row.project_id,
            frequency=ScheduleFrequency(row.frequency),
            cron_expression=row.cron_expression,
            is_active=row.is_active,
            last_run_at=row.last_run_at,
            next_run_at=row.next_run_at,
            created_by=row.created_by,
            created_at=row.created_at,
        )

    async def list_for_project(self, project_id: UUID) -> list[Schedule]:
        stmt = (
            select(ScheduleModel)
            .where(ScheduleModel.project_id == project_id)
            .order_by(ScheduleModel.created_at.desc())
        )
        result = await self._session.execute(stmt)
        schedules: list[Schedule] = []
        for row in result.scalars().all():
            schedules.append(Schedule(
                id=row.id,
                workflow_id=row.workflow_id,
                project_id=row.project_id,
                frequency=ScheduleFrequency(row.frequency),
                cron_expression=row.cron_expression,
                is_active=row.is_active,
                last_run_at=row.last_run_at,
                next_run_at=row.next_run_at,
                created_by=row.created_by,
                created_at=row.created_at,
            ))
        return schedules

    async def list_active(self) -> list[Schedule]:
        stmt = (
            select(ScheduleModel)
            .where(ScheduleModel.is_active == True)  # noqa: E712
            .order_by(ScheduleModel.next_run_at)
        )
        result = await self._session.execute(stmt)
        schedules: list[Schedule] = []
        for row in result.scalars().all():
            schedules.append(Schedule(
                id=row.id,
                workflow_id=row.workflow_id,
                project_id=row.project_id,
                frequency=ScheduleFrequency(row.frequency),
                cron_expression=row.cron_expression,
                is_active=row.is_active,
                last_run_at=row.last_run_at,
                next_run_at=row.next_run_at,
                created_by=row.created_by,
                created_at=row.created_at,
            ))
        return schedules

    async def list_due(self, now: datetime) -> list[Schedule]:
        stmt = (
            select(ScheduleModel)
            .where(ScheduleModel.is_active == True)  # noqa: E712
            .where(ScheduleModel.next_run_at <= now)
            .order_by(ScheduleModel.next_run_at)
        )
        result = await self._session.execute(stmt)
        schedules: list[Schedule] = []
        for row in result.scalars().all():
            schedules.append(Schedule(
                id=row.id,
                workflow_id=row.workflow_id,
                project_id=row.project_id,
                frequency=ScheduleFrequency(row.frequency),
                cron_expression=row.cron_expression,
                is_active=row.is_active,
                last_run_at=row.last_run_at,
                next_run_at=row.next_run_at,
                created_by=row.created_by,
                created_at=row.created_at,
            ))
        return schedules

    async def update(self, schedule: Schedule) -> None:
        stmt = (
            update(ScheduleModel)
            .where(ScheduleModel.id == schedule.id)
            .values(
                frequency=schedule.frequency.value,
                cron_expression=schedule.cron_expression,
                is_active=schedule.is_active,
                last_run_at=schedule.last_run_at,
                next_run_at=schedule.next_run_at,
            )
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def delete(self, schedule_id: UUID) -> None:
        stmt = delete(ScheduleModel).where(ScheduleModel.id == schedule_id)
        await self._session.execute(stmt)
        await self._session.flush()
