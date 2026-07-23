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
from uuid import UUID

from app.infrastructure.celery_app.app import celery_app


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
    from app.application.scope_guard_service import ScopeGuardService
    from app.core.config import get_settings
    from app.infrastructure.db.repositories.audit_log_repository import (
        SqlAlchemyAuditLogRepository,
    )
    from app.infrastructure.db.repositories.authorization_repository import (
        SqlAlchemyAuthorizationRecordRepository,
    )
    from app.infrastructure.db.repositories.project_repository import SqlAlchemyProjectRepository
    from app.infrastructure.db.repositories.scan_repository import SqlAlchemyScanRepository
    from app.infrastructure.db.repositories.target_repository import SqlAlchemyTargetRepository
    from app.infrastructure.db.repositories.tool_result_repository import (
        SqlAlchemyToolResultRepository,
    )
    from app.infrastructure.execution.engine import ExecutionEngine
    from app.infrastructure.storage.local_artifact_store import LocalArtifactStore
    from app.plugins.manager import PluginManager
    from app.plugins.normalizer_registry import normalizer_registry
    from app.plugins.registry import registry

    settings = get_settings()

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
            execution_engine = ExecutionEngine(
                scan_repository=SqlAlchemyScanRepository(session),
                scope_guard=ScopeGuardService(
                    project_repository=SqlAlchemyProjectRepository(session),
                    target_repository=SqlAlchemyTargetRepository(session),
                    authorization_repository=SqlAlchemyAuthorizationRecordRepository(session),
                ),
                plugin_manager=PluginManager(registry),
                artifact_store=LocalArtifactStore(settings.SCAN_ARTIFACTS_DIR),
                audit_log_repository=SqlAlchemyAuditLogRepository(session),
                tool_result_repository=SqlAlchemyToolResultRepository(session),
                normalizer_registry=normalizer_registry,
                default_timeout_seconds=settings.SCAN_DEFAULT_TIMEOUT_SECONDS,
            )
            await execution_engine.run(scan_id)
            await session.commit()
    finally:
        await engine.dispose()


@celery_app.task(name="specter.execute_workflow")
def execute_workflow_task(execution_id: str) -> None:
    """Entry point for workflow execution — same sync-bridge pattern as scan tasks."""
    asyncio.run(_execute_workflow(UUID(execution_id)))


async def _execute_workflow(execution_id: UUID) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.plugins.builtin  # noqa: F401
    import app.plugins.normalizers  # noqa: F401
    from app.application.correlation_service import CorrelationService
    from app.application.workflow_executor import WorkflowExecutor
    from app.core.config import get_settings
    from app.infrastructure.db.repositories.finding_repository import (
        SqlAlchemyFindingRepository,
    )
    from app.infrastructure.db.repositories.scan_repository import (
        SqlAlchemyScanRepository,
    )
    from app.infrastructure.db.repositories.tool_result_repository import (
        SqlAlchemyToolResultRepository,
    )
    from app.infrastructure.db.repositories.workflow_repository import (
        SqlAlchemyWorkflowExecutionRepository,
        SqlAlchemyWorkflowStepRepository,
    )
    from app.plugins.manager import PluginManager
    from app.plugins.normalizer_registry import normalizer_registry
    from app.plugins.registry import registry

    settings = get_settings()
    engine = create_async_engine(str(settings.DATABASE_URL))
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    try:
        async with session_factory() as session:
            executor = WorkflowExecutor(
                plugin_manager=PluginManager(registry),
                normalizer_registry=normalizer_registry,
                execution_repository=SqlAlchemyWorkflowExecutionRepository(session),
                step_repository=SqlAlchemyWorkflowStepRepository(session),
                scan_repository=SqlAlchemyScanRepository(session),
                tool_result_repository=SqlAlchemyToolResultRepository(session),
                correlation_service=CorrelationService(
                    SqlAlchemyFindingRepository(session)
                ),
                default_timeout_seconds=settings.SCAN_DEFAULT_TIMEOUT_SECONDS,
            )
            try:
                await executor.execute(execution_id)
                await session.commit()
            except Exception:
                await session.rollback()
                raise
    finally:
        await engine.dispose()
