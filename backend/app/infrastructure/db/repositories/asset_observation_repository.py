"""SQLAlchemy implementation of `AssetObservationRepository` (M7.3 Phase 2)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as SqlAsyncSession

from app.domain.entities import AssetObservation
from app.infrastructure.db.models.asset_observation import AssetObservationModel


def _to_entity(row: AssetObservationModel) -> AssetObservation:
    return AssetObservation(
        id=row.id,
        project_id=row.project_id,
        asset_id=row.asset_id,
        tool_result_id=row.tool_result_id,
        scan_id=row.scan_id,
        plugin=row.plugin,
        observed_at=row.observed_at,
        details=dict(row.details or {}),
    )


class SqlAlchemyAssetObservationRepository:
    def __init__(self, session: SqlAsyncSession) -> None:
        self._session = session

    async def add(self, observation: AssetObservation) -> None:
        model = AssetObservationModel(
            id=observation.id,
            project_id=observation.project_id,
            asset_id=observation.asset_id,
            tool_result_id=observation.tool_result_id,
            scan_id=observation.scan_id,
            plugin=observation.plugin,
            observed_at=observation.observed_at or datetime.now(UTC),
            details=observation.details,
        )
        self._session.add(model)
        await self._session.flush()

    async def exists_for(
        self, tool_result_id: UUID, asset_id: UUID
    ) -> bool:
        stmt = select(AssetObservationModel.id).where(
            AssetObservationModel.tool_result_id == tool_result_id,
            AssetObservationModel.asset_id == asset_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def list_for_asset(
        self, asset_id: UUID, limit: int = 100
    ) -> list[AssetObservation]:
        stmt = (
            select(AssetObservationModel)
            .where(AssetObservationModel.asset_id == asset_id)
            .order_by(AssetObservationModel.observed_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return [_to_entity(row) for row in result.scalars().all()]
