from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from app.domain.entities import Asset, ToolResult
from app.domain.exceptions import AssetNotFoundError
from app.domain.repositories import AssetRepository
from app.domain.value_objects import AssetType

if TYPE_CHECKING:
    from app.application.graph_service import GraphService


class AssetService:
    def __init__(
        self,
        asset_repository: AssetRepository,
        graph_service: GraphService | None = None,
    ) -> None:
        self._assets = asset_repository
        self._graph = graph_service

    async def list_for_project(
        self,
        project_id: UUID,
        asset_type: AssetType | None = None,
        limit: int = 20,
        cursor: datetime | None = None,
    ) -> list[Asset]:
        return await self._assets.list_for_project(
            project_id, asset_type, limit=limit, cursor=cursor
        )

    async def get(self, asset_id: UUID) -> Asset:
        asset = await self._assets.get_by_id(asset_id)
        if asset is None:
            raise AssetNotFoundError(asset_id)
        return asset

    async def upsert_from_tool_result(
        self, project_id: UUID, tool_result: ToolResult
    ) -> list[Asset]:
        payload = tool_result.normalized_payload
        plugin = tool_result.plugin
        now = datetime.now(UTC)
        upserted: list[Asset] = []

        if plugin == "ping":
            asset = await self._upsert_ping_host(project_id, tool_result, payload, now)
            if asset is not None:
                upserted.append(asset)

        elif plugin == "nmap":
            host_asset = await self._upsert_nmap_host(
                project_id, tool_result, payload, now
            )
            if host_asset is not None:
                upserted.append(host_asset)

            target = str(payload.get("target", ""))
            ports = payload.get("ports", [])
            if isinstance(ports, list):
                for port_info in ports:
                    if not isinstance(port_info, dict):
                        continue
                    if port_info.get("state") != "open":
                        continue
                    svc_asset = await self._upsert_nmap_service(
                        project_id, tool_result, target, port_info, now
                    )
                    if svc_asset is not None:
                        upserted.append(svc_asset)

        if self._graph is not None and upserted:
            await self._project_assets_to_graph(project_id, upserted)

        return upserted

    async def _project_assets_to_graph(
        self, project_id: UUID, assets: list[Asset]
    ) -> None:
        """Create/update graph nodes for assets and wire host→service edges."""
        assert self._graph is not None

        host_asset_ids: set[UUID] = set()
        service_assets: list[Asset] = []

        for asset in assets:
            await self._graph.upsert_asset_node(
                project_id,
                asset.id,
                asset.value,
                asset_type=asset.asset_type.value,
            )
            if asset.asset_type == AssetType.HOST:
                host_asset_ids.add(asset.id)
            elif asset.asset_type == AssetType.SERVICE:
                service_assets.append(asset)

        for svc in service_assets:
            svc_node = await self._graph.find_node_by_source(
                project_id, "assets", svc.id
            )
            if svc_node is None:
                continue
            target_value = svc.value.split("://")[1].split(":")[0] if "://" in svc.value else ""
            for host_asset in assets:
                if host_asset.asset_type == AssetType.HOST and host_asset.value == target_value:
                    host_node = await self._graph.find_node_by_source(
                        project_id, "assets", host_asset.id
                    )
                    if host_node is not None:
                        await self._graph.add_edge(
                            project_id,
                            host_node.id,
                            svc_node.id,
                            "hosts",
                        )
                        break

    async def _upsert_ping_host(
        self,
        project_id: UUID,
        tool_result: ToolResult,
        payload: dict[str, object],
        now: datetime,
    ) -> Asset | None:
        host = payload.get("host")
        if not host or not isinstance(host, str):
            return None

        existing = await self._assets.get_by_dedup(
            project_id, AssetType.HOST, host
        )
        if existing is not None:
            existing.last_seen = now
            existing.source_scan_id = tool_result.scan_id
            await self._assets.update(existing)
            return existing

        asset = Asset(
            id=uuid4(),
            project_id=project_id,
            asset_type=AssetType.HOST,
            value=host,
            first_seen=now,
            last_seen=now,
            source_scan_id=tool_result.scan_id,
            metadata={"reachable": payload.get("reachable")},
            created_at=now,
        )
        await self._assets.upsert(asset)
        return asset

    async def _upsert_nmap_host(
        self,
        project_id: UUID,
        tool_result: ToolResult,
        payload: dict[str, object],
        now: datetime,
    ) -> Asset | None:
        target = payload.get("target")
        if not target or not isinstance(target, str):
            return None

        existing = await self._assets.get_by_dedup(
            project_id, AssetType.HOST, target
        )
        if existing is not None:
            existing.last_seen = now
            existing.source_scan_id = tool_result.scan_id
            existing.metadata["host_up"] = payload.get("host_up")
            await self._assets.update(existing)
            return existing

        asset = Asset(
            id=uuid4(),
            project_id=project_id,
            asset_type=AssetType.HOST,
            value=target,
            first_seen=now,
            last_seen=now,
            source_scan_id=tool_result.scan_id,
            metadata={"host_up": payload.get("host_up")},
            created_at=now,
        )
        await self._assets.upsert(asset)
        return asset

    async def _upsert_nmap_service(
        self,
        project_id: UUID,
        tool_result: ToolResult,
        target: str,
        port_info: dict[str, object],
        now: datetime,
    ) -> Asset | None:
        port = port_info.get("port")
        service = port_info.get("service", "")
        protocol = port_info.get("protocol", "tcp")
        version = port_info.get("version", "")
        if port is None:
            return None

        value = f"{service}://{target}:{port}/{protocol}"

        existing = await self._assets.get_by_dedup(
            project_id, AssetType.SERVICE, value
        )
        if existing is not None:
            existing.last_seen = now
            existing.source_scan_id = tool_result.scan_id
            existing.metadata["version"] = version
            await self._assets.update(existing)
            return existing

        asset = Asset(
            id=uuid4(),
            project_id=project_id,
            asset_type=AssetType.SERVICE,
            value=value,
            first_seen=now,
            last_seen=now,
            source_scan_id=tool_result.scan_id,
            metadata={
                "port": port,
                "protocol": protocol,
                "service": service,
                "version": version,
            },
            created_at=now,
        )
        await self._assets.upsert(asset)
        return asset
