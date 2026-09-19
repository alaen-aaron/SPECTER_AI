"""
Celery tasks.

`ping` verifies broker wiring (Milestone 1). `execute_scan_task`
(Milestone 3) is the actual background scan runner: it's a plain sync
Celery task (Celery's worker pool is sync) that bridges into the async
`ExecutionEngine` via `asyncio.run`, opening its own DB session scoped
to just this task — Celery tasks run outside any FastAPI request, so
there is no request-scoped session to reuse here.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import structlog

from app.infrastructure.celery_app.app import celery_app

logger = structlog.get_logger(__name__)


@celery_app.task(name="specter.ping")
def ping() -> str:
    """Trivial task used to smoke-test the Celery worker in Milestone 1."""
    return "pong"


@celery_app.task(name="specter.execute_scan")
def execute_scan_task(scan_id: str) -> None:
    """Entry point Celery invokes; `scan_id` arrives as a string because
    Celery messages are JSON-serialized and UUID isn't JSON-native."""
    asyncio.run(_execute_scan(UUID(scan_id)))


async def _execute_scan(scan_id: UUID) -> None:
    # Local imports: this module must be importable by the Celery app
    # (and therefore by `-A app.infrastructure.celery_app.app`) without
    # pulling in the full FastAPI/DB stack at *module* import time —
    # only when a task actually runs.
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.plugins.builtin  # noqa: F401 - side-effect import, registers built-in plugins
    import app.plugins.normalizers  # noqa: F401 - side-effect import, registers normalizers
    from app.application.asset_service import AssetService
    from app.application.correlation_service import CorrelationService
    from app.application.graph_service import GraphService
    from app.application.scope_guard_service import ScopeGuardService
    from app.core.config import get_settings
    from app.infrastructure.db.repositories.asset_observation_repository import (
        SqlAlchemyAssetObservationRepository,
    )
    from app.infrastructure.db.repositories.asset_repository import (
        SqlAlchemyAssetRepository,
    )
    from app.infrastructure.db.repositories.audit_log_repository import (
        SqlAlchemyAuditLogRepository,
    )
    from app.infrastructure.db.repositories.authorization_repository import (
        SqlAlchemyAuthorizationRecordRepository,
    )
    from app.infrastructure.db.repositories.finding_repository import (
        SqlAlchemyFindingRepository,
    )
    from app.infrastructure.db.repositories.graph_repository import (
        SqlAlchemyGraphRepository,
    )
    from app.infrastructure.db.repositories.project_repository import SqlAlchemyProjectRepository
    from app.infrastructure.db.repositories.scan_repository import SqlAlchemyScanRepository
    from app.infrastructure.db.repositories.target_repository import SqlAlchemyTargetRepository
    from app.infrastructure.db.repositories.tool_result_repository import (
        SqlAlchemyToolResultRepository,
    )
    from app.infrastructure.execution.engine import ExecutionEngine
    from app.infrastructure.execution.executor_runner import ExecutorHttpRunner
    from app.infrastructure.storage.local_artifact_store import LocalArtifactStore
    from app.plugins.base import CommandRunner
    from app.plugins.manager import PluginManager
    from app.plugins.normalizer_registry import normalizer_registry
    from app.plugins.registry import registry

    settings = get_settings()

    runner: CommandRunner | None = None
    if settings.EXECUTOR_ENABLED:
        runner = ExecutorHttpRunner(
            base_url=settings.EXECUTOR_URL,
            image=settings.EXECUTOR_IMAGE,
            cpu_limit=settings.EXECUTOR_CPU_LIMIT,
            memory_limit=settings.EXECUTOR_MEMORY_LIMIT,
        )

    # Deliberately NOT the process-wide cached `get_engine()`/
    # `get_session_factory()` singletons from `infrastructure/db/session.py`.
    # Those are correct for FastAPI, which has exactly one long-lived
    # event loop for the whole process — but each Celery task here runs
    # inside its OWN fresh loop via `asyncio.run()` (see `execute_scan_task`
    # above), and asyncpg connections are bound to the loop that created
    # them. Sharing the cached engine across tasks would bind its pool to
    # the first task's loop and then break on the second task's loop with
    # "Future attached to a different loop." A per-task engine, disposed
    # at the end of this function, is what makes that impossible.
    engine = create_async_engine(str(settings.DATABASE_URL))
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    try:
        async with session_factory() as session:
            graph_repo = SqlAlchemyGraphRepository(session)
            graph_service = GraphService(graph_repo)
            target_repo_for_policy = SqlAlchemyTargetRepository(session)

            async def _registered_target_values(
                target_ids: list[UUID],
            ) -> list[str]:
                """M7.3 Phase 2: registered identities for executor policy."""
                values: list[str] = []
                for tid in target_ids:
                    t = await target_repo_for_policy.get_by_id(tid)
                    if t is not None:
                        values.append(t.value)
                return values

            asset_service = AssetService(
                asset_repository=SqlAlchemyAssetRepository(session),
                graph_service=graph_service,
                observation_repository=SqlAlchemyAssetObservationRepository(session),
            )
            execution_engine = ExecutionEngine(
                scan_repository=SqlAlchemyScanRepository(session),
                scope_guard=ScopeGuardService(
                    project_repository=SqlAlchemyProjectRepository(session),
                    target_repository=target_repo_for_policy,
                    authorization_repository=SqlAlchemyAuthorizationRecordRepository(session),
                ),
                plugin_manager=PluginManager(registry),
                artifact_store=LocalArtifactStore(settings.SCAN_ARTIFACTS_DIR),
                audit_log_repository=SqlAlchemyAuditLogRepository(session),
                tool_result_repository=SqlAlchemyToolResultRepository(session),
                normalizer_registry=normalizer_registry,
                default_timeout_seconds=settings.SCAN_DEFAULT_TIMEOUT_SECONDS,
                correlation_service=CorrelationService(
                    finding_repository=SqlAlchemyFindingRepository(session),
                    asset_repository=SqlAlchemyAssetRepository(session),
                    observation_repository=SqlAlchemyAssetObservationRepository(session),
                    graph_service=graph_service,
                ),
                asset_service=asset_service,
                graph_service=graph_service,
                runner=runner,
                registered_target_values=_registered_target_values,
            )
            await execution_engine.run(scan_id)
            await session.commit()
    finally:
        await engine.dispose()


