"""SQLAlchemy implementation of `ScanRepository` (Milestone 3)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession as SqlAsyncSession

from app.domain.entities import Scan
from app.domain.value_objects import ScanStatus
from app.infrastructure.db.models.scan import ScanModel


def _scan_to_entity(row: ScanModel) -> Scan:
    return Scan(
        id=row.id,
        project_id=row.project_id,
        initiated_by=row.initiated_by,
        plugin=row.plugin,
        status=ScanStatus(row.status),
        target_ids=[UUID(t) for t in (row.target_ids or [])],
        plugin_config=dict(row.plugin_config or {}),
        created_at=row.created_at,
        started_at=row.started_at,
        completed_at=row.completed_at,
        logs_path=row.logs_path,
        artifacts_path=row.artifacts_path,
        exit_code=row.exit_code,
        error_message=row.error_message,
    )


class SqlAlchemyScanRepository:
    """Satisfies `app.domain.repositories.ScanRepository` structurally."""

    def __init__(self, session: SqlAsyncSession) -> None:
        self._session = session

    async def create(self, scan: Scan) -> None:
        model = ScanModel(
            id=scan.id,
            project_id=scan.project_id,
            initiated_by=scan.initiated_by,
            plugin=scan.plugin,
            status=scan.status.value,
            target_ids=[str(t) for t in scan.target_ids],
            plugin_config=scan.plugin_config,
        )
        self._session.add(model)
        await self._session.flush()

    async def get(self, scan_id: UUID) -> Scan | None:
        row = await self._session.get(ScanModel, scan_id)
        return _scan_to_entity(row) if row else None

    async def list(
        self, project_id: UUID, limit: int = 20, cursor: datetime | None = None
    ) -> list[Scan]:
        stmt = select(ScanModel).where(ScanModel.project_id == project_id)
        if cursor is not None:
            stmt = stmt.where(ScanModel.created_at < cursor)
        stmt = stmt.order_by(ScanModel.created_at.desc()).limit(limit + 1)
        result = await self._session.execute(stmt)
        return [_scan_to_entity(row) for row in result.scalars().all()]

    async def update_status(self, scan_id: UUID, status: ScanStatus) -> None:
        values: dict[str, object] = {"status": status.value}
        if status is ScanStatus.RUNNING:
            values["started_at"] = datetime.now(UTC)
        stmt = update(ScanModel).where(ScanModel.id == scan_id).values(**values)
        await self._session.execute(stmt)
        await self._session.flush()

    async def append_log(self, scan_id: UUID, logs_path: str) -> None:
        stmt = update(ScanModel).where(ScanModel.id == scan_id).values(logs_path=logs_path)
        await self._session.execute(stmt)
        await self._session.flush()

    async def complete(self, scan_id: UUID, exit_code: int, artifacts_path: str | None) -> None:
        stmt = (
            update(ScanModel)
            .where(ScanModel.id == scan_id)
            .values(
                status=ScanStatus.COMPLETED.value,
                exit_code=exit_code,
                artifacts_path=artifacts_path,
                completed_at=datetime.now(UTC),
            )
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def fail(self, scan_id: UUID, error_message: str, exit_code: int | None) -> None:
        stmt = (
            update(ScanModel)
            .where(ScanModel.id == scan_id)
            .values(
                status=ScanStatus.FAILED.value,
                error_message=error_message,
                exit_code=exit_code,
                completed_at=datetime.now(UTC),
            )
        )
        await self._session.execute(stmt)
        await self._session.flush()
