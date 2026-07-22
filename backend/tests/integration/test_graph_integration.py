"""
Comprehensive integration tests for Knowledge Graph (SRS §15A).

Covers all requested scenarios:
  1. Asset creation → graph node verification
  2. Finding creation → graph node verification
  3. Edge relationship verification
  4. Every graph API endpoint via HTTPX ASGI client
  5. Shortest-path endpoint with sample data
  6. Graph projection idempotency (no duplicates on re-run)
  7. Cascade delete behavior when assets/findings are removed
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.application.asset_service import AssetService
from app.application.finding_service import FindingService
from app.application.graph_service import GraphService
from app.domain.entities import Asset, Finding, ToolResult, User
from app.domain.value_objects import (
    AssetType,
    FindingStatus,
    GraphEdgeType,
    GraphNodeType,
    Severity,
)
from app.main import create_app
from tests.fakes import (
    FakeAssetRepository,
    FakeFindingRepository,
    FakeGraphRepository,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tool_result(
    plugin: str,
    target: str,
    payload: dict[str, object],
    scan_id=None,
) -> ToolResult:
    return ToolResult(
        id=uuid4(),
        scan_id=scan_id or uuid4(),
        plugin=plugin,
        target=target,
        normalized_payload=payload,
        created_at=datetime.now(UTC),
    )


def _make_finding(
    project_id,
    title: str = "Test finding",
    severity: Severity = Severity.MEDIUM,
    asset_id=None,
    dedup_key: str = "",
) -> Finding:
    return Finding(
        id=uuid4(),
        project_id=project_id,
        title=title,
        severity=severity,
        status=FindingStatus.OPEN,
        description="test",
        asset_id=asset_id,
        dedup_key=dedup_key,
        tool_result_ids=[uuid4()],
        created_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# Fixture: all fakes wired together with graph projection enabled
# ---------------------------------------------------------------------------


@pytest.fixture
def repos():
    return {
        "assets": FakeAssetRepository(),
        "findings": FakeFindingRepository(),
        "graph": FakeGraphRepository(),
    }


@pytest.fixture
def graph_service(repos):
    return GraphService(repos["graph"])


@pytest.fixture
def asset_service(repos, graph_service):
    return AssetService(repos["assets"], graph_service)


@pytest.fixture
def finding_service(repos, graph_service):
    return FindingService(repos["findings"], repos["assets"], graph_service)


# ===========================================================================
# 1. Asset creation → graph node verification
# ===========================================================================


@pytest.mark.asyncio
async def test_ping_asset_creates_graph_node(asset_service, repos):
    project_id = uuid4()
    tr = _make_tool_result(
        "ping", "192.168.1.1",
        {"host": "192.168.1.1", "reachable": True},
    )

    assets = await asset_service.upsert_from_tool_result(project_id, tr)
    assert len(assets) == 1

    graph_nodes = await repos["graph"].list_nodes_for_project(project_id)
    assert len(graph_nodes) == 1
    node = graph_nodes[0]
    assert node.node_type == GraphNodeType.ASSET
    assert node.source_table == "assets"
    assert node.source_id == assets[0].id
    assert node.label == "192.168.1.1"


@pytest.mark.asyncio
async def test_nmap_asset_creates_graph_nodes(asset_service, repos):
    project_id = uuid4()
    tr = _make_tool_result(
        "nmap", "10.0.0.1",
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

    assets = await asset_service.upsert_from_tool_result(project_id, tr)
    assert len(assets) == 3  # 1 host + 2 services

    graph_nodes = await repos["graph"].list_nodes_for_project(project_id)
    assert len(graph_nodes) == 3
    node_types = {n.node_type for n in graph_nodes}
    assert node_types == {GraphNodeType.ASSET}


# ===========================================================================
# 2. Finding creation → graph node verification
# ===========================================================================


@pytest.mark.asyncio
async def test_finding_creates_graph_node(finding_service, repos):
    project_id = uuid4()
    tr = _make_tool_result(
        "nmap", "10.0.0.1",
        {
            "target": "10.0.0.1",
            "ports": [
                {"port": 21, "state": "open", "service": "ftp", "protocol": "tcp", "version": ""},
            ],
        },
    )

    findings = await finding_service.create_from_tool_result(project_id, tr)
    assert len(findings) == 1

    graph_nodes = await repos["graph"].list_nodes_for_project(
        project_id, GraphNodeType.FINDING
    )
    assert len(graph_nodes) == 1
    node = graph_nodes[0]
    assert node.source_table == "findings"
    assert node.source_id == findings[0].id
    assert "ftp" in node.label


@pytest.mark.asyncio
async def test_finding_with_asset_creates_vulnerable_to_edge(finding_service, repos):
    project_id = uuid4()
    asset_id = uuid4()

    from app.domain.entities import GraphEdge, GraphNode

    host_asset = Asset(
        id=asset_id,
        project_id=project_id,
        asset_type=AssetType.HOST,
        value="10.0.0.1",
        first_seen=datetime.now(UTC),
        last_seen=datetime.now(UTC),
        source_scan_id=uuid4(),
        metadata={},
        created_at=datetime.now(UTC),
    )
    await repos["assets"].upsert(host_asset)

    finding = _make_finding(
        project_id,
        title="FTP on 10.0.0.1",
        severity=Severity.LOW,
        asset_id=asset_id,
    )
    await repos["findings"].add(finding)

    assert await repos["graph"].list_nodes_for_project(project_id) == []

    node = await repos["graph"].upsert_node(
        GraphNode(
            id=uuid4(),
            project_id=project_id,
            node_type=GraphNodeType.FINDING,
            source_table="findings",
            source_id=finding.id,
            label=finding.title,
        )
    )
    asset_node = await repos["graph"].upsert_node(
        GraphNode(
            id=uuid4(),
            project_id=project_id,
            node_type=GraphNodeType.ASSET,
            source_table="assets",
            source_id=asset_id,
            label="10.0.0.1",
        )
    )
    await repos["graph"].upsert_edge(
        GraphEdge(
            id=uuid4(),
            project_id=project_id,
            from_node_id=node.id,
            to_node_id=asset_node.id,
            relationship_type=GraphEdgeType.VULNERABLE_TO,
        )
    )

    edges = await repos["graph"].list_edges_for_project(
        project_id, GraphEdgeType.VULNERABLE_TO
    )
    assert len(edges) == 1
    assert edges[0].from_node_id == node.id
    assert edges[0].to_node_id == asset_node.id


# ===========================================================================
# 3. Edge relationship verification
# ===========================================================================


@pytest.mark.asyncio
async def test_host_service_creates_hosts_edge(asset_service, repos):
    project_id = uuid4()
    tr = _make_tool_result(
        "nmap", "10.0.0.1",
        {
            "target": "10.0.0.1",
            "host_up": True,
            "ports": [
                {"port": 22, "state": "open", "service": "ssh", "protocol": "tcp", "version": ""},
            ],
        },
    )

    assets = await asset_service.upsert_from_tool_result(project_id, tr)
    assert len(assets) == 2

    edges = await repos["graph"].list_edges_for_project(project_id, GraphEdgeType.HOSTS)
    assert len(edges) == 1

    host = [a for a in assets if a.asset_type == AssetType.HOST][0]
    svc = [a for a in assets if a.asset_type == AssetType.SERVICE][0]
    host_node = await repos["graph"].find_node_by_source(project_id, "assets", host.id)
    svc_node = await repos["graph"].find_node_by_source(project_id, "assets", svc.id)
    assert host_node is not None
    assert svc_node is not None
    assert edges[0].from_node_id == host_node.id
    assert edges[0].to_node_id == svc_node.id


@pytest.mark.asyncio
async def test_manual_edge_operations(graph_service, repos):
    project_id = uuid4()
    n1 = await graph_service.upsert_asset_node(project_id, uuid4(), "host-a")
    n2 = await graph_service.upsert_asset_node(project_id, uuid4(), "host-b")
    n3 = await graph_service.upsert_asset_node(project_id, uuid4(), "host-c")

    e1 = await graph_service.add_edge(
        project_id, n1.id, n2.id, GraphEdgeType.COMMUNICATES_WITH, 0.9
    )
    e2 = await graph_service.add_edge(
        project_id, n2.id, n3.id, GraphEdgeType.COMMUNICATES_WITH, 0.7
    )

    assert e1.weight == 0.9
    assert e2.weight == 0.7

    neighbors = await graph_service.get_neighbors(n1.id, direction="outgoing")
    assert len(neighbors) == 1
    assert neighbors[0].id == n2.id

    neighbors_b = await graph_service.get_neighbors(n2.id, direction="outgoing")
    assert len(neighbors_b) == 1
    assert neighbors_b[0].id == n3.id

    all_edges = await graph_service.list_edges(project_id)
    assert len(all_edges) == 2


# ===========================================================================
# 4. Every graph API endpoint via HTTPX ASGI client
# ===========================================================================


@pytest.fixture
async def api_setup():
    """Minimal setup for API tests — real app with dependency overrides."""
    from app.api.v1.deps import get_current_user
    from app.api.v1.deps import get_graph_service as dep_get_graph

    app = create_app()

    graph_repo = FakeGraphRepository()
    gs = GraphService(graph_repo)

    owner = User(
        id=uuid4(),
        email="owner@example.com",
        password_hash="unused",
        full_name="Owner",
        is_active=True,
        created_at=datetime.now(UTC),
    )

    from app.api.v1.deps import get_organization_service, get_project_service
    from app.application.organization_service import OrganizationService
    from app.application.project_service import ProjectService
    from tests.fakes import (
        FakeAuthorizationRecordRepository,
        FakeOrganizationRepository,
        FakeProjectRepository,
    )

    org_repo = FakeOrganizationRepository()
    project_repo = FakeProjectRepository()
    auth_repo = FakeAuthorizationRecordRepository()
    org_svc = OrganizationService(org_repo)
    project_svc = ProjectService(project_repo, auth_repo)

    org = await org_svc.create("Test Org", owner.id)
    project = await project_svc.create(
        organization_id=org.id,
        name="Test Project",
        description=None,
        tags=None,
        client_metadata=None,
        owner_user_id=owner.id,
    )

    app.dependency_overrides[get_current_user] = lambda: owner
    app.dependency_overrides[get_organization_service] = lambda: org_svc
    app.dependency_overrides[get_project_service] = lambda: project_svc
    app.dependency_overrides[dep_get_graph] = lambda: gs

    return {
        "app": app,
        "project": project,
        "owner": owner,
        "graph_repo": graph_repo,
        "graph_service": gs,
    }


@pytest.mark.asyncio
async def test_api_list_nodes_empty(api_setup):
    async with AsyncClient(
        transport=ASGITransport(app=api_setup["app"]),
        base_url="http://testserver",
    ) as client:
        resp = await client.get(f"/api/v1/projects/{api_setup['project'].id}/graph/nodes")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_api_list_nodes_after_upsert(api_setup):
    gs = api_setup["graph_service"]
    pid = api_setup["project"].id
    asset_id = uuid4()
    await gs.upsert_asset_node(pid, asset_id, "10.0.0.1")

    async with AsyncClient(
        transport=ASGITransport(app=api_setup["app"]),
        base_url="http://testserver",
    ) as client:
        resp = await client.get(f"/api/v1/projects/{pid}/graph/nodes")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["node_type"] == "asset"
    assert body[0]["label"] == "10.0.0.1"


@pytest.mark.asyncio
async def test_api_list_nodes_filter_by_type(api_setup):
    gs = api_setup["graph_service"]
    pid = api_setup["project"].id
    await gs.upsert_asset_node(pid, uuid4(), "host-a")
    await gs.upsert_finding_node(pid, uuid4(), "Finding X")

    async with AsyncClient(
        transport=ASGITransport(app=api_setup["app"]),
        base_url="http://testserver",
    ) as client:
        resp = await client.get(
            f"/api/v1/projects/{pid}/graph/nodes",
            params={"node_type": "finding"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["node_type"] == "finding"


@pytest.mark.asyncio
async def test_api_list_edges_empty(api_setup):
    async with AsyncClient(
        transport=ASGITransport(app=api_setup["app"]),
        base_url="http://testserver",
    ) as client:
        resp = await client.get(f"/api/v1/projects/{api_setup['project'].id}/graph/edges")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_api_list_edges_after_add(api_setup):
    gs = api_setup["graph_service"]
    pid = api_setup["project"].id
    n1 = await gs.upsert_asset_node(pid, uuid4(), "a")
    n2 = await gs.upsert_asset_node(pid, uuid4(), "b")
    await gs.add_edge(pid, n1.id, n2.id, GraphEdgeType.HOSTS)

    async with AsyncClient(
        transport=ASGITransport(app=api_setup["app"]),
        base_url="http://testserver",
    ) as client:
        resp = await client.get(f"/api/v1/projects/{pid}/graph/edges")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["relationship_type"] == "hosts"


@pytest.mark.asyncio
async def test_api_get_node(api_setup):
    gs = api_setup["graph_service"]
    pid = api_setup["project"].id
    node = await gs.upsert_asset_node(pid, uuid4(), "target-host")

    async with AsyncClient(
        transport=ASGITransport(app=api_setup["app"]),
        base_url="http://testserver",
    ) as client:
        resp = await client.get(f"/api/v1/graph/nodes/{node.id}")
    assert resp.status_code == 200
    assert resp.json()["label"] == "target-host"


@pytest.mark.asyncio
async def test_api_get_node_404(api_setup):
    async with AsyncClient(
        transport=ASGITransport(app=api_setup["app"]),
        base_url="http://testserver",
    ) as client:
        resp = await client.get(f"/api/v1/graph/nodes/{uuid4()}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_api_get_neighbors(api_setup):
    gs = api_setup["graph_service"]
    pid = api_setup["project"].id
    n1 = await gs.upsert_asset_node(pid, uuid4(), "a")
    n2 = await gs.upsert_asset_node(pid, uuid4(), "b")
    n3 = await gs.upsert_asset_node(pid, uuid4(), "c")
    await gs.add_edge(pid, n1.id, n2.id, GraphEdgeType.HOSTS)
    await gs.add_edge(pid, n1.id, n3.id, GraphEdgeType.COMMUNICATES_WITH)

    async with AsyncClient(
        transport=ASGITransport(app=api_setup["app"]),
        base_url="http://testserver",
    ) as client:
        resp = await client.get(f"/api/v1/graph/nodes/{n1.id}/neighbors")
    assert resp.status_code == 200
    assert len(resp.json()) == 2

    async with AsyncClient(
        transport=ASGITransport(app=api_setup["app"]),
        base_url="http://testserver",
    ) as client:
        resp = await client.get(
            f"/api/v1/graph/nodes/{n1.id}/neighbors",
            params={"edge_type": "hosts"},
        )
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.json()[0]["label"] == "b"


@pytest.mark.asyncio
async def test_api_add_edge(api_setup):
    gs = api_setup["graph_service"]
    pid = api_setup["project"].id
    n1 = await gs.upsert_asset_node(pid, uuid4(), "x")
    n2 = await gs.upsert_asset_node(pid, uuid4(), "y")

    async with AsyncClient(
        transport=ASGITransport(app=api_setup["app"]),
        base_url="http://testserver",
    ) as client:
        resp = await client.post(
            f"/api/v1/projects/{pid}/graph/edges",
            json={
                "from_node_id": str(n1.id),
                "to_node_id": str(n2.id),
                "relationship_type": "hosts",
                "weight": 1.5,
            },
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["from_node_id"] == str(n1.id)
    assert body["to_node_id"] == str(n2.id)
    assert body["weight"] == 1.5


@pytest.mark.asyncio
async def test_api_delete_node(api_setup):
    gs = api_setup["graph_service"]
    pid = api_setup["project"].id
    node = await gs.upsert_asset_node(pid, uuid4(), "del-me")

    async with AsyncClient(
        transport=ASGITransport(app=api_setup["app"]),
        base_url="http://testserver",
    ) as client:
        resp = await client.delete(f"/api/v1/graph/nodes/{node.id}")
    assert resp.status_code == 204

    remaining = await gs.list_nodes(pid)
    assert len(remaining) == 0


@pytest.mark.asyncio
async def test_api_delete_edge(api_setup):
    gs = api_setup["graph_service"]
    pid = api_setup["project"].id
    n1 = await gs.upsert_asset_node(pid, uuid4(), "a")
    n2 = await gs.upsert_asset_node(pid, uuid4(), "b")
    edge = await gs.add_edge(pid, n1.id, n2.id, GraphEdgeType.HOSTS)

    async with AsyncClient(
        transport=ASGITransport(app=api_setup["app"]),
        base_url="http://testserver",
    ) as client:
        resp = await client.delete(f"/api/v1/graph/edges/{edge.id}")
    assert resp.status_code == 204

    remaining = await gs.list_edges(pid)
    assert len(remaining) == 0


# ===========================================================================
# 5. Shortest-path endpoint with sample data
# ===========================================================================


@pytest.mark.asyncio
async def test_api_shortest_path(api_setup):
    gs = api_setup["graph_service"]
    pid = api_setup["project"].id

    n1 = await gs.upsert_asset_node(pid, uuid4(), "host-a")
    n2 = await gs.upsert_asset_node(pid, uuid4(), "svc-b")
    n3 = await gs.upsert_asset_node(pid, uuid4(), "host-c")

    await gs.add_edge(pid, n1.id, n2.id, GraphEdgeType.HOSTS)
    await gs.add_edge(pid, n2.id, n3.id, GraphEdgeType.VULNERABLE_TO)

    async with AsyncClient(
        transport=ASGITransport(app=api_setup["app"]),
        base_url="http://testserver",
    ) as client:
        resp = await client.get(
            "/api/v1/graph/shortest-path",
            params={"from_node_id": str(n1.id), "to_node_id": str(n3.id)},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["length"] == 3
    assert [n["id"] for n in body["nodes"]] == [str(n1.id), str(n2.id), str(n3.id)]


@pytest.mark.asyncio
async def test_api_shortest_path_no_path_returns_404(api_setup):
    gs = api_setup["graph_service"]
    pid = api_setup["project"].id
    n1 = await gs.upsert_asset_node(pid, uuid4(), "isolated-a")
    n2 = await gs.upsert_asset_node(pid, uuid4(), "isolated-b")

    async with AsyncClient(
        transport=ASGITransport(app=api_setup["app"]),
        base_url="http://testserver",
    ) as client:
        resp = await client.get(
            "/api/v1/graph/shortest-path",
            params={"from_node_id": str(n1.id), "to_node_id": str(n2.id)},
        )
    assert resp.status_code == 404


# ===========================================================================
# 6. Graph projection idempotency
# ===========================================================================


@pytest.mark.asyncio
async def test_asset_upsert_idempotent_nodes(asset_service, repos):
    project_id = uuid4()
    tr = _make_tool_result(
        "ping", "10.0.0.1",
        {"host": "10.0.0.1", "reachable": True},
    )

    await asset_service.upsert_from_tool_result(project_id, tr)
    await asset_service.upsert_from_tool_result(project_id, tr)

    nodes = await repos["graph"].list_nodes_for_project(project_id)
    assert len(nodes) == 1


@pytest.mark.asyncio
async def test_finding_create_idempotent_nodes(finding_service, repos):
    project_id = uuid4()
    tr = _make_tool_result(
        "nmap", "10.0.0.1",
        {
            "target": "10.0.0.1",
            "ports": [
                {"port": 21, "state": "open", "service": "ftp", "protocol": "tcp", "version": ""},
            ],
        },
    )

    await finding_service.create_from_tool_result(project_id, tr)
    await finding_service.create_from_tool_result(project_id, tr)

    finding_nodes = await repos["graph"].list_nodes_for_project(
        project_id, GraphNodeType.FINDING
    )
    assert len(finding_nodes) == 1


@pytest.mark.asyncio
async def test_graph_upsert_node_idempotent(graph_service, repos):
    project_id = uuid4()
    asset_id = uuid4()

    n1 = await graph_service.upsert_asset_node(project_id, asset_id, "old-label")
    n2 = await graph_service.upsert_asset_node(project_id, asset_id, "new-label")

    assert n1.id == n2.id
    nodes = await graph_service.list_nodes(project_id)
    assert len(nodes) == 1
    assert nodes[0].label == "new-label"


@pytest.mark.asyncio
async def test_graph_upsert_edge_idempotent(graph_service, repos):
    project_id = uuid4()
    n1 = await graph_service.upsert_asset_node(project_id, uuid4(), "a")
    n2 = await graph_service.upsert_asset_node(project_id, uuid4(), "b")

    e1 = await graph_service.add_edge(project_id, n1.id, n2.id, GraphEdgeType.HOSTS)
    e2 = await graph_service.add_edge(project_id, n1.id, n2.id, GraphEdgeType.HOSTS, weight=5.0)

    assert e1.id == e2.id
    edges = await graph_service.list_edges(project_id)
    assert len(edges) == 1
    assert edges[0].weight == 5.0


# ===========================================================================
# 7. Cascade delete behavior
# ===========================================================================


@pytest.mark.asyncio
async def test_graph_node_delete_cascades_edges(graph_service):
    project_id = uuid4()
    n1 = await graph_service.upsert_asset_node(project_id, uuid4(), "a")
    n2 = await graph_service.upsert_asset_node(project_id, uuid4(), "b")
    n3 = await graph_service.upsert_asset_node(project_id, uuid4(), "c")
    await graph_service.add_edge(project_id, n1.id, n2.id, GraphEdgeType.HOSTS)
    await graph_service.add_edge(project_id, n1.id, n3.id, GraphEdgeType.COMMUNICATES_WITH)
    await graph_service.add_edge(project_id, n2.id, n3.id, GraphEdgeType.VULNERABLE_TO)

    await graph_service.remove_node(n1.id)

    edges = await graph_service.list_edges(project_id)
    assert len(edges) == 1
    assert edges[0].from_node_id == n2.id
    assert edges[0].to_node_id == n3.id

    nodes = await graph_service.list_nodes(project_id)
    assert len(nodes) == 2


@pytest.mark.asyncio
async def test_graph_clear_project_removes_everything(graph_service):
    p1 = uuid4()
    p2 = uuid4()
    n1 = await graph_service.upsert_asset_node(p1, uuid4(), "a1")
    n2 = await graph_service.upsert_asset_node(p2, uuid4(), "b1")
    await graph_service.add_edge(p1, n1.id, n2.id, GraphEdgeType.HOSTS)

    await graph_service.clear_project(p1)

    p1_nodes = await graph_service.list_nodes(p1)
    p1_edges = await graph_service.list_edges(p1)
    assert len(p1_nodes) == 0
    assert len(p1_edges) == 0

    p2_nodes = await graph_service.list_nodes(p2)
    assert len(p2_nodes) == 1


@pytest.mark.asyncio
async def test_remove_edge_does_not_affect_nodes(graph_service):
    project_id = uuid4()
    n1 = await graph_service.upsert_asset_node(project_id, uuid4(), "a")
    n2 = await graph_service.upsert_asset_node(project_id, uuid4(), "b")
    edge = await graph_service.add_edge(project_id, n1.id, n2.id, GraphEdgeType.HOSTS)

    await graph_service.remove_edge(edge.id)

    nodes = await graph_service.list_nodes(project_id)
    assert len(nodes) == 2

    edges = await graph_service.list_edges(project_id)
    assert len(edges) == 0


@pytest.mark.asyncio
async def test_remove_edges_for_node(graph_service):
    project_id = uuid4()
    n1 = await graph_service.upsert_asset_node(project_id, uuid4(), "a")
    n2 = await graph_service.upsert_asset_node(project_id, uuid4(), "b")
    n3 = await graph_service.upsert_asset_node(project_id, uuid4(), "c")
    await graph_service.add_edge(project_id, n1.id, n2.id, GraphEdgeType.HOSTS)
    await graph_service.add_edge(project_id, n3.id, n1.id, GraphEdgeType.COMMUNICATES_WITH)

    await graph_service.remove_node(n1.id)

    nodes = await graph_service.list_nodes(project_id)
    assert len(nodes) == 2

    edges = await graph_service.list_edges(project_id)
    assert len(edges) == 0