@celery_app.task(name="specter.execute_workflow")
def execute_workflow_task(execution_id: str) -> None:
    """Entry point for workflow execution — same sync-bridge pattern as scan tasks."""
    asyncio.run(_execute_workflow(UUID(execution_id)))


@celery_app.task(name="specter.tick_schedules")
def tick_schedules_task() -> None:
    """Periodic task invoked by Celery Beat.

    Polls for active schedules whose ``next_run_at <= now()``,
    creates a WorkflowExecution for each, dispatches execution,
    and advances (or deactivates) the schedule.
    """
    asyncio.run(_tick_schedules())


async def _execute_workflow(execution_id: UUID) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.plugins.builtin  # noqa: F401
    import app.plugins.normalizers  # noqa: F401
    from app.application.asset_service import AssetService
    from app.application.correlation_service import CorrelationService
    from app.application.graph_service import GraphService
    from app.application.scan_service import NullScanTaskDispatcher, ScanService
    from app.application.scope_guard_service import ScopeGuardService
    from app.application.workflow_executor import WorkflowExecutor
    from app.core.config import get_settings
    from app.infrastructure.db.repositories.asset_observation_repository import (
        SqlAlchemyAssetObservationRepository,
    )
    from app.infrastructure.db.repositories.asset_repository import (
        SqlAlchemyAssetRepository,
    )
    from app.infrastructure.db.repositories.audit_log_repository import (
        SqlAlchemyAuditLogRepository,
    )
    from app.infrastructure.db.repositories.authorization_repository import (
        SqlAlchemyAuthorizationRecordRepository,
    )
    from app.infrastructure.db.repositories.finding_repository import (
        SqlAlchemyFindingRepository,
    )
    from app.infrastructure.db.repositories.graph_repository import (
        SqlAlchemyGraphRepository,
    )
    from app.infrastructure.db.repositories.project_repository import (
        SqlAlchemyProjectRepository,
    )
    from app.infrastructure.db.repositories.scan_repository import (
        SqlAlchemyScanRepository,
    )
    from app.infrastructure.db.repositories.target_repository import (
        SqlAlchemyTargetRepository,
    )
    from app.infrastructure.db.repositories.tool_result_repository import (
        SqlAlchemyToolResultRepository,
    )
    from app.infrastructure.db.repositories.workflow_repository import (
        SqlAlchemyWorkflowExecutionRepository,
        SqlAlchemyWorkflowStepRepository,
    )
    from app.infrastructure.execution.engine import ExecutionEngine
    from app.infrastructure.storage.local_artifact_store import LocalArtifactStore
    from app.plugins.base import CommandRunner
    from app.plugins.manager import PluginManager
    from app.plugins.normalizer_registry import normalizer_registry
    from app.plugins.registry import registry

    settings = get_settings()
    engine = create_async_engine(str(settings.DATABASE_URL))
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    runner: CommandRunner | None = None
    if settings.EXECUTOR_ENABLED:
        from app.infrastructure.execution.executor_runner import ExecutorHttpRunner

        runner = ExecutorHttpRunner(
            base_url=settings.EXECUTOR_URL,
            image=settings.EXECUTOR_IMAGE,
            cpu_limit=settings.EXECUTOR_CPU_LIMIT,
            memory_limit=settings.EXECUTOR_MEMORY_LIMIT,
        )

    try:
        async with session_factory() as session:
            graph_repo = SqlAlchemyGraphRepository(session)
            graph_service = GraphService(graph_repo)
            asset_service = AssetService(
                asset_repository=SqlAlchemyAssetRepository(session),
                graph_service=graph_service,
                observation_repository=SqlAlchemyAssetObservationRepository(session),
            )
            target_repo = SqlAlchemyTargetRepository(session)
            scope_guard = ScopeGuardService(
                project_repository=SqlAlchemyProjectRepository(session),
                target_repository=target_repo,
                authorization_repository=SqlAlchemyAuthorizationRecordRepository(session),
            )
            audit_repo = SqlAlchemyAuditLogRepository(session)
            scan_repo = SqlAlchemyScanRepository(session)
            plugin_manager = PluginManager(registry, runner=runner)

            async def _registered_target_values(
                target_ids: list[UUID],
            ) -> list[str]:
                """M7.3 Phase 2: registered identities for executor policy."""
                values: list[str] = []
                for tid in target_ids:
                    t = await target_repo.get_by_id(tid)
                    if t is not None:
                        values.append(t.value)
                return values

            # M7.5 Phase 1: workflow steps go through the SAME canonical
            # path as ordinary scans — ScanService.create (Scope Guard +
            # plugin-config validation) then the ExecutionEngine (scope
            # revalidation, M7.1 isolation, ToolResult/correlation/assets/
            # audit). NullScanTaskDispatcher: the engine runs the scan
            # IN-PROCESS below, so the create must NOT double-dispatch to
            # Celery, or a worker would race the same scan.
            scan_service = ScanService(
                scan_repository=scan_repo,
                scope_guard=scope_guard,
                plugin_manager=plugin_manager,
                task_dispatcher=NullScanTaskDispatcher(),
            )
            execution_engine = ExecutionEngine(
                scan_repository=scan_repo,
                scope_guard=scope_guard,
                plugin_manager=plugin_manager,
                artifact_store=LocalArtifactStore(settings.SCAN_ARTIFACTS_DIR),
                audit_log_repository=audit_repo,
                tool_result_repository=SqlAlchemyToolResultRepository(session),
                normalizer_registry=normalizer_registry,
                default_timeout_seconds=settings.SCAN_DEFAULT_TIMEOUT_SECONDS,
                correlation_service=CorrelationService(
                    finding_repository=SqlAlchemyFindingRepository(session),
                    asset_repository=SqlAlchemyAssetRepository(session),
                    observation_repository=SqlAlchemyAssetObservationRepository(session),
                    graph_service=graph_service,
                ),
                asset_service=asset_service,
                graph_service=graph_service,
                runner=runner,
                registered_target_values=_registered_target_values,
            )
            workflow_executor = WorkflowExecutor(
                scan_service=scan_service,
                execution_engine=execution_engine,
                target_repository=target_repo,
                execution_repository=SqlAlchemyWorkflowExecutionRepository(session),
                step_repository=SqlAlchemyWorkflowStepRepository(session),
                audit_log_repository=audit_repo,
            )
            try:
                await workflow_executor.execute(execution_id)
                await session.commit()
            except Exception:
                await session.rollback()
                raise
    finally:
        await engine.dispose()


