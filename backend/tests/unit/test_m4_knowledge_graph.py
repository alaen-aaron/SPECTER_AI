"""Tests for Milestone 4 — Knowledge Graph Foundation.

Covers:
  - GraphProjector (project_asset, project_finding, project_evidence, rebuild)
  - GraphService (blast_radius, finding_relationships, graph_summary)
  - FakeGraphRepository.blast_radius (BFS traversal)
  - Idempotency of projection and rebuild
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.application.graph_projector import GraphProjector
from app.application.graph_service import GraphService
from app.domain.entities import Asset, Evidence, Finding, GraphNode
from app.domain.value_objects import (
    AssetType,
    EvidenceType,
    FindingStatus,
    GraphEdgeType,
    GraphNodeType,
    Severity,
)
from tests.fakes import (
    FakeAssetRepository,
    FakeEvidenceRepository,
    FakeFindingRepository,
    FakeGraphRepository,
)

NOW = datetime.now(UTC)


def _make_asset(
    project_id: uuid4,
    *,
    asset_type: AssetType = AssetType.HOST,
    value: str = "192.168.1.1",
    asset_id: uuid4 | None = None,
) -> Asset:
    aid = asset_id or uuid4()
    return Asset(
        id=aid,
        project_id=project_id,
        asset_type=asset_type,
        value=value,
        first_seen=NOW,
        last_seen=NOW,
        in_scope=True,
        source_scan_id=uuid4(),
        metadata={},
        created_at=NOW,
    )


def _make_finding(
    project_id: uuid4,
    *,
    title: str = "Open SSH port",
    severity: Severity = Severity.HIGH,
    finding_id: uuid4 | None = None,
    description: str | None = None,
) -> Finding:
    fid = finding_id or uuid4()
    return Finding(
        id=fid,
        project_id=project_id,
        title=title,
        severity=severity,
        status=FindingStatus.OPEN,
        description=description,
        asset_id=None,
        cvss_score=8.5,
        dedup_key=f"dedup-{fid}",
        tool_result_ids=[],
        created_at=NOW,
    )


def _make_evidence(
    finding_id: uuid4,
    *,
    evidence_type: EvidenceType = EvidenceType.SCREENSHOT,
    evidence_id: uuid4 | None = None,
    filename: str | None = "proof.png",
) -> Evidence:
    eid = evidence_id or uuid4()
    return Evidence(
        id=eid,
        finding_id=finding_id,
        evidence_type=evidence_type,
        storage_pointer="/storage/proof.png",
        content_hash="abc123",
        collected_by=uuid4(),
        collected_at=NOW,
        filename=filename,
        file_size=1024,
        created_at=NOW,
    )


# ======================================================================
# GraphProjector tests
# ======================================================================


class TestGraphProjector:
    @pytest.fixture
    def repos(self):
        graph = FakeGraphRepository()
        assets = FakeAssetRepository()
        findings = FakeFindingRepository()
        evidence = FakeEvidenceRepository()
        evidence.set_findings(findings)
        return graph, assets, findings, evidence

    @pytest.fixture
    def projector(self, repos):
        graph, assets, findings, evidence = repos
        return GraphProjector(graph, assets, findings, evidence)

    @pytest.mark.asyncio
    async def test_project_asset_creates_node(self, projector, repos):
        graph, _, _, _ = repos
        project_id = uuid4()
        asset = _make_asset(project_id)

        node = await projector.project_asset(asset)

        assert node.project_id == project_id
        assert node.node_type == GraphNodeType.ASSET
        assert node.source_table == "assets"
        assert node.source_id == asset.id
        assert node.label == asset.value
        assert node.properties["asset_type"] == "host"
        assert node.properties["in_scope"] is True

    @pytest.mark.asyncio
    async def test_project_asset_idempotent(self, projector, repos):
        graph, _, _, _ = repos
        project_id = uuid4()
        asset = _make_asset(project_id)

        n1 = await projector.project_asset(asset)
        n2 = await projector.project_asset(asset)

        assert n1.id == n2.id
        assert len(graph._nodes) == 1

    @pytest.mark.asyncio
    async def test_project_asset_technology_type(self, projector):
        project_id = uuid4()
        asset = _make_asset(project_id, asset_type=AssetType.TECHNOLOGY, value="nginx")

        node = await projector.project_asset(asset)

        assert node.node_type == GraphNodeType.TECHNOLOGY

    @pytest.mark.asyncio
    async def test_project_asset_credential_type(self, projector):
        project_id = uuid4()
        asset = _make_asset(
            project_id, asset_type=AssetType.CREDENTIAL, value="admin:pass123"
        )

        node = await projector.project_asset(asset)

        assert node.node_type == GraphNodeType.CREDENTIAL

    @pytest.mark.asyncio
    async def test_project_finding_creates_node(self, projector, repos):
        graph, _, _, _ = repos
        project_id = uuid4()
        finding = _make_finding(project_id, title="SQL Injection in /api")

        node = await projector.project_finding(finding)

        assert node.project_id == project_id
        assert node.node_type == GraphNodeType.FINDING
        assert node.source_table == "findings"
        assert node.source_id == finding.id
        assert node.label == "SQL Injection in /api"
        assert node.properties["severity"] == "high"
        assert node.properties["cvss_score"] == 8.5

    @pytest.mark.asyncio
    async def test_project_finding_idempotent(self, projector, repos):
        graph, _, _, _ = repos
        project_id = uuid4()
        finding = _make_finding(project_id)

        n1 = await projector.project_finding(finding)
        n2 = await projector.project_finding(finding)

        assert n1.id == n2.id
        finding_nodes = [n for n in graph._nodes.values() if n.node_type == GraphNodeType.FINDING]
        assert len(finding_nodes) == 1

    @pytest.mark.asyncio
    async def test_project_finding_wires_vulnerable_to_edge(self, projector, repos):
        graph, assets, _, _ = repos
        project_id = uuid4()
        asset = _make_asset(project_id, value="web-server-01")
        await assets.upsert(asset)
        await projector.project_asset(asset)

        finding = _make_finding(
            project_id,
            title="Vulnerability in web-server-01",
            description="Critical issue found",
        )

        node = await projector.project_finding(finding)

        vulnerable_edges = [
            e
            for e in graph._edges.values()
            if e.relationship_type == GraphEdgeType.VULNERABLE_TO
        ]
        assert len(vulnerable_edges) == 1
        assert vulnerable_edges[0].from_node_id == node.id

    @pytest.mark.asyncio
    async def test_project_finding_no_edge_if_no_asset_match(self, projector, repos):
        graph, assets, _, _ = repos
        project_id = uuid4()
        asset = _make_asset(project_id, value="db-server-01")
        await assets.upsert(asset)

        finding = _make_finding(
            project_id,
            title="Vulnerability in web-server-01",
            description="Unrelated",
        )
        await projector.project_finding(finding)

        vulnerable_edges = [
            e
            for e in graph._edges.values()
            if e.relationship_type == GraphEdgeType.VULNERABLE_TO
        ]
        assert len(vulnerable_edges) == 0

    @pytest.mark.asyncio
    async def test_project_evidence_creates_node(self, projector, repos):
        graph, _, findings, _ = repos
        project_id = uuid4()
        finding = _make_finding(project_id)
        await findings.add(finding)

        evidence = _make_evidence(finding.id)

        node = await projector.project_evidence(evidence, finding)

        assert node.project_id == project_id
        assert node.node_type == GraphNodeType.EVIDENCE
        assert node.source_table == "evidence"
        assert node.label == "proof.png"
        assert node.properties["evidence_type"] == "screenshot"

    @pytest.mark.asyncio
    async def test_project_evidence_wires_evidenced_by_edge(self, projector, repos):
        graph, _, findings, _ = repos
        project_id = uuid4()
        finding = _make_finding(project_id)
        await findings.add(finding)

        evidence = _make_evidence(finding.id)

        finding_node = await projector.project_finding(finding)
        evidence_node = await projector.project_evidence(evidence, finding)

        evidenced_edges = [
            e
            for e in graph._edges.values()
            if e.relationship_type == GraphEdgeType.EVIDENCED_BY
        ]
        assert len(evidenced_edges) == 1
        assert evidenced_edges[0].from_node_id == evidence_node.id
        assert evidenced_edges[0].to_node_id == finding_node.id

    @pytest.mark.asyncio
    async def test_project_evidence_idempotent(self, projector, repos):
        graph, _, findings, _ = repos
        project_id = uuid4()
        finding = _make_finding(project_id)
        await findings.add(finding)

        evidence = _make_evidence(finding.id)

        n1 = await projector.project_evidence(evidence, finding)
        n2 = await projector.project_evidence(evidence, finding)

        assert n1.id == n2.id
        evidence_nodes = [
            n for n in graph._nodes.values() if n.node_type == GraphNodeType.EVIDENCE
        ]
        assert len(evidence_nodes) == 1

    @pytest.mark.asyncio
    async def test_rebuild_graph_from_scratch(self, projector, repos):
        graph, assets, findings, evidence = repos
        project_id = uuid4()

        asset = _make_asset(project_id, value="target-host")
        await assets.upsert(asset)
        finding = _make_finding(project_id, title="Issue in target-host")
        await findings.add(finding)
        ev = _make_evidence(finding.id)
        await evidence.add(ev)

        counts = await projector.rebuild_graph_from_scratch(project_id)

        assert counts["nodes"] == 3
        assert counts["edges"] >= 1

        all_nodes = await graph.list_nodes_for_project(project_id)
        assert len(all_nodes) == 3
        types = {n.node_type for n in all_nodes}
        assert types == {GraphNodeType.ASSET, GraphNodeType.FINDING, GraphNodeType.EVIDENCE}

    @pytest.mark.asyncio
    async def test_rebuild_is_idempotent(self, projector, repos):
        graph, assets, findings, evidence = repos
        project_id = uuid4()

        asset = _make_asset(project_id, value="host-a")
        await assets.upsert(asset)
        finding = _make_finding(project_id)
        await findings.add(finding)

        c1 = await projector.rebuild_graph_from_scratch(project_id)
        c2 = await projector.rebuild_graph_from_scratch(project_id)

        assert c1["nodes"] == c2["nodes"]
        assert c1["edges"] == c2["edges"]
        nodes = await graph.list_nodes_for_project(project_id)
        assert len(nodes) == 2

    @pytest.mark.asyncio
    async def test_rebuild_clears_old_graph(self, projector, repos):
        graph, assets, _, _ = repos
        project_id = uuid4()

        n = await graph.upsert_node(
            GraphNode(
                id=uuid4(),
                project_id=project_id,
                node_type=GraphNodeType.ASSET,
                source_table="assets",
                source_id=uuid4(),
                label="stale-node",
            )
        )

        asset = _make_asset(project_id, value="fresh-host")
        await assets.upsert(asset)
        await projector.rebuild_graph_from_scratch(project_id)

        old_node = await graph.get_node(n.id)
        assert old_node is None

    @pytest.mark.asyncio
    async def test_rebuild_empty_project(self, projector, repos):
        graph, _, _, _ = repos
        project_id = uuid4()

        counts = await projector.rebuild_graph_from_scratch(project_id)

        assert counts["nodes"] == 0
        assert counts["edges"] == 0


# ======================================================================
# GraphService — blast_radius, finding_relationships, graph_summary
# ======================================================================


class TestGraphServiceBlastRadius:
    @pytest.fixture
    def service(self):
        return GraphService(FakeGraphRepository())

    @pytest.mark.asyncio
    async def test_blast_radius_returns_reachable_nodes(self, service):
        project_id = uuid4()
        a = await service.upsert_asset_node(project_id, uuid4(), "a")
        b = await service.upsert_asset_node(project_id, uuid4(), "b")
        c = await service.upsert_asset_node(project_id, uuid4(), "c")
        d = await service.upsert_asset_node(project_id, uuid4(), "d")

        await service.add_edge(project_id, a.id, b.id, GraphEdgeType.HOSTS)
        await service.add_edge(project_id, b.id, c.id, GraphEdgeType.HOSTS)
        await service.add_edge(project_id, a.id, d.id, GraphEdgeType.COMMUNICATES_WITH)

        reachable = await service.blast_radius(project_id, a.id)
        reachable_ids = {n.id for n in reachable}

        assert reachable_ids == {b.id, c.id, d.id}

    @pytest.mark.asyncio
    async def test_blast_radius_respects_max_depth(self, service):
        project_id = uuid4()
        a = await service.upsert_asset_node(project_id, uuid4(), "a")
        b = await service.upsert_asset_node(project_id, uuid4(), "b")
        c = await service.upsert_asset_node(project_id, uuid4(), "c")

        await service.add_edge(project_id, a.id, b.id, GraphEdgeType.HOSTS)
        await service.add_edge(project_id, b.id, c.id, GraphEdgeType.HOSTS)

        reachable = await service.blast_radius(project_id, a.id, max_depth=1)
        reachable_ids = {n.id for n in reachable}

        assert b.id in reachable_ids
        assert c.id not in reachable_ids

    @pytest.mark.asyncio
    async def test_blast_radius_excludes_start_node(self, service):
        project_id = uuid4()
        a = await service.upsert_asset_node(project_id, uuid4(), "a")
        b = await service.upsert_asset_node(project_id, uuid4(), "b")
        await service.add_edge(project_id, a.id, b.id, GraphEdgeType.HOSTS)

        reachable = await service.blast_radius(project_id, a.id)
        reachable_ids = {n.id for n in reachable}

        assert a.id not in reachable_ids

    @pytest.mark.asyncio
    async def test_blast_radius_empty_graph(self, service):
        project_id = uuid4()
        node = await service.upsert_asset_node(project_id, uuid4(), "lonely")

        reachable = await service.blast_radius(project_id, node.id)
        assert reachable == []

    @pytest.mark.asyncio
    async def test_blast_radius_diamond_shape(self, service):
        project_id = uuid4()
        a = await service.upsert_asset_node(project_id, uuid4(), "a")
        b = await service.upsert_asset_node(project_id, uuid4(), "b")
        c = await service.upsert_asset_node(project_id, uuid4(), "c")
        d = await service.upsert_asset_node(project_id, uuid4(), "d")

        await service.add_edge(project_id, a.id, b.id, GraphEdgeType.HOSTS)
        await service.add_edge(project_id, a.id, c.id, GraphEdgeType.HOSTS)
        await service.add_edge(project_id, b.id, d.id, GraphEdgeType.COMMUNICATES_WITH)
        await service.add_edge(project_id, c.id, d.id, GraphEdgeType.COMMUNICATES_WITH)

        reachable = await service.blast_radius(project_id, a.id)
        reachable_ids = {n.id for n in reachable}

        assert reachable_ids == {b.id, c.id, d.id}


class TestGraphServiceFindingRelationships:
    @pytest.fixture
    def service(self):
        return GraphService(FakeGraphRepository())

    @pytest.mark.asyncio
    async def test_finding_relationships_outgoing(self, service):
        project_id = uuid4()
        finding_node = await service.upsert_finding_node(
            project_id, uuid4(), "XSS finding"
        )
        asset = await service.upsert_asset_node(project_id, uuid4(), "target-host")
        await service.add_edge(
            project_id, finding_node.id, asset.id, GraphEdgeType.VULNERABLE_TO
        )

        finding_source_id = finding_node.source_id
        result = await service.finding_relationships(project_id, finding_source_id)

        assert len(result["outgoing"]) == 1
        assert result["outgoing"][0].id == asset.id
        assert len(result["incoming"]) == 0

    @pytest.mark.asyncio
    async def test_finding_relationships_incoming(self, service):
        project_id = uuid4()
        finding_node = await service.upsert_finding_node(
            project_id, uuid4(), "Evidence-linked"
        )
        evidence_node = await service.upsert_evidence_node(
            project_id, uuid4(), "pcap-file"
        )
        await service.add_edge(
            project_id, evidence_node.id, finding_node.id, GraphEdgeType.EVIDENCED_BY
        )

        finding_source_id = finding_node.source_id
        result = await service.finding_relationships(project_id, finding_source_id)

        assert len(result["incoming"]) == 1
        assert result["incoming"][0].id == evidence_node.id
        assert len(result["outgoing"]) == 0

    @pytest.mark.asyncio
    async def test_finding_relationships_nonexistent(self, service):
        project_id = uuid4()
        result = await service.finding_relationships(project_id, uuid4())
        assert result == {"outgoing": [], "incoming": []}


class TestGraphServiceSummary:
    @pytest.fixture
    def service(self):
        return GraphService(FakeGraphRepository())

    @pytest.mark.asyncio
    async def test_graph_summary_empty(self, service):
        project_id = uuid4()
        summary = await service.graph_summary(project_id)

        assert summary["total_nodes"] == 0
        assert summary["total_edges"] == 0
        assert summary["nodes_by_type"] == {}
        assert summary["edges_by_type"] == {}

    @pytest.mark.asyncio
    async def test_graph_summary_counts_by_type(self, service):
        project_id = uuid4()
        await service.upsert_asset_node(project_id, uuid4(), "host-a")
        await service.upsert_asset_node(project_id, uuid4(), "host-b")
        await service.upsert_finding_node(project_id, uuid4(), "XSS")

        n1 = (await service.list_nodes(project_id))[0]
        n2 = (await service.list_nodes(project_id))[1]
        await service.add_edge(project_id, n1.id, n2.id, GraphEdgeType.HOSTS)

        summary = await service.graph_summary(project_id)

        assert summary["total_nodes"] == 3
        assert summary["total_edges"] == 1
        assert summary["nodes_by_type"]["asset"] == 2
        assert summary["nodes_by_type"]["finding"] == 1
        assert summary["edges_by_type"]["hosts"] == 1

    @pytest.mark.asyncio
    async def test_graph_summary_scoped_to_project(self, service):
        p1 = uuid4()
        p2 = uuid4()
        await service.upsert_asset_node(p1, uuid4(), "a1")
        await service.upsert_asset_node(p2, uuid4(), "b1")

        s1 = await service.graph_summary(p1)
        s2 = await service.graph_summary(p2)

        assert s1["total_nodes"] == 1
        assert s2["total_nodes"] == 1
        assert s1["project_id"] == str(p1)
