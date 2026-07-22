from __future__ import annotations

from uuid import uuid4

import pytest

from app.application.graph_service import GraphService
from app.domain.exceptions import GraphNodeNotFoundError
from app.domain.value_objects import GraphEdgeType, GraphNodeType
from tests.fakes import FakeGraphRepository


@pytest.fixture
def repo():
    return FakeGraphRepository()


@pytest.fixture
def service(repo):
    return GraphService(repo)


@pytest.mark.asyncio
async def test_upsert_asset_node(service, repo):
    project_id = uuid4()
    asset_id = uuid4()

    node = await service.upsert_asset_node(project_id, asset_id, "192.168.1.1", ip="192.168.1.1")

    assert node.project_id == project_id
    assert node.node_type == GraphNodeType.ASSET
    assert node.source_table == "assets"
    assert node.source_id == asset_id
    assert node.label == "192.168.1.1"
    assert node.properties == {"ip": "192.168.1.1"}


@pytest.mark.asyncio
async def test_upsert_asset_node_idempotent(service, repo):
    project_id = uuid4()
    asset_id = uuid4()

    n1 = await service.upsert_asset_node(project_id, asset_id, "old-label")
    n2 = await service.upsert_asset_node(project_id, asset_id, "new-label")

    assert n1.id == n2.id
    assert n2.label == "new-label"


@pytest.mark.asyncio
async def test_upsert_finding_node(service):
    project_id = uuid4()
    finding_id = uuid4()

    node = await service.upsert_finding_node(project_id, finding_id, "XSS in /login")

    assert node.node_type == GraphNodeType.FINDING
    assert node.source_table == "findings"
    assert node.source_id == finding_id


@pytest.mark.asyncio
async def test_add_edge(service):
    project_id = uuid4()
    n1 = await service.upsert_asset_node(project_id, uuid4(), "host-a")
    n2 = await service.upsert_asset_node(project_id, uuid4(), "host-b")

    edge = await service.add_edge(
        project_id, n1.id, n2.id, GraphEdgeType.COMMUNICATES_WITH, 0.8
    )

    assert edge.from_node_id == n1.id
    assert edge.to_node_id == n2.id
    assert edge.relationship_type == GraphEdgeType.COMMUNICATES_WITH
    assert edge.weight == 0.8


@pytest.mark.asyncio
async def test_add_edge_idempotent(service):
    project_id = uuid4()
    n1 = await service.upsert_asset_node(project_id, uuid4(), "a")
    n2 = await service.upsert_asset_node(project_id, uuid4(), "b")

    e1 = await service.add_edge(project_id, n1.id, n2.id, GraphEdgeType.HOSTS)
    e2 = await service.add_edge(project_id, n1.id, n2.id, GraphEdgeType.HOSTS, weight=2.0)

    assert e1.id == e2.id
    assert e2.weight == 2.0


@pytest.mark.asyncio
async def test_get_node_raises_not_found(service):
    with pytest.raises(GraphNodeNotFoundError):
        await service.get_node(uuid4())


@pytest.mark.asyncio
async def test_get_node_returns_node(service):
    project_id = uuid4()
    node = await service.upsert_asset_node(project_id, uuid4(), "target")

    result = await service.get_node(node.id)
    assert result.id == node.id


@pytest.mark.asyncio
async def test_get_neighbors(service):
    project_id = uuid4()
    n1 = await service.upsert_asset_node(project_id, uuid4(), "a")
    n2 = await service.upsert_asset_node(project_id, uuid4(), "b")
    n3 = await service.upsert_asset_node(project_id, uuid4(), "c")
    await service.add_edge(project_id, n1.id, n2.id, GraphEdgeType.HOSTS)
    await service.add_edge(project_id, n1.id, n3.id, GraphEdgeType.HOSTS)
    await service.add_edge(project_id, n2.id, n3.id, GraphEdgeType.COMMUNICATES_WITH)

    outgoing = await service.get_neighbors(n1.id, direction="outgoing")
    assert len(outgoing) == 2
    outgoing_ids = {n.id for n in outgoing}
    assert outgoing_ids == {n2.id, n3.id}