async def _tick_schedules() -> None:
    """Poll due schedules and dispatch workflow executions.

    M7.5 Phase 1 — durable fire-lock:
      * `claim_due` takes `SELECT ... FOR UPDATE SKIP LOCKED` on the due
        rows, so two beat workers can never double-fire one schedule
        while the claim transaction is open.
      * The claim lives and dies with the transaction: commit advances
        the schedule (next_run_at slid forward / ONCE deactivated), a
        rollback leaves it due so it is picked up on the next tick
        (at-least-once, never wedged).
      * An archived/deleted workflow's schedule is PERMANENTLY disabled
        instead of re-firing every 30s.
    """
    from datetime import UTC, datetime
    from uuid import uuid4

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.application.autonomous_service import AutonomousService
    from app.application.campaign_scheduler_service import CampaignSchedulerService
    from app.application.outbox_service import OutboxService
    from app.application.schedule_service import ScheduleService
    from app.application.scope_guard_service import ScopeGuardService
    from app.application.workflow_service import WorkflowService
    from app.core.config import get_settings
    from app.domain.entities import AuditLogEntry
    from app.domain.exceptions import (
        WorkflowNotExecutableError,
        WorkflowNotFoundError,
    )
    from app.domain.value_objects import ScheduleKind
    from app.infrastructure.celery_app.dispatcher import (
        CeleryWorkflowTaskDispatcher,
    )
    from app.infrastructure.db.repositories.audit_log_repository import (
        SqlAlchemyAuditLogRepository,
    )
    from app.infrastructure.db.repositories.authorization_repository import (
        SqlAlchemyAuthorizationRecordRepository,
    )
    from app.infrastructure.db.repositories.autonomous_run_action_repository import (
        SqlAlchemyAutonomousRunActionRepository,
    )
    from app.infrastructure.db.repositories.autonomous_run_repository import (
        SqlAlchemyAutonomousRunRepository,
    )
    from app.infrastructure.db.repositories.event_outbox_repository import (
        SqlAlchemyOutboxEventRepository,
    )
    from app.infrastructure.db.repositories.project_repository import (
        SqlAlchemyProjectRepository,
    )
    from app.infrastructure.db.repositories.target_repository import (
        SqlAlchemyTargetRepository,
    )
    from app.infrastructure.db.repositories.workflow_repository import (
        SqlAlchemyScheduleRepository,
        SqlAlchemyWorkflowExecutionRepository,
        SqlAlchemyWorkflowRepository,
        SqlAlchemyWorkflowStepRepository,
    )

    settings = get_settings()
    engine = create_async_engine(str(settings.DATABASE_URL))
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    try:
        async with session_factory() as session:
            schedule_repo = SqlAlchemyScheduleRepository(session)
            workflow_repo = SqlAlchemyWorkflowRepository(session)
            audit_repo = SqlAlchemyAuditLogRepository(session)
            run_repo = SqlAlchemyAutonomousRunRepository(session)
            project_repo = SqlAlchemyProjectRepository(session)

            schedule_service = ScheduleService(schedule_repo, workflow_repo)
            workflow_service = WorkflowService(
                workflow_repository=workflow_repo,
                step_repository=SqlAlchemyWorkflowStepRepository(session),
                execution_repository=SqlAlchemyWorkflowExecutionRepository(session),
                task_dispatcher=CeleryWorkflowTaskDispatcher(),
            )
            campaign_scheduler = CampaignSchedulerService(
                schedule_service=schedule_service,
                autonomous_service=AutonomousService(
                    run_repo=run_repo,
                    action_repo=SqlAlchemyAutonomousRunActionRepository(session),
                ),
                scope_guard=ScopeGuardService(
                    project_repository=project_repo,
                    target_repository=SqlAlchemyTargetRepository(session),
                    authorization_repository=SqlAlchemyAuthorizationRecordRepository(session),
                ),
                audit_repository=audit_repo,
            )
            outbox_service = OutboxService(SqlAlchemyOutboxEventRepository(session))

            now = datetime.now(UTC)
            due_schedules = await schedule_repo.claim_due(now, limit=50)

            for schedule in due_schedules:
                actor = schedule.created_by or schedule.project_id
                if schedule.kind is ScheduleKind.CAMPAIGN:
                    await _fire_campaign_schedule(
                        session,
                        campaign_scheduler,
                        audit_repo,
                        outbox_service,
                        project_repo,
                        schedule,
                        actor,
                        now,
                    )
                    continue
                try:
                    assert schedule.workflow_id is not None
                    execution = await workflow_service.execute(
                        schedule.workflow_id,
                        actor,
                    )
                    await schedule_service.mark_run(schedule.id)
                    await audit_repo.add(
                        AuditLogEntry(
                            id=uuid4(),
                            organization_id=None,
                            actor_id=actor,
                            action="scheduler.schedule_fired",
                            target_type="schedule",
                            target_id=schedule.id,
                            ip_address=None,
                            created_at=datetime.now(UTC),
                            after_state={
                                "workflow_id": str(schedule.workflow_id),
                                "execution_id": str(execution.id),
                                "frequency": schedule.frequency.value,
                            },
                        )
                    )
                    await session.commit()
                except (WorkflowNotExecutableError, WorkflowNotFoundError) as exc:
                    # Archived/deleted workflow — never re-fire. Permanently
                    # disable the schedule and say why.
                    schedule.is_active = False
                    schedule.next_run_at = None
                    schedule.updated_at = now
                    await schedule_repo.update(schedule)
                    await audit_repo.add(
                        AuditLogEntry(
                            id=uuid4(),
                            organization_id=None,
                            actor_id=actor,
                            action="scheduler.schedule_disabled",
                            target_type="schedule",
                            target_id=schedule.id,
                            ip_address=None,
                            created_at=datetime.now(UTC),
                            after_state={
                                "workflow_id": str(schedule.workflow_id),
                                "reason": f"{type(exc).__name__}: {exc}",
                            },
                        )
                    )
                    await session.commit()
                except Exception:
                    # Unexpected failure — rollback keeps the schedule due so
                    # the next tick re-fires it. Best-effort audit of the miss.
                    await session.rollback()
                    try:
                        await audit_repo.add(
                            AuditLogEntry(
                                id=uuid4(),
                                organization_id=None,
                                actor_id=actor,
                                action="scheduler.schedule_fire_failed",
                                target_type="schedule",
                                target_id=schedule.id,
                                ip_address=None,
                                created_at=datetime.now(UTC),
                            )
                        )
                        await session.commit()
                    except Exception:  # noqa: BLE001
                        await session.rollback()
                    logger.warning(
                        "schedule_fire_failed",
                        schedule_id=str(schedule.id),
                        workflow_id=str(schedule.workflow_id),
                    )
    finally:
        await engine.dispose()


