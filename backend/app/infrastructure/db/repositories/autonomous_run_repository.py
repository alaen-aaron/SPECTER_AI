"""SQLAlchemy implementation of `AutonomousRunRepository` (M7.4)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql.asyncpg import AsyncAdapt_asyncpg_dbapi
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession as SqlAsyncSession

from app.domain.entities import AutonomousRun
from app.domain.exceptions import AutonomousRunActiveExistsError
from app.domain.value_objects import AutonomousRunStatus
from app.infrastructure.db.models.autonomous import (
    AutonomousRunActionModel,
    AutonomousRunModel,
)

# Partial-unique index name (see db/models/autonomous.py): exactly one
# non-terminal run per project is enforced by the DATABASE, not by an
# application-level check-then-insert that two concurrent requests could
# race past.
_UQ_ACTIVE_PROJECT = "uq_autonomous_runs_active_project"


def _constraint_name(exc: object) -> str | None:
    """Best-effort pg constraint name from a failure exception.

    asyncpg exposes it directly as ``.constraint_name`` on the error (and
    on its Optional ``.diag`` Diagnostics object in older versions).
    """
    name = getattr(exc, "constraint_name", None)
    if isinstance(name, str):
        return name
    diag = getattr(exc, "diag", None)
    if diag is not None:
        name = getattr(diag, "constraint_name", None)
        if isinstance(name, str):
            return name
    return None


def _to_entity(row: AutonomousRunModel) -> AutonomousRun:
    return AutonomousRun(
        id=row.id,
        project_id=row.project_id,
        initiated_by=row.initiated_by,
        status=AutonomousRunStatus(row.status),
        objective=row.objective,
        max_actions=row.max_actions,
        max_runtime_seconds=row.max_runtime_seconds,
        current_cycle=row.current_cycle,
        actions_completed=row.actions_completed,
        approval_policy=row.approval_policy,
        started_at=row.started_at,
        completed_at=row.completed_at,
        last_heartbeat_at=row.last_heartbeat_at,
        error_message=row.error_message,
        result_summary=row.result_summary or {},
        created_at=row.created_at,
    )


class SqlAlchemyAutonomousRunRepository:
    def __init__(self, session: SqlAsyncSession) -> None:
        self._session = session

    async def create(self, run: AutonomousRun) -> None:
        model = AutonomousRunModel(
            id=run.id,
            project_id=run.project_id,
            initiated_by=run.initiated_by,
            status=run.status.value,
            objective=run.objective,
            max_actions=run.max_actions,
            max_runtime_seconds=run.max_runtime_seconds,
            current_cycle=run.current_cycle,
            actions_completed=run.actions_completed,
            approval_policy=run.approval_policy,
            started_at=run.started_at,
            completed_at=run.completed_at,
            last_heartbeat_at=run.last_heartbeat_at,
            error_message=run.error_message,
            result_summary=run.result_summary,
        )
        self._session.add(model)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            if self._is_active_project_violation(exc):
                # The app-level existence check lost a race: another run for
                # this project already holds the active slot. Surface the
                # domain error so the API maps it as a clean 409 instead of
                # a raw IntegrityError.
                raise AutonomousRunActiveExistsError(run.project_id) from exc
            raise
        except AsyncAdapt_asyncpg_dbapi.IntegrityError as exc:
            # asyncpg's dialect-level IntegrityError is a plain Exception,
            # NOT a sqlalchemy.exc.IntegrityError subclass — it can escape
            # the core error wrapping for ORM flushes under greenlet mode.
            if self._is_active_project_violation(exc):
                raise AutonomousRunActiveExistsError(run.project_id) from exc
            raise

    async def get(self, run_id: UUID) -> AutonomousRun | None:
        row = await self._session.get(AutonomousRunModel, run_id)
        return _to_entity(row) if row else None

    async def list_for_project(
        self,
        project_id: UUID,
        status: AutonomousRunStatus | None = None,
        limit: int = 20,
        cursor: datetime | None = None,
    ) -> list[AutonomousRun]:
        stmt = select(AutonomousRunModel).where(
            AutonomousRunModel.project_id == project_id
        )
        if status is not None:
            stmt = stmt.where(AutonomousRunModel.status == status.value)
        if cursor is not None:
            stmt = stmt.where(AutonomousRunModel.created_at < cursor)
        stmt = stmt.order_by(AutonomousRunModel.created_at.desc()).limit(limit)
        result = await self._session.execute(stmt)
        return [_to_entity(row) for row in result.scalars().all()]

    async def get_active_for_project(self, project_id: UUID) -> AutonomousRun | None:
        terminal_statuses = {s.value for s in AutonomousRunStatus} - {
            AutonomousRunStatus.COMPLETED.value,
            AutonomousRunStatus.CANCELLED.value,
            AutonomousRunStatus.FAILED.value,
        }
        stmt = (
            select(AutonomousRunModel)
            .where(
                AutonomousRunModel.project_id == project_id,
                AutonomousRunModel.status.in_(terminal_statuses),
            )
            .order_by(AutonomousRunModel.created_at.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        return _to_entity(row) if row else None

    async def update(self, run: AutonomousRun) -> None:
        row = await self._session.get(AutonomousRunModel, run.id)
        if row is None:
            return
        row.status = run.status
        row.objective = run.objective
        row.current_cycle = run.current_cycle
        row.actions_completed = run.actions_completed
        row.started_at = run.started_at
        row.completed_at = run.completed_at
        row.last_heartbeat_at = run.last_heartbeat_at
        row.error_message = run.error_message
        row.result_summary = run.result_summary
        await self._session.flush()

    async def count_actions(self, run_id: UUID) -> int:
        stmt = select(func.count()).where(AutonomousRunActionModel.run_id == run_id)
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def try_cycle_lock(self, run_id: UUID) -> bool:
        """M7.4 Phase 4 — durable per-run cycle guard (advisory xact lock).

        ``pg_try_advisory_xact_lock`` is transaction-scoped: it is held for
        as long as the current DB transaction (the request / supervisor
        pass) lives and is released automatically at commit/rollback — so a
        pooled connection can never carry it into a later request. Two
        concurrent ``cycle()`` calls for the same run therefore fail fast at
        the database, independent of which process issued them.
        """
        key = int(run_id.int & ((1 << 63) - 1))
        stmt = text("SELECT pg_try_advisory_xact_lock(:key)")
        result = await self._session.execute(stmt, {"key": key})
        acquired = result.scalar_one()
        return bool(acquired)

    async def list_stale_active(self, threshold: datetime) -> list[AutonomousRun]:
        """Non-terminal runs whose progress anchor predates ``threshold``.

        Anchor = ``last_heartbeat_at`` falling back to ``started_at``.
        Returns oldest-first so the supervisor settles the most abandoned
        runs first.
        """
        terminal = {s.value for s in AutonomousRunStatus} - {
            AutonomousRunStatus.COMPLETED.value,
            AutonomousRunStatus.CANCELLED.value,
            AutonomousRunStatus.FAILED.value,
        }
        anchor = func.coalesce(
            AutonomousRunModel.last_heartbeat_at, AutonomousRunModel.started_at
        )
        stmt = (
            select(AutonomousRunModel)
            .where(
                AutonomousRunModel.status.in_(terminal),
                AutonomousRunModel.started_at.isnot(None),
                anchor < threshold,
            )
            .order_by(anchor.asc())
        )
        result = await self._session.execute(stmt)
        return [_to_entity(row) for row in result.scalars().all()]

    @staticmethod
    def _is_active_project_violation(exc: Exception) -> bool:
        # Transport chain varies by asyncpg version / dialect adapters:
        # sqlalchemy.exc.IntegrityError -> .orig -> AsyncAdapt adapter ->
        # .__cause__ asyncpg exception. asyncpg >=0.29 exposes the
        # constraint name directly on the error; older versions carry it
        # on a `.diag` (Diagnostics) object. Check every link.
        if _constraint_name(exc) == _UQ_ACTIVE_PROJECT:
            return True
        orig = getattr(exc, "orig", None)
        if isinstance(orig, BaseException) and _constraint_name(orig) == _UQ_ACTIVE_PROJECT:
            return True
        cause = getattr(orig, "__cause__", None) or getattr(orig, "__context__", None)
        return bool(
            isinstance(cause, BaseException) and _constraint_name(cause) == _UQ_ACTIVE_PROJECT
        )