@pytest.mark.asyncio
async def test_get_neighbors_filtered_by_edge_type(service):
    project_id = uuid4()
    n1 = await service.upsert_asset_node(project_id, uuid4(), "a")
    n2 = await service.upsert_asset_node(project_id, uuid4(), "b")
    n3 = await service.upsert_asset_node(project_id, uuid4(), "c")
    await service.add_edge(project_id, n1.id, n2.id, GraphEdgeType.HOSTS)
    await service.add_edge(project_id, n1.id, n3.id, GraphEdgeType.COMMUNICATES_WITH)

    hosts = await service.get_neighbors(n1.id, edge_type=GraphEdgeType.HOSTS)
    assert len(hosts) == 1
    assert hosts[0].id == n2.id


@pytest.mark.asyncio
async def test_shortest_path(service):
    project_id = uuid4()
    n1 = await service.upsert_asset_node(project_id, uuid4(), "a")
    n2 = await service.upsert_asset_node(project_id, uuid4(), "b")
    n3 = await service.upsert_asset_node(project_id, uuid4(), "c")
    await service.add_edge(project_id, n1.id, n2.id, GraphEdgeType.HOSTS)
    await service.add_edge(project_id, n2.id, n3.id, GraphEdgeType.HOSTS)

    path = await service.shortest_path(n1.id, n3.id)
    assert path is not None
    assert len(path) == 3
    assert [n.id for n in path] == [n1.id, n2.id, n3.id]


@pytest.mark.asyncio
async def test_shortest_path_no_path(service):
    project_id = uuid4()
    n1 = await service.upsert_asset_node(project_id, uuid4(), "a")
    n2 = await service.upsert_asset_node(project_id, uuid4(), "b")

    path = await service.shortest_path(n1.id, n2.id)
    assert path is None


@pytest.mark.asyncio
async def test_list_nodes(service):
    project_id = uuid4()
    await service.upsert_asset_node(project_id, uuid4(), "a")
    await service.upsert_asset_node(project_id, uuid4(), "b")
    await service.upsert_finding_node(project_id, uuid4(), "finding-1")

    all_nodes = await service.list_nodes(project_id)
    assert len(all_nodes) == 3

    asset_nodes = await service.list_nodes(project_id, GraphNodeType.ASSET)
    assert len(asset_nodes) == 2

    finding_nodes = await service.list_nodes(project_id, GraphNodeType.FINDING)
    assert len(finding_nodes) == 1


@pytest.mark.asyncio
async def test_list_edges(service):
    project_id = uuid4()
    n1 = await service.upsert_asset_node(project_id, uuid4(), "a")
    n2 = await service.upsert_asset_node(project_id, uuid4(), "b")
    n3 = await service.upsert_asset_node(project_id, uuid4(), "c")
    await service.add_edge(project_id, n1.id, n2.id, GraphEdgeType.HOSTS)
    await service.add_edge(project_id, n2.id, n3.id, GraphEdgeType.COMMUNICATES_WITH)

    all_edges = await service.list_edges(project_id)
    assert len(all_edges) == 2

    hosts = await service.list_edges(project_id, GraphEdgeType.HOSTS)
    assert len(hosts) == 1


@pytest.mark.asyncio
async def test_remove_node_cascades_edges(service, repo):
    project_id = uuid4()
    n1 = await service.upsert_asset_node(project_id, uuid4(), "a")
    n2 = await service.upsert_asset_node(project_id, uuid4(), "b")
    await service.add_edge(project_id, n1.id, n2.id, GraphEdgeType.HOSTS)

    await service.remove_node(n1.id)

    assert await repo.get_node(n1.id) is None
    edges = await service.list_edges(project_id)
    assert len(edges) == 0


@pytest.mark.asyncio
async def test_remove_edge(service):
    project_id = uuid4()
    n1 = await service.upsert_asset_node(project_id, uuid4(), "a")
    n2 = await service.upsert_asset_node(project_id, uuid4(), "b")
    edge = await service.add_edge(project_id, n1.id, n2.id, GraphEdgeType.HOSTS)

    await service.remove_edge(edge.id)

    edges = await service.list_edges(project_id)
    assert len(edges) == 0


@pytest.mark.asyncio
async def test_clear_project(service):
    p1 = uuid4()
    p2 = uuid4()
    await service.upsert_asset_node(p1, uuid4(), "a1")
    await service.upsert_asset_node(p2, uuid4(), "b1")

    await service.clear_project(p1)

    p1_nodes = await service.list_nodes(p1)
    p2_nodes = await service.list_nodes(p2)
    assert len(p1_nodes) == 0
    assert len(p2_nodes) == 1
