from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.application.asset_service import AssetService
from app.domain.entities import Asset, ToolResult
from app.domain.exceptions import AssetNotFoundError
from app.domain.value_objects import AssetType
from tests.fakes import FakeAssetRepository


def _make_asset(
    project_id,
    asset_type: AssetType = AssetType.HOST,
    value: str = "10.0.0.1",
) -> Asset:
    now = datetime.now(UTC)
    return Asset(
        id=uuid4(),
        project_id=project_id,
        asset_type=asset_type,
        value=value,
        first_seen=now,
        last_seen=now,
        source_scan_id=uuid4(),
        metadata={},
        created_at=now,
    )


def _make_tool_result(plugin, payload, scan_id=None):
    return ToolResult(
        id=uuid4(),
        scan_id=scan_id or uuid4(),
        plugin=plugin,
        target="test-target",
        normalized_payload=payload,
        created_at=datetime.now(UTC),
    )


@pytest.fixture
def repo():
    return FakeAssetRepository()


def _make_service(repo: FakeAssetRepository) -> AssetService:
    return AssetService(asset_repository=repo)


@pytest.mark.asyncio
async def test_list_for_project_returns_assets(repo):
    project_id = uuid4()
    asset_a = _make_asset(project_id, AssetType.HOST, "10.0.0.1")
    asset_b = _make_asset(project_id, AssetType.SERVICE, "ssh://10.0.0.1:22/tcp")
    await repo.add(asset_a)
    await repo.add(asset_b)

    service = _make_service(repo)
    result = await service.list_for_project(project_id)

    assert len(result) == 2
    values = {a.value for a in result}
    assert values == {"10.0.0.1", "ssh://10.0.0.1:22/tcp"}


@pytest.mark.asyncio
async def test_list_for_project_filters_by_type(repo):
    project_id = uuid4()
    host = _make_asset(project_id, AssetType.HOST, "10.0.0.1")
    service = _make_asset(project_id, AssetType.SERVICE, "ssh://10.0.0.1:22/tcp")
    await repo.add(host)
    await repo.add(service)

    svc = _make_service(repo)
    result = await svc.list_for_project(project_id, asset_type=AssetType.SERVICE)

    assert len(result) == 1
    assert result[0].asset_type is AssetType.SERVICE
    assert result[0].value == "ssh://10.0.0.1:22/tcp"


@pytest.mark.asyncio
async def test_get_raises_for_unknown_id(repo):
    svc = _make_service(repo)
    with pytest.raises(AssetNotFoundError):
        await svc.get(uuid4())


@pytest.mark.asyncio
async def test_get_returns_existing_asset(repo):
    project_id = uuid4()
    asset = _make_asset(project_id)
    await repo.add(asset)

    svc = _make_service(repo)
    result = await svc.get(asset.id)

    assert result.id == asset.id
    assert result.value == asset.value


@pytest.mark.asyncio
async def test_upsert_from_ping_creates_host_asset(repo):
    project_id = uuid4()
    tr = _make_tool_result(
        "ping",
        {"host": "10.0.0.1", "reachable": True},
    )

    svc = _make_service(repo)
    assets = await svc.upsert_from_tool_result(project_id, tr)

    assert len(assets) == 1
    created = assets[0]
    assert created.asset_type is AssetType.HOST
    assert created.value == "10.0.0.1"
    assert created.project_id == project_id
    assert created.metadata == {"reachable": True}
    assert created.source_scan_id == tr.scan_id


@pytest.mark.asyncio
async def test_upsert_from_nmap_creates_host_and_services(repo):
    project_id = uuid4()
    tr = _make_tool_result(
        "nmap",
        {
            "target": "10.0.0.1",
            "host_up": True,
            "ports": [
                {
                    "port": 22, "state": "open", "service": "ssh",
                    "protocol": "tcp", "version": "OpenSSH 8.9",
                },
                {
                    "port": 80, "state": "open", "service": "http",
                    "protocol": "tcp", "version": "nginx 1.24",
                },
            ],
        },
    )

    svc = _make_service(repo)
    assets = await svc.upsert_from_tool_result(project_id, tr)

    assert len(assets) == 3
    types = [a.asset_type for a in assets]
    assert types.count(AssetType.HOST) == 1
    assert types.count(AssetType.SERVICE) == 2

    host = [a for a in assets if a.asset_type is AssetType.HOST][0]
    assert host.value == "10.0.0.1"
    assert host.metadata["host_up"] is True

    svc_assets = sorted(
        [a for a in assets if a.asset_type is AssetType.SERVICE],
        key=lambda a: a.metadata["port"],
    )
    assert svc_assets[0].value == "ssh://10.0.0.1:22/tcp"
    assert svc_assets[0].metadata["version"] == "OpenSSH 8.9"
    assert svc_assets[1].value == "http://10.0.0.1:80/tcp"
    assert svc_assets[1].metadata["version"] == "nginx 1.24"


@pytest.mark.asyncio
async def test_upsert_from_nmap_updates_existing_host(repo):
    project_id = uuid4()
    scan_id_1 = uuid4()
    scan_id_2 = uuid4()

    tr1 = _make_tool_result(
        "nmap",
        {"target": "10.0.0.1", "host_up": True, "ports": []},
        scan_id=scan_id_1,
    )
    tr2 = _make_tool_result(
        "nmap",
        {"target": "10.0.0.1", "host_up": True, "ports": []},
        scan_id=scan_id_2,
    )

    svc = _make_service(repo)
    first = await svc.upsert_from_tool_result(project_id, tr1)
    assert len(first) == 1
    first_seen = first[0].first_seen
    first_last_seen = first[0].last_seen

    second = await svc.upsert_from_tool_result(project_id, tr2)
    assert len(second) == 1
    updated = second[0]
    assert updated.id == first[0].id
    assert updated.first_seen == first_seen
    assert updated.last_seen >= first_last_seen
    assert updated.source_scan_id == scan_id_2


@pytest.mark.asyncio
async def test_upsert_from_unknown_plugin_returns_empty(repo):
    project_id = uuid4()
    tr = _make_tool_result("unknown_plugin", {"some": "data"})

    svc = _make_service(repo)
    assets = await svc.upsert_from_tool_result(project_id, tr)

    assert assets == []
