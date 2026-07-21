"""SQLAlchemy implementation of `AssetRepository` (Milestone 4B)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession as SqlAsyncSession

from app.domain.entities import Asset
from app.domain.value_objects import AssetType
from app.infrastructure.db.models.asset import AssetModel


def _to_entity(row: AssetModel) -> Asset:
    return Asset(
        id=row.id,
        project_id=row.project_id,
        asset_type=AssetType(row.asset_type),
        value=row.value,
        first_seen=row.first_seen,
        last_seen=row.last_seen,
        in_scope=row.in_scope,
        source_scan_id=row.source_scan_id,
        metadata=dict(row.metadata_ or {}),
        created_at=row.created_at,
    )


class SqlAlchemyAssetRepository:
    def __init__(self, session: SqlAsyncSession) -> None:
        self._session = session

    async def get_by_id(self, asset_id: UUID) -> Asset | None:
        row = await self._session.get(AssetModel, asset_id)
        return _to_entity(row) if row else None

    async def list_for_project(
        self,
        project_id: UUID,
        asset_type: AssetType | None = None,
        limit: int = 20,
        cursor: datetime | None = None,
    ) -> list[Asset]:
        stmt = select(AssetModel).where(AssetModel.project_id == project_id)
        if asset_type is not None:
            stmt = stmt.where(AssetModel.asset_type == asset_type.value)
        if cursor is not None:
            stmt = stmt.where(AssetModel.created_at < cursor)
        stmt = stmt.order_by(AssetModel.created_at.desc()).limit(limit + 1)
        result = await self._session.execute(stmt)
        return [_to_entity(row) for row in result.scalars().all()]

    async def add(self, asset: Asset) -> None:
        model = AssetModel(
            id=asset.id,
            project_id=asset.project_id,
            asset_type=asset.asset_type.value,
            value=asset.value,
            first_seen=asset.first_seen,
            last_seen=asset.last_seen,
            in_scope=asset.in_scope,
            source_scan_id=asset.source_scan_id,
            metadata_=asset.metadata,
        )
        self._session.add(model)
        await self._session.flush()

    async def update(self, asset: Asset) -> None:
        model = await self._session.get(AssetModel, asset.id)
        if model is not None:
            model.asset_type = asset.asset_type.value
            model.value = asset.value
            model.last_seen = asset.last_seen
            model.in_scope = asset.in_scope
            model.source_scan_id = asset.source_scan_id
            model.metadata_ = asset.metadata
            await self._session.flush()

    async def upsert(self, asset: Asset) -> Asset:
        """Insert or update: if an asset with the same (project_id,
        asset_type, value) exists, update its last_seen and metadata."""
        now = datetime.now(UTC)
        stmt = (
            pg_insert(AssetModel)
            .values(
                id=asset.id,
                project_id=asset.project_id,
                asset_type=asset.asset_type.value,
                value=asset.value,
                first_seen=now,
                last_seen=now,
                in_scope=asset.in_scope,
                source_scan_id=asset.source_scan_id,
                metadata_=asset.metadata,
            )
            .on_conflict_do_update(
                index_elements=["project_id", "asset_type", "value"],
                set_={
                    "last_seen": now,
                    "source_scan_id": asset.source_scan_id,
                    "metadata_": asset.metadata,
                    "in_scope": asset.in_scope,
                },
            )
            .returning(AssetModel)
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one()
        await self._session.flush()
        return _to_entity(row)

    async def get_by_dedup(
        self, project_id: UUID, asset_type: AssetType, value: str
    ) -> Asset | None:
        stmt = select(AssetModel).where(
            AssetModel.project_id == project_id,
            AssetModel.asset_type == asset_type.value,
            AssetModel.value == value,
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        return _to_entity(row) if row else None
