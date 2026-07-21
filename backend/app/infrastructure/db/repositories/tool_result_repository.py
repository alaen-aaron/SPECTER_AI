"""SQLAlchemy implementation of `ToolResultRepository` (Milestone 4A)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as SqlAsyncSession

from app.domain.entities import ToolResult
from app.infrastructure.db.models.tool_result import ToolResultModel


def _to_entity(row: ToolResultModel) -> ToolResult:
    return ToolResult(
        id=row.id,
        scan_id=row.scan_id,
        plugin=row.plugin,
        target=row.target,
        normalized_payload=dict(row.normalized_payload or {}),
        raw_output_path=row.raw_output_path,
        created_at=row.created_at,
    )


class SqlAlchemyToolResultRepository:
    def __init__(self, session: SqlAsyncSession) -> None:
        self._session = session

    async def add(self, tool_result: ToolResult) -> None:
        model = ToolResultModel(
            id=tool_result.id,
            scan_id=tool_result.scan_id,
            plugin=tool_result.plugin,
            target=tool_result.target,
            normalized_payload=tool_result.normalized_payload,
            raw_output_path=tool_result.raw_output_path,
        )
        self._session.add(model)
        await self._session.flush()

    async def get(self, tool_result_id: UUID) -> ToolResult | None:
        row = await self._session.get(ToolResultModel, tool_result_id)
        return _to_entity(row) if row else None

    async def list_for_scan(self, scan_id: UUID) -> list[ToolResult]:
        stmt = (
            select(ToolResultModel)
            .where(ToolResultModel.scan_id == scan_id)
            .order_by(ToolResultModel.created_at)
        )
        result = await self._session.execute(stmt)
        return [_to_entity(row) for row in result.scalars().all()]