async def _fire_campaign_schedule(
    session: object,
    campaign_scheduler: Any,
    audit_repo: object,
    outbox_service: object,
    project_repo: object,
    schedule: object,
    actor: UUID,
    now: Any,
) -> None:
    """Fire one claimed CAMPAIGN schedule and advance it (single transaction).

    ``campaign_scheduler.fire`` already consumed the occurrence inside the
    beat loop's transaction: the commit below makes the AutonomousRun (or
    the audited skip/rejection) durable together with the schedule advance.
    On FIRED we also record ``campaign.run.started`` in the SAME
    transaction — a committed fire is a committed started-event, an aborted
    fire leaves no event. On FIRED we queue the M7.4 first cycle under the
    run's OWN id, so a re-delivered kick is a no-op at the broker and
    doubly-guarded by a status check in the task body.
    """
    from datetime import UTC, datetime
    from uuid import uuid4

    from app.application.campaign_scheduler_service import CampaignFireOutcome
    from app.domain.entities import AuditLogEntry

    try:
        result = await campaign_scheduler.fire(schedule)
        if result.outcome is CampaignFireOutcome.FIRED and result.run_id is not None:
            project_entity = await project_repo.get_by_id(  # type: ignore[attr-defined]
                schedule.project_id  # type: ignore[attr-defined]
            )
            await outbox_service.record_campaign_run_started(  # type: ignore[attr-defined]
                run_id=result.run_id,
                project_id=schedule.project_id,  # type: ignore[attr-defined]
                organization_id=(
                    project_entity.organization_id if project_entity is not None else None
                ),
                schedule_id=schedule.id,  # type: ignore[attr-defined]
                objective=schedule.campaign_config.objective,  # type: ignore[attr-defined]
                max_actions=schedule.campaign_config.max_actions,  # type: ignore[attr-defined]
                max_runtime_seconds=schedule.campaign_config.max_runtime_seconds,  # type: ignore[attr-defined]
                initiated_by=schedule.created_by,  # type: ignore[attr-defined]
            )
        await session.commit()  # type: ignore[attr-defined]
        if result.outcome is CampaignFireOutcome.FIRED and result.run_id is not None:
            campaign_advance_task.apply_async(
                args=[str(result.run_id)],
                task_id=str(result.run_id),
            )
    except Exception:  # noqa: BLE001
        # Unexpected failure — the claim + fire die with this transaction,
        # leaving the schedule due for the next tick (at-least-once, never
        # wedged). Best-effort audit of the miss.
        await session.rollback()  # type: ignore[attr-defined]
        try:
            await audit_repo.add(  # type: ignore[attr-defined]
                AuditLogEntry(
                    id=uuid4(),
                    organization_id=None,
                    actor_id=actor,
                    action="scheduler.campaign_fire_failed",
                    target_type="schedule",
                    target_id=schedule.id,  # type: ignore[attr-defined]
                    ip_address=None,
                    created_at=datetime.now(UTC),
                )
            )
            await session.commit()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            await session.rollback()  # type: ignore[attr-defined]
        logger.warning(
            "campaign_fire_failed",
            schedule_id=str(schedule.id),  # type: ignore[attr-defined]
            project_id=str(schedule.project_id),  # type: ignore[attr-defined]
        )


