"""Tests for Milestone 4.5 — Knowledge Graph Intelligence Integration.

Covers:
  - AttackPathService (shortest path, multiple paths, reachable, crown jewel, lateral movement)
  - ImpactAnalysisService (analyze, analyze_asset, analyze_finding, blast radius, confidence)
  - HistoricalIntelligenceService (asset delta, finding trends, recurring, tech changes)
  - ExecutiveIntelligenceService (risk assets, connected, surface, tech exposure, chains)
  - Graph-integrated PlannerService (graph-enriched suggestions)
  - Graph-integrated AnalyzerService (graph-based correlations)
  - Graph-integrated AIReporterService (graph context in narratives)
  - Graph-integrated ExplainerService (graph context in explanations)
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.application.ai_reporter_service import AIReporterService
from app.application.analyzer_service import AnalyzerService
from app.application.attack_path_service import AttackPathService
from app.application.executive_intelligence_service import ExecutiveIntelligenceService
from app.application.explainer_service import ExplainerService
from app.application.historical_intelligence_service import HistoricalIntelligenceService
from app.application.impact_analysis_service import ImpactAnalysisService
from app.application.planner_service import PlannerService
from app.domain.entities import (
    Asset,
    Finding,
    GraphEdge,
    GraphNode,
    Scan,
)
from app.domain.value_objects import (
    AssetType,
    FindingStatus,
    GraphEdgeType,
    GraphNodeType,
    ScanStatus,
    Severity,
)
from tests.fakes import (
    FakeAIContextMemoryRepository,
    FakeAssetRepository,
    FakeEvidenceRepository,
    FakeFindingRepository,
    FakeGraphRepository,
    FakePlannedActionRepository,
    FakeReportRepository,
    FakeScanRepository,
)

NOW = datetime.now(UTC)


# -------------------------------------------------------------------
# Helper factories
# -------------------------------------------------------------------


def _node(
    project_id: uuid4,
    *,
    node_type: GraphNodeType = GraphNodeType.ASSET,
    label: str = "node",
    source_table: str = "assets",
    source_id: uuid4 | None = None,
    properties: dict | None = None,
) -> GraphNode:
    return GraphNode(
        id=uuid4(),
        project_id=project_id,
        node_type=node_type,
        source_table=source_table,
        source_id=source_id or uuid4(),
        label=label,
        properties=properties or {},
        created_at=NOW,
    )


def _edge(
    project_id: uuid4,
    from_id: uuid4,
    to_id: uuid4,
    rel: GraphEdgeType = GraphEdgeType.COMMUNICATES_WITH,
) -> GraphEdge:
    return GraphEdge(
        id=uuid4(),
        project_id=project_id,
        from_node_id=from_id,
        to_node_id=to_id,
        relationship_type=rel,
    )


def _make_asset(project_id: uuid4, value: str = "host-a") -> Asset:
    return Asset(
        id=uuid4(),
        project_id=project_id,
        asset_type=AssetType.HOST,
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
    title: str = "XSS in /api",
    severity: Severity = Severity.HIGH,
) -> Finding:
    return Finding(
        id=uuid4(),
        project_id=project_id,
        title=title,
        severity=severity,
        status=FindingStatus.OPEN,
        dedup_key=f"dedup-{uuid4()}",
        created_at=NOW,
    )


def _make_scan(
    project_id: uuid4,
    *,
    status: ScanStatus = ScanStatus.COMPLETED,
    target_ids: list | None = None,
) -> Scan:
    return Scan(
        id=uuid4(),
        project_id=project_id,
        initiated_by=uuid4(),
        plugin="nmap",
        status=status,
        target_ids=target_ids or [],
        plugin_config={},
        created_at=NOW,
        completed_at=NOW,
    )


def _setup_graph(project_id: uuid4):
    """Create a graph with:
    A --COMMUNICATES_WITH--> B --HOSTS--> C
    F1 --VULNERABLE_TO--> A
    F2 --VULNERABLE_TO--> B
    CRED --AUTHENTICATES_AS--> A
    """
    graph = FakeGraphRepository()

    a = _node(project_id, label="asset-a")
    b = _node(project_id, label="asset-b")
    c = _node(project_id, label="asset-c")
    f1 = _node(
        project_id, node_type=GraphNodeType.FINDING, label="finding-1",
        source_table="findings",
    )
    f2 = _node(
        project_id, node_type=GraphNodeType.FINDING, label="finding-2",
        source_table="findings",
    )
    cred = _node(
        project_id, node_type=GraphNodeType.CREDENTIAL, label="cred-1",
        source_table="credentials",
    )

    nodes = [a, b, c, f1, f2, cred]
    for n in nodes:
        import asyncio
        asyncio.get_event_loop().run_until_complete(graph.upsert_node(n))

    edges = [
        _edge(project_id, a.id, b.id, GraphEdgeType.COMMUNICATES_WITH),
        _edge(project_id, b.id, c.id, GraphEdgeType.HOSTS),
        _edge(project_id, f1.id, a.id, GraphEdgeType.VULNERABLE_TO),
        _edge(project_id, f2.id, b.id, GraphEdgeType.VULNERABLE_TO),
        _edge(project_id, cred.id, a.id, GraphEdgeType.AUTHENTICATES_AS),
    ]
    for e in edges:
        import asyncio
        asyncio.get_event_loop().run_until_complete(graph.upsert_edge(e))

    return graph, a, b, c, f1, f2, cred


# ======================================================================
# AttackPathService tests
# ======================================================================


class TestAttackPathService:
    @pytest.mark.asyncio
    async def test_shortest_attack_path(self):
        project_id = uuid4()
        graph = FakeGraphRepository()
        a = _node(project_id, label="a")
        b = _node(project_id, label="b")
        c = _node(project_id, label="c")
        await graph.upsert_node(a)
        await graph.upsert_node(b)
        await graph.upsert_node(c)
        await graph.upsert_edge(_edge(project_id, a.id, b.id))
        await graph.upsert_edge(_edge(project_id, b.id, c.id))

        svc = AttackPathService(graph)
        path = await svc.shortest_attack_path(project_id, a.id, c.id)

        assert path is not None
        assert path.length == 3
        assert [n.label for n in path.nodes] == ["a", "b", "c"]
        assert path.risk_score > 0

    @pytest.mark.asyncio
    async def test_shortest_attack_path_no_path(self):
        project_id = uuid4()
        graph = FakeGraphRepository()
        a = _node(project_id, label="a")
        b = _node(project_id, label="b")
        await graph.upsert_node(a)
        await graph.upsert_node(b)

        svc = AttackPathService(graph)
        path = await svc.shortest_attack_path(project_id, a.id, b.id)
        assert path is None

    @pytest.mark.asyncio
    async def test_reachable_assets(self):
        project_id = uuid4()
        graph = FakeGraphRepository()
        a = _node(project_id, label="a")
        b = _node(project_id, label="b")
        c = _node(
            project_id, node_type=GraphNodeType.FINDING, label="f",
            source_table="findings",
        )
        await graph.upsert_node(a)
        await graph.upsert_node(b)
        await graph.upsert_node(c)
        await graph.upsert_edge(_edge(project_id, a.id, b.id))
        await graph.upsert_edge(_edge(project_id, a.id, c.id))

        svc = AttackPathService(graph)
        reachable = await svc.reachable_assets(project_id, a.id)

        labels = {n.label for n in reachable}
        assert "b" in labels
        assert "f" not in labels

    @pytest.mark.asyncio
    async def test_crown_jewel_analysis(self):
        project_id = uuid4()
        graph = FakeGraphRepository()
        a = _node(project_id, label="asset-a")
        cred = _node(
            project_id, node_type=GraphNodeType.CREDENTIAL, label="cred",
            source_table="credentials",
        )
        await graph.upsert_node(a)
        await graph.upsert_node(cred)
        await graph.upsert_edge(_edge(project_id, a.id, cred.id))

        svc = AttackPathService(graph)
        results = await svc.crown_jewel_analysis(project_id)

        assert len(results) == 1
        assert results[0].crown_jewel.label == "cred"

    @pytest.mark.asyncio
    async def test_crown_jewel_specific_node(self):
        project_id = uuid4()
        graph = FakeGraphRepository()
        cred = _node(
            project_id, node_type=GraphNodeType.CREDENTIAL, label="cred",
            source_table="credentials",
        )
        await graph.upsert_node(cred)

        svc = AttackPathService(graph)
        results = await svc.crown_jewel_analysis(project_id, cred.id)

        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_crown_jewel_no_credentials(self):
        project_id = uuid4()
        graph = FakeGraphRepository()
        a = _node(project_id, label="a")
        await graph.upsert_node(a)

        svc = AttackPathService(graph)
        results = await svc.crown_jewel_analysis(project_id)
        assert results == []

    @pytest.mark.asyncio
    async def test_lateral_movement_chains(self):
        project_id = uuid4()
        graph = FakeGraphRepository()
        a = _node(project_id, label="a")
        b = _node(project_id, label="b")
        await graph.upsert_node(a)
        await graph.upsert_node(b)
        await graph.upsert_edge(
            _edge(project_id, a.id, b.id, GraphEdgeType.COMMUNICATES_WITH)
        )

        svc = AttackPathService(graph)
        chains = await svc.lateral_movement_chains(project_id)

        assert len(chains) >= 1

    @pytest.mark.asyncio
    async def test_multiple_attack_paths(self):
        project_id = uuid4()
        graph = FakeGraphRepository()
        a = _node(project_id, label="a")
        b = _node(project_id, label="b")
        await graph.upsert_node(a)
        await graph.upsert_node(b)
        await graph.upsert_edge(_edge(project_id, a.id, b.id))

        svc = AttackPathService(graph)
        paths = await svc.multiple_attack_paths(project_id, a.id, b.id)
        assert len(paths) >= 1


# ======================================================================
# ImpactAnalysisService tests
# ======================================================================


class TestImpactAnalysisService:
    @pytest.mark.asyncio
    async def test_analyze_returns_none_for_missing_node(self):
        project_id = uuid4()
        graph = FakeGraphRepository()
        svc = ImpactAnalysisService(
            graph, FakeFindingRepository(), FakeEvidenceRepository(),
            FakeAssetRepository(),
        )
        result = await svc.analyze(project_id, uuid4())
        assert result is None

    @pytest.mark.asyncio
    async def test_analyze_finds_affected_assets(self):
        project_id = uuid4()
        graph = FakeGraphRepository()
        a = _node(project_id, label="asset-a")
        b = _node(project_id, label="asset-b")
        c = _node(project_id, label="asset-c")
        f = _node(
            project_id, node_type=GraphNodeType.FINDING, label="f",
            source_table="findings",
        )
        for n in [a, b, c, f]:
            await graph.upsert_node(n)
        await graph.upsert_edge(_edge(project_id, a.id, b.id))
        await graph.upsert_edge(_edge(project_id, b.id, c.id))
        await graph.upsert_edge(
            _edge(project_id, f.id, a.id, GraphEdgeType.VULNERABLE_TO)
        )

        svc = ImpactAnalysisService(
            graph, FakeFindingRepository(), FakeEvidenceRepository(),
            FakeAssetRepository(),
        )
        result = await svc.analyze(project_id, f.id)

        assert result is not None
        assert result.blast_radius_count >= 2
        assert len(result.affected_assets) >= 2

    @pytest.mark.asyncio
    async def test_analyze_confidence_increases_with_assets(self):
        project_id = uuid4()
        graph = FakeGraphRepository()
        cred = _node(
            project_id, node_type=GraphNodeType.CREDENTIAL, label="cred",
            source_table="credentials",
        )
        a = _node(project_id, label="a")
        b = _node(project_id, label="b")
        await graph.upsert_node(cred)
        await graph.upsert_node(a)
        await graph.upsert_node(b)
        await graph.upsert_edge(_edge(project_id, cred.id, a.id))
        await graph.upsert_edge(_edge(project_id, cred.id, b.id))

        svc = ImpactAnalysisService(
            graph, FakeFindingRepository(), FakeEvidenceRepository(),
            FakeAssetRepository(),
        )
        result = await svc.analyze(project_id, cred.id)

        assert result is not None
        assert result.confidence > 0.3

    @pytest.mark.asyncio
    async def test_analyze_risk_classification(self):
        project_id = uuid4()
        graph = FakeGraphRepository()
        f = _node(
            project_id, node_type=GraphNodeType.FINDING, label="f",
            source_table="findings",
            properties={"severity": "critical"},
        )
        a = _node(project_id, label="a")
        await graph.upsert_node(f)
        await graph.upsert_node(a)
        await graph.upsert_edge(
            _edge(project_id, f.id, a.id, GraphEdgeType.VULNERABLE_TO)
        )

        svc = ImpactAnalysisService(
            graph, FakeFindingRepository(), FakeEvidenceRepository(),
            FakeAssetRepository(),
        )
        result = await svc.analyze(project_id, f.id)

        assert result is not None
        assert result.risk_level in {"info", "low", "medium", "high", "critical"}


# ======================================================================
# HistoricalIntelligenceService tests
# ======================================================================


class TestHistoricalIntelligenceService:
    @pytest.mark.asyncio
    async def test_asset_delta_insufficient_scans(self):
        project_id = uuid4()
        svc = HistoricalIntelligenceService(
            FakeGraphRepository(), FakeAssetRepository(),
            FakeFindingRepository(), FakeScanRepository(),
        )
        delta = await svc.compute_asset_delta(project_id)
        assert delta.new_count == 0
        assert delta.disappeared_count == 0

    @pytest.mark.asyncio
    async def test_finding_trends_empty(self):
        project_id = uuid4()
        svc = HistoricalIntelligenceService(
            FakeGraphRepository(), FakeAssetRepository(),
            FakeFindingRepository(), FakeScanRepository(),
        )
        trends = await svc.compute_finding_trends(project_id)
        assert trends == []

    @pytest.mark.asyncio
    async def test_recurring_findings(self):
        project_id = uuid4()
        finding_repo = FakeFindingRepository()
        f1 = _make_finding(project_id, title="Recurring XSS")
        f2 = _make_finding(project_id, title="Recurring XSS")
        await finding_repo.add(f1)
        await finding_repo.add(f2)

        svc = HistoricalIntelligenceService(
            FakeGraphRepository(), FakeAssetRepository(),
            finding_repo, FakeScanRepository(),
        )
        recurring = await svc.find_recurring_findings(project_id)
        assert len(recurring) == 1
        assert recurring[0].occurrence_count == 2

    @pytest.mark.asyncio
    async def test_technology_changes(self):
        project_id = uuid4()
        graph = FakeGraphRepository()
        tech = _node(
            project_id, node_type=GraphNodeType.TECHNOLOGY, label="nginx",
        )
        await graph.upsert_node(tech)

        svc = HistoricalIntelligenceService(
            graph, FakeAssetRepository(), FakeFindingRepository(),
            FakeScanRepository(),
        )
        changes = await svc.detect_technology_changes(project_id)
        assert isinstance(changes, list)

    @pytest.mark.asyncio
    async def test_surface_expanding(self):
        project_id = uuid4()
        scan_repo = FakeScanRepository()
        s1 = _make_scan(project_id, target_ids=[uuid4()])
        s2 = _make_scan(project_id, target_ids=[uuid4(), uuid4()])
        await scan_repo.create(s1)
        await scan_repo.create(s2)

        svc = HistoricalIntelligenceService(
            FakeGraphRepository(), FakeAssetRepository(),
            FakeFindingRepository(), scan_repo,
        )
        expanding = await svc.is_surface_expanding(project_id)
        assert isinstance(expanding, bool)

    @pytest.mark.asyncio
    async def test_generate_report(self):
        project_id = uuid4()
        svc = HistoricalIntelligenceService(
            FakeGraphRepository(), FakeAssetRepository(),
            FakeFindingRepository(), FakeScanRepository(),
        )
        report = await svc.generate_report(project_id)
        assert report.scan_count == 0
        assert report.asset_delta.new_count == 0


# ======================================================================
# ExecutiveIntelligenceService tests
# ======================================================================


class TestExecutiveIntelligenceService:
    @pytest.mark.asyncio
    async def test_highest_risk_assets_empty(self):
        project_id = uuid4()
        svc = ExecutiveIntelligenceService(
            FakeGraphRepository(), FakeFindingRepository(),
            FakeAssetRepository(), FakeScanRepository(),
        )
        result = await svc.highest_risk_assets(project_id)
        assert result == []

    @pytest.mark.asyncio
    async def test_most_connected_assets(self):
        project_id = uuid4()
        graph = FakeGraphRepository()
        a = _node(project_id, label="hub")
        b = _node(project_id, label="spoke1")
        c = _node(project_id, label="spoke2")
        for n in [a, b, c]:
            await graph.upsert_node(n)
        await graph.upsert_edge(_edge(project_id, a.id, b.id))
        await graph.upsert_edge(_edge(project_id, a.id, c.id))

        svc = ExecutiveIntelligenceService(
            graph, FakeFindingRepository(), FakeAssetRepository(),
            FakeScanRepository(),
        )
        result = await svc.most_connected_assets(project_id)
        assert len(result) >= 1
        assert result[0].connection_count == 2

    @pytest.mark.asyncio
    async def test_technology_exposure(self):
        project_id = uuid4()
        graph = FakeGraphRepository()
        tech = _node(
            project_id, node_type=GraphNodeType.TECHNOLOGY, label="apache",
        )
        f = _node(
            project_id, node_type=GraphNodeType.FINDING, label="vuln",
            source_table="findings",
        )
        await graph.upsert_node(tech)
        await graph.upsert_node(f)
        await graph.upsert_edge(
            _edge(project_id, tech.id, f.id, GraphEdgeType.RUNS)
        )

        svc = ExecutiveIntelligenceService(
            graph, FakeFindingRepository(), FakeAssetRepository(),
            FakeScanRepository(),
        )
        result = await svc.technologies_with_most_exposure(project_id)
        assert len(result) == 1
        assert result[0].connected_finding_count == 1

    @pytest.mark.asyncio
    async def test_graph_growth(self):
        project_id = uuid4()
        graph = FakeGraphRepository()
        a = _node(project_id, label="a")
        b = _node(project_id, label="b")
        await graph.upsert_node(a)
        await graph.upsert_node(b)
        await graph.upsert_edge(_edge(project_id, a.id, b.id))

        svc = ExecutiveIntelligenceService(
            graph, FakeFindingRepository(), FakeAssetRepository(),
            FakeScanRepository(),
        )
        growth = await svc.graph_growth(project_id)
        assert len(growth) > 0

    @pytest.mark.asyncio
    async def test_generate_report(self):
        project_id = uuid4()
        svc = ExecutiveIntelligenceService(
            FakeGraphRepository(), FakeFindingRepository(),
            FakeAssetRepository(), FakeScanRepository(),
        )
        report = await svc.generate_report(project_id)
        assert report.total_nodes == 0
        assert report.total_edges == 0


# ======================================================================
# Graph-integrated PlannerService tests
# ======================================================================


class TestPlannerGraphIntegration:
    @pytest.mark.asyncio
    async def test_planner_uses_graph_when_available(self):
        project_id = uuid4()
        graph = FakeGraphRepository()
        a = _node(project_id, label="target-host")
        f = _node(
            project_id, node_type=GraphNodeType.FINDING, label="vuln",
            source_table="findings",
        )
        for n in [a, f]:
            await graph.upsert_node(n)
        await graph.upsert_edge(
            _edge(project_id, f.id, a.id, GraphEdgeType.VULNERABLE_TO)
        )

        finding_repo = FakeFindingRepository()
        asset_repo = FakeAssetRepository()
        finding = _make_finding(project_id)
        await finding_repo.add(finding)

        svc = PlannerService(
            planned_action_repo=FakePlannedActionRepository(),
            finding_repo=finding_repo,
            asset_repo=asset_repo,
            context_memory_repo=FakeAIContextMemoryRepository(),
            graph_repo=graph,
        )
        actions = await svc.suggest(project_id)
        assert len(actions) > 0

    @pytest.mark.asyncio
    async def test_planner_falls_back_without_graph(self):
        project_id = uuid4()
        svc = PlannerService(
            planned_action_repo=FakePlannedActionRepository(),
            finding_repo=FakeFindingRepository(),
            asset_repo=FakeAssetRepository(),
            context_memory_repo=FakeAIContextMemoryRepository(),
            graph_repo=None,
        )
        actions = await svc.suggest(project_id)
        assert len(actions) > 0


# ======================================================================
# Graph-integrated AnalyzerService tests
# ======================================================================


class TestAnalyzerGraphIntegration:
    @pytest.mark.asyncio
    async def test_analyzer_graph_correlations(self):
        project_id = uuid4()
        graph = FakeGraphRepository()
        shared_asset = _node(project_id, label="shared-host")

        f1_node = _node(
            project_id, node_type=GraphNodeType.FINDING, label="f1",
            source_table="findings",
        )
        f2_node = _node(
            project_id, node_type=GraphNodeType.FINDING, label="f2",
            source_table="findings",
        )
        for n in [shared_asset, f1_node, f2_node]:
            await graph.upsert_node(n)
        await graph.upsert_edge(
            _edge(project_id, f1_node.id, shared_asset.id, GraphEdgeType.VULNERABLE_TO)
        )
        await graph.upsert_edge(
            _edge(project_id, f2_node.id, shared_asset.id, GraphEdgeType.VULNERABLE_TO)
        )

        finding_repo = FakeFindingRepository()
        f1 = _make_finding(project_id, title="F1")
        f2 = _make_finding(project_id, title="F2")
        f1.id = f1_node.source_id
        f2.id = f2_node.source_id
        await finding_repo.add(f1)
        await finding_repo.add(f2)

        svc = AnalyzerService(finding_repo, graph_repo=graph)
        correlations = await svc.get_finding_correlations(f1.id)
        graph_corrs = [
            c for c in correlations
            if c.get("correlation_type") == "graph_blast_radius"
        ]
        assert len(graph_corrs) >= 1

    @pytest.mark.asyncio
    async def test_analyzer_without_graph(self):
        project_id = uuid4()
        finding_repo = FakeFindingRepository()
        f = _make_finding(project_id)
        await finding_repo.add(f)

        svc = AnalyzerService(finding_repo, graph_repo=None)
        correlations = await svc.get_finding_correlations(f.id)
        assert correlations == []


# ======================================================================
# Graph-integrated ExplainerService tests
# ======================================================================


class TestExplainerGraphIntegration:
    @pytest.mark.asyncio
    async def test_explainer_includes_graph_context(self):
        project_id = uuid4()
        graph = FakeGraphRepository()
        finding_repo = FakeFindingRepository()

        f = _make_finding(project_id)
        await finding_repo.add(f)

        f_node = _node(
            project_id, node_type=GraphNodeType.FINDING, label=f.title,
            source_table="findings", source_id=f.id,
        )
        a = _node(project_id, label="affected-host")
        await graph.upsert_node(f_node)
        await graph.upsert_node(a)
        await graph.upsert_edge(
            _edge(project_id, f_node.id, a.id, GraphEdgeType.VULNERABLE_TO)
        )

        svc = ExplainerService(finding_repo, graph_repo=graph)
        result = await svc.explain_finding(f.id)
        assert "graph_context" in result
        assert "blast radius" in result["graph_context"]

    @pytest.mark.asyncio
    async def test_explainer_without_graph(self):
        project_id = uuid4()
        finding_repo = FakeFindingRepository()
        f = _make_finding(project_id)
        await finding_repo.add(f)

        svc = ExplainerService(finding_repo, graph_repo=None)
        result = await svc.explain_finding(f.id)
        assert "graph_context" not in result


# ======================================================================
# Graph-integrated AIReporterService tests
# ======================================================================


class TestReporterGraphIntegration:
    @pytest.mark.asyncio
    async def test_reporter_includes_graph_context(self):
        project_id = uuid4()
        graph = FakeGraphRepository()
        finding_repo = FakeFindingRepository()

        f = _make_finding(project_id)
        await finding_repo.add(f)

        f_node = _node(
            project_id, node_type=GraphNodeType.FINDING, label=f.title,
            source_table="findings", source_id=f.id,
        )
        a = _node(project_id, label="target-server")
        await graph.upsert_node(f_node)
        await graph.upsert_node(a)
        await graph.upsert_edge(
            _edge(project_id, f_node.id, a.id, GraphEdgeType.VULNERABLE_TO)
        )

        svc = AIReporterService(
            finding_repo, FakeReportRepository(), graph_repo=graph,
        )
        result = await svc.draft_finding_narrative(f.id)
        assert "graph_context" in result
        assert "target-server" in result["graph_context"]

    @pytest.mark.asyncio
    async def test_reporter_without_graph(self):
        project_id = uuid4()
        finding_repo = FakeFindingRepository()
        f = _make_finding(project_id)
        await finding_repo.add(f)

        svc = AIReporterService(
            finding_repo, FakeReportRepository(), graph_repo=None,
        )
        result = await svc.draft_finding_narrative(f.id)
        assert "graph_context" not in result
