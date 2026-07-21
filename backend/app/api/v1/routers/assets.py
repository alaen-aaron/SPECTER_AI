"""Asset inventory endpoints."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.v1.deps import get_asset_service, require_project_role, require_project_role_for_asset
from app.api.v1.schemas.assets import AssetResponse, PaginatedAssetResponse
from app.application.asset_service import AssetService
from app.domain.entities import Asset, ProjectMember

router = APIRouter(tags=["assets"])


@router.get(
    "/projects/{project_id}/assets",
    response_model=PaginatedAssetResponse,
    summary="List assets for a project (any project member)",
)
async def list_assets(
    project_id: UUID,
    asset_type: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    cursor: datetime | None = Query(default=None),
    _member: ProjectMember = Depends(require_project_role()),
    service: AssetService = Depends(get_asset_service),
) -> PaginatedAssetResponse:
    from app.domain.value_objects import AssetType

    asset_type_enum = AssetType(asset_type) if asset_type else None
    assets = await service.list_for_project(
        project_id, asset_type_enum, limit=limit, cursor=cursor
    )
    has_more = len(assets) > limit
    items = assets[:limit]
    next_cursor = items[-1].created_at if has_more and items else None
    return PaginatedAssetResponse(items=items, next_cursor=next_cursor)


@router.get(
    "/assets/{asset_id}",
    response_model=AssetResponse,
    summary="Get a single asset by id (any member of its owning project)",
)
async def get_asset(
    asset_id: UUID,
    _member: ProjectMember = Depends(require_project_role_for_asset()),
    service: AssetService = Depends(get_asset_service),
) -> Asset:
    return await service.get(asset_id)