@celery_app.task(name="specter.campaign_advance")
def campaign_advance_task(run_id: str) -> None:
    """M7.5 Phase 3 — drive the FIRST M7.4 cycle of a scheduled campaign.

    Delivered after the scheduler's fire transaction commits, using the
    run's own id as the Celery task id, so a duplicated kick can never run
    twice; the body additionally guards on run status. Everything after the
    first cycle belongs to the (untouched) M7.4 subsystem — orchestrator →
    planner → approval gate → executor → observation, entered through the
    exact same ``orchestrator.cycle`` entry point the interactive flow uses.
    """
    asyncio.run(_campaign_advance(UUID(run_id)))


async def _campaign_advance(run_id: UUID) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.plugins.builtin  # noqa: F401
    from app.application.action_classifier import ActionClassificationPolicy
    from app.application.action_validator import ActionProposalValidator
    from app.application.autonomous_observation import ObservationIngestService
    from app.application.autonomous_orchestrator import AutonomousOrchestrator
    from app.application.autonomous_recovery import AutonomousRecoveryService
    from app.application.autonomous_service import AutonomousService
    from app.application.outbox_service import OutboxService
    from app.application.planner_service import PlannerService
    from app.application.scan_service import ScanService
    from app.application.scope_guard_service import ScopeGuardService
    from app.core.config import get_settings
    from app.domain.exceptions import AutonomousRunNotFoundError
    from app.domain.value_objects import AutonomousRunStatus
    from app.infrastructure.celery_app.dispatch_after_commit import (
        drain_pending_dispatches,
    )
    from app.infrastructure.celery_app.dispatcher import (
        AfterCommitScanTaskDispatcher,
        CeleryScanTaskDispatcher,
    )
    from app.infrastructure.db.repositories.ai_context_memory_repository import (
        SqlAlchemyAIContextMemoryRepository,
    )
    from app.infrastructure.db.repositories.asset_repository import (
        SqlAlchemyAssetRepository,
    )
    from app.infrastructure.db.repositories.audit_log_repository import (
        SqlAlchemyAuditLogRepository,
    )
    from app.infrastructure.db.repositories.authorization_repository import (
        SqlAlchemyAuthorizationRecordRepository,
    )
    from app.infrastructure.db.repositories.autonomous_run_action_repository import (
        SqlAlchemyAutonomousRunActionRepository,
    )
    from app.infrastructure.db.repositories.autonomous_run_repository import (
        SqlAlchemyAutonomousRunRepository,
    )
    from app.infrastructure.db.repositories.event_outbox_repository import (
        SqlAlchemyOutboxEventRepository,
    )
    from app.infrastructure.db.repositories.finding_repository import (
        SqlAlchemyFindingRepository,
    )
    from app.infrastructure.db.repositories.planned_action_repository import (
        SqlAlchemyPlannedActionRepository,
    )
    from app.infrastructure.db.repositories.project_repository import (
        SqlAlchemyProjectRepository,
    )
    from app.infrastructure.db.repositories.scan_repository import (
        SqlAlchemyScanRepository,
    )
    from app.infrastructure.db.repositories.target_repository import (
        SqlAlchemyTargetRepository,
    )
    from app.infrastructure.db.repositories.tool_result_repository import (
        SqlAlchemyToolResultRepository,
    )
    from app.plugins.manager import PluginManager
    from app.plugins.registry import registry as plugin_registry

    settings = get_settings()
    engine = create_async_engine(str(settings.DATABASE_URL))
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    try:
        async with session_factory() as session:
            run_repo = SqlAlchemyAutonomousRunRepository(session)
            action_repo = SqlAlchemyAutonomousRunActionRepository(session)
            scan_repo = SqlAlchemyScanRepository(session)
            planned_action_repo = SqlAlchemyPlannedActionRepository(session)
            finding_repo = SqlAlchemyFindingRepository(session)
            asset_repo = SqlAlchemyAssetRepository(session)
            context_memory_repo = SqlAlchemyAIContextMemoryRepository(session)
            project_repo = SqlAlchemyProjectRepository(session)
            target_repo = SqlAlchemyTargetRepository(session)
            auth_repo = SqlAlchemyAuthorizationRecordRepository(session)
            tool_result_repo = SqlAlchemyToolResultRepository(session)
            audit_repo = SqlAlchemyAuditLogRepository(session)

            # Do nothing if the run is no longer CREATED — the interactive
            # flow (or a recovery pass) already drove it. The idempotent guard
            # makes a re-delivered campaign kick a strict no-op.
            autonomous_service = AutonomousService(
                run_repo=run_repo,
                action_repo=action_repo,
            )
            try:
                run = await autonomous_service.get(run_id)
            except AutonomousRunNotFoundError:
                return
            if run.status is not AutonomousRunStatus.CREATED:
                return

            scope_guard = ScopeGuardService(project_repo, target_repo, auth_repo)
            plugin_policy = PluginManager(plugin_registry)
            validator = ActionProposalValidator(
                policy_validator=plugin_policy,
                plugin_lookup=plugin_registry,
                target_repository=target_repo,
                action_repository=planned_action_repo,
                scope_guard=scope_guard,
                executor_enabled=settings.EXECUTOR_ENABLED,
                executor_image=settings.EXECUTOR_IMAGE,
            )
            planner = PlannerService(
                planned_action_repo=planned_action_repo,
                finding_repo=finding_repo,
                asset_repo=asset_repo,
                context_memory_repo=context_memory_repo,
                project_repo=project_repo,
                audit_repo=audit_repo,
            )
            planner.set_validator(validator)

            scan_service = ScanService(
                scan_repo,
                scope_guard,
                plugin_policy,
                AfterCommitScanTaskDispatcher(inner=CeleryScanTaskDispatcher()),
            )
            recovery = AutonomousRecoveryService(
                autonomous_service=autonomous_service,
                planner=planner,
                launcher=scan_service.create,
                scan_repository=scan_repo,
                audit_repository=audit_repo,
                max_retries_per_action=settings.AUTONOMOUS_MAX_RETRIES_PER_ACTION,
            )
            observation = ObservationIngestService(
                action_repository=action_repo,
                scan_repository=scan_repo,
                tool_result_repository=tool_result_repo,
                asset_repository=asset_repo,
                finding_repository=finding_repo,
                target_repository=target_repo,
            )
            orchestrator = AutonomousOrchestrator(
                autonomous_service=autonomous_service,
                planner=planner,
                launcher=scan_service.create,
                run_repository=run_repo,
                classification=ActionClassificationPolicy(),
                audit_repository=audit_repo,
                observation=observation,
                recovery=recovery,
            )
            outbox_service = OutboxService(SqlAlchemyOutboxEventRepository(session))

            outcome = await orchestrator.cycle(run_id)
            # M7.5 Phase 4-A: mirror the post-cycle TERMINAL transition with a
            # durable event in this same transaction (rollback ⇒ no event).
            # CANCELLED is never emitted here — it is owned exclusively by the
            # cancel endpoint, so a single run can never emit it twice.
            if outcome.run.status is AutonomousRunStatus.COMPLETED:
                project_entity = await project_repo.get_by_id(outcome.run.project_id)
                await outbox_service.record_campaign_run_completed(
                    run=outcome.run,
                    organization_id=(
                        project_entity.organization_id if project_entity is not None else None
                    ),
                )
            elif outcome.run.status is AutonomousRunStatus.FAILED:
                project_entity = await project_repo.get_by_id(outcome.run.project_id)
                await outbox_service.record_campaign_run_failed(
                    run=outcome.run,
                    organization_id=(
                        project_entity.organization_id if project_entity is not None else None
                    ),
                )
            await session.commit()
            drain_pending_dispatches()
    finally:
        await engine.dispose()


@celery_app.task(name="specter.run_ai_analysis")
def run_ai_analysis_task(project_id: str) -> None:
    """Run AI analysis pipeline for a project: planner suggestions + correlation."""
    asyncio.run(_run_ai_analysis(UUID(project_id)))


async def _run_ai_analysis(project_id: UUID) -> None:
    """Async implementation of the AI analysis pipeline."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.application.analyzer_service import AnalyzerService
    from app.core.config import get_settings
    from app.infrastructure.db.repositories.finding_repository import SqlAlchemyFindingRepository

    settings = get_settings()
    engine = create_async_engine(str(settings.DATABASE_URL))
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    try:
        async with session_factory() as session:
            finding_repo = SqlAlchemyFindingRepository(session)

            analyzer = AnalyzerService(finding_repo=finding_repo)
            await analyzer.correlate_findings(project_id)

            await session.commit()
    finally:
        await engine.dispose()


@celery_app.task(name="specter.recover_autonomous_runs")
def recover_autonomous_runs_task() -> None:
    """M7.4 Phase 4 — periodic recovery supervisor (Celery Beat).

    Finds non-terminal autonomous runs whose progress anchor
    (``last_heartbeat_at`` falling back to ``started_at``) has gone stale
    and runs the fail-closed recovery settlement on each: transport
    failures are retried once (never re-running a plugin that already
    ran), EXECUTING runs whose executed scans all went terminal are
    advanced, and ambiguous runs (executed action whose scan is gone)
    are failed. Runs with live scans are left untouched.
    """
    asyncio.run(_recover_stale_autonomous_runs())


async def _recover_stale_autonomous_runs() -> None:
    # Same local-import discipline as the other async task bodies: the
    # Celery app imports this module at process start, but the DB/API
    # stack is only loaded once a task actually runs.
    from datetime import UTC, datetime, timedelta

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.plugins.builtin  # noqa: F401 - side-effect import, registers built-in plugins
    from app.application.action_validator import ActionProposalValidator
    from app.application.autonomous_recovery import AutonomousRecoveryService
    from app.application.autonomous_service import AutonomousService
    from app.application.planner_service import PlannerService
    from app.application.scan_service import ScanService
    from app.application.scope_guard_service import ScopeGuardService
    from app.core.config import get_settings
    from app.infrastructure.celery_app.dispatch_after_commit import (
        drain_pending_dispatches,
    )
    from app.infrastructure.celery_app.dispatcher import (
        AfterCommitScanTaskDispatcher,
        CeleryScanTaskDispatcher,
    )
    from app.infrastructure.db.repositories.ai_context_memory_repository import (
        SqlAlchemyAIContextMemoryRepository,
    )
    from app.infrastructure.db.repositories.asset_repository import (
        SqlAlchemyAssetRepository,
    )
    from app.infrastructure.db.repositories.audit_log_repository import (
        SqlAlchemyAuditLogRepository,
    )
    from app.infrastructure.db.repositories.authorization_repository import (
        SqlAlchemyAuthorizationRecordRepository,
    )
    from app.infrastructure.db.repositories.autonomous_run_action_repository import (
        SqlAlchemyAutonomousRunActionRepository,
    )
    from app.infrastructure.db.repositories.autonomous_run_repository import (
        SqlAlchemyAutonomousRunRepository,
    )
    from app.infrastructure.db.repositories.finding_repository import (
        SqlAlchemyFindingRepository,
    )
    from app.infrastructure.db.repositories.planned_action_repository import (
        SqlAlchemyPlannedActionRepository,
    )
    from app.infrastructure.db.repositories.project_repository import (
        SqlAlchemyProjectRepository,
    )
    from app.infrastructure.db.repositories.scan_repository import (
        SqlAlchemyScanRepository,
    )
    from app.infrastructure.db.repositories.target_repository import (
        SqlAlchemyTargetRepository,
    )
    from app.plugins.manager import PluginManager
    from app.plugins.registry import registry as plugin_registry

    settings = get_settings()
    engine = create_async_engine(str(settings.DATABASE_URL))
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    try:
        async with session_factory() as session:
            run_repo = SqlAlchemyAutonomousRunRepository(session)
            action_repo = SqlAlchemyAutonomousRunActionRepository(session)
            scan_repo = SqlAlchemyScanRepository(session)
            planned_action_repo = SqlAlchemyPlannedActionRepository(session)
            finding_repo = SqlAlchemyFindingRepository(session)
            asset_repo = SqlAlchemyAssetRepository(session)
            context_memory_repo = SqlAlchemyAIContextMemoryRepository(session)
            project_repo = SqlAlchemyProjectRepository(session)
            target_repo = SqlAlchemyTargetRepository(session)
            auth_repo = SqlAlchemyAuthorizationRecordRepository(session)
            audit_repo = SqlAlchemyAuditLogRepository(session)

            scope_guard = ScopeGuardService(project_repo, target_repo, auth_repo)
            plugin_policy = PluginManager(plugin_registry)
            validator = ActionProposalValidator(
                policy_validator=plugin_policy,
                plugin_lookup=plugin_registry,
                target_repository=target_repo,
                action_repository=planned_action_repo,
                scope_guard=scope_guard,
                executor_enabled=settings.EXECUTOR_ENABLED,
                executor_image=settings.EXECUTOR_IMAGE,
            )
            planner = PlannerService(
                planned_action_repo=planned_action_repo,
                finding_repo=finding_repo,
                asset_repo=asset_repo,
                context_memory_repo=context_memory_repo,
                project_repo=project_repo,
                audit_repo=audit_repo,
            )
            planner.set_validator(validator)

            # Scan dispatch must happen strictly AFTER this task's commit —
            # reused from the Phase 3 request path so a retried scan can never
            # be picked up before its row exists.
            scan_service = ScanService(
                scan_repo,
                scope_guard,
                plugin_policy,
                AfterCommitScanTaskDispatcher(inner=CeleryScanTaskDispatcher()),
            )
            autonomous_service = AutonomousService(
                run_repo=run_repo,
                action_repo=action_repo,
                scan_canceller=scan_service.cancel,
            )
            recovery = AutonomousRecoveryService(
                autonomous_service=autonomous_service,
                planner=planner,
                launcher=scan_service.create,
                scan_repository=scan_repo,
                audit_repository=audit_repo,
                max_retries_per_action=settings.AUTONOMOUS_MAX_RETRIES_PER_ACTION,
            )

            now = datetime.now(UTC)
            threshold = now - timedelta(seconds=settings.AUTONOMOUS_STALLED_THRESHOLD_SECONDS)
            stale_runs = await run_repo.list_stale_active(threshold)

            for run in stale_runs:
                try:
                    await recovery.recover_stale(run.id)
                    await session.commit()
                    drain_pending_dispatches()
                except Exception:  # noqa: BLE001 - one bad run never blocks the sweep
                    await session.rollback()
                    continue
    finally:
        await engine.dispose()
