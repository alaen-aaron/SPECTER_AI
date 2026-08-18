"""
Executive Intelligence Service (Milestone 4.5 — Knowledge Graph Intelligence).

Generates backend analytics suitable for dashboards: highest-risk
assets, most-connected assets, findings by attack surface,
technologies with most exposure, top attack chains, and graph
growth over time.

Only implements backend data — no frontend charts.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.domain.entities import GraphNode
from app.domain.repositories import (
    AssetRepository,
    FindingRepository,
    GraphRepository,
    ScanRepository,
)
from app.domain.value_objects import (
    GraphEdgeType,
    GraphNodeType,
)

_SEVERITY_WEIGHTS: dict[str, float] = {
    "critical": 10.0,
    "high": 7.0,
    "medium": 4.0,
    "low": 1.0,
    "info": 0.1,
}


@dataclass(frozen=True)
class RiskAsset:
    """An asset ranked by risk score."""

    node: GraphNode
    risk_score: float
    finding_count: int
    connection_count: int


@dataclass(frozen=True)
class ConnectedAsset:
    """An asset ranked by number of graph connections."""

    node: GraphNode
    connection_count: int
    connected_types: list[str]


@dataclass(frozen=True)
class AttackSurfaceFinding:
    """Findings grouped by attack surface / edge type."""

    edge_type: str
    finding_count: int
    finding_ids: list[UUID]


@dataclass(frozen=True)
class TechnologyExposure:
    """Technology ranked by number of associated findings/exposure."""

    node: GraphNode
    exposure_score: float
    connected_finding_count: int


@dataclass(frozen=True)
class AttackChain:
    """A ranked attack chain through the graph."""

    nodes: list[GraphNode]
    chain_length: int
    risk_score: float


@dataclass(frozen=True)
class GraphGrowth:
    """Graph growth over time (nodes and edges added per period)."""

    period: str
    nodes_added: int
    edges_added: int
    total_nodes: int
    total_edges: int


@dataclass(frozen=True)
class ExecutiveReport:
    """Complete executive intelligence report for dashboard consumption."""

    highest_risk_assets: list[RiskAsset]
    most_connected_assets: list[ConnectedAsset]
    findings_by_surface: list[AttackSurfaceFinding]
    technology_exposure: list[TechnologyExposure]
    top_attack_chains: list[AttackChain]
    graph_growth: list[GraphGrowth]
    total_nodes: int
    total_edges: int
    total_findings: int
    total_assets: int


class ExecutiveIntelligenceService:
    """Generates executive-level analytics from the Knowledge Graph.

    Depends only on domain interfaces — no infrastructure imports.
    """

    def __init__(
        self,
        graph_repo: GraphRepository,
        finding_repo: FindingRepository,
        asset_repo: AssetRepository,
        scan_repo: ScanRepository,
    ) -> None:
        self._graph = graph_repo
        self._findings = finding_repo
        self._assets = asset_repo
        self._scans = scan_repo

    async def highest_risk_assets(
        self,
        project_id: UUID,
        limit: int = 10,
    ) -> list[RiskAsset]:
        """Rank assets by risk score based on findings and connections."""
        asset_nodes = await self._graph.list_nodes_for_project(
            project_id, GraphNodeType.ASSET
        )
        finding_nodes = await self._graph.list_nodes_for_project(
            project_id, GraphNodeType.FINDING
        )

        asset_risks: dict[UUID, tuple[GraphNode, float, int, int]] = {}
        for an in asset_nodes:
            asset_risks[an.id] = (an, 0.0, 0, 0)

        for fn in finding_nodes:
            neighbors = await self._graph.get_neighbors(
                fn.id, GraphEdgeType.VULNERABLE_TO, "outgoing"
            )
            sev = str(fn.properties.get("severity", "info"))
            weight = _SEVERITY_WEIGHTS.get(sev, 0.1)

            for neighbor in neighbors:
                if neighbor.id in asset_risks:
                    node, risk, fc, cc = asset_risks[neighbor.id]
                    asset_risks[neighbor.id] = (node, risk + weight, fc + 1, cc)

        for an in asset_nodes:
            neighbors = await self._graph.get_neighbors(an.id)
            node, risk, fc, _cc = asset_risks[an.id]
            asset_risks[an.id] = (node, risk, fc, len(neighbors))

        ranked = sorted(
            asset_risks.values(),
            key=lambda x: x[1],
            reverse=True,
        )

        return [
            RiskAsset(
                node=entry[0],
                risk_score=entry[1],
                finding_count=entry[2],
                connection_count=entry[3],
            )
            for entry in ranked[:limit]
            if entry[1] > 0
        ]

    async def most_connected_assets(
        self,
        project_id: UUID,
        limit: int = 10,
    ) -> list[ConnectedAsset]:
        """Rank assets by number of graph connections."""
        asset_nodes = await self._graph.list_nodes_for_project(
            project_id, GraphNodeType.ASSET
        )

        results: list[ConnectedAsset] = []
        for an in asset_nodes:
            neighbors = await self._graph.get_neighbors(an.id)
            connected_types = list(
                {n.node_type.value for n in neighbors}
            )
            results.append(
                ConnectedAsset(
                    node=an,
                    connection_count=len(neighbors),
                    connected_types=connected_types,
                )
            )

        results.sort(key=lambda x: x.connection_count, reverse=True)
        return results[:limit]

    async def findings_by_attack_surface(
        self,
        project_id: UUID,
    ) -> list[AttackSurfaceFinding]:
        """Group findings by the edge type connecting them to assets."""
        finding_nodes = await self._graph.list_nodes_for_project(
            project_id, GraphNodeType.FINDING
        )

        surface_groups: dict[str, list[UUID]] = {}
        for fn in finding_nodes:
            incoming = await self._graph.get_neighbors(
                fn.id, direction="incoming"
            )
            if not incoming:
                key = "unlinked"
                surface_groups.setdefault(key, []).append(fn.id)
                continue

            for _ in incoming:
                for et in GraphEdgeType:
                    edge_key = et.value
                    surface_groups.setdefault(edge_key, []).append(fn.id)
                    break
                break

        return [
            AttackSurfaceFinding(
                edge_type=et,
                finding_count=len(fids),
                finding_ids=fids,
            )
            for et, fids in sorted(
                surface_groups.items(),
                key=lambda x: len(x[1]),
                reverse=True,
            )
        ]

    async def technologies_with_most_exposure(
        self,
        project_id: UUID,
        limit: int = 10,
    ) -> list[TechnologyExposure]:
        """Rank technologies by number of connected findings."""
        tech_nodes = await self._graph.list_nodes_for_project(
            project_id, GraphNodeType.TECHNOLOGY
        )

        results: list[TechnologyExposure] = []
        for tn in tech_nodes:
            neighbors = await self._graph.get_neighbors(tn.id)
            finding_count = sum(
                1 for n in neighbors if n.node_type == GraphNodeType.FINDING
            )
            exposure = finding_count * _SEVERITY_WEIGHTS.get("medium", 4.0)
            results.append(
                TechnologyExposure(
                    node=tn,
                    exposure_score=exposure,
                    connected_finding_count=finding_count,
                )
            )

        results.sort(key=lambda x: x.exposure_score, reverse=True)
        return results[:limit]

    async def top_attack_chains(
        self,
        project_id: UUID,
        limit: int = 5,
    ) -> list[AttackChain]:
        """Find the most impactful attack chains through the graph."""
        all_nodes = await self._graph.list_nodes_for_project(project_id)

        asset_nodes = [
            n for n in all_nodes if n.node_type == GraphNodeType.ASSET
        ]

        chains: list[AttackChain] = []
        for start in asset_nodes[:20]:
            for end in asset_nodes[:20]:
                if start.id == end.id:
                    continue
                path = await self._graph.shortest_path(
                    start.id, end.id, 6
                )
                if path and len(path) >= 3:
                    risk = len(path) * 0.1
                    chains.append(
                        AttackChain(
                            nodes=path,
                            chain_length=len(path),
                            risk_score=min(risk, 1.0),
                        )
                    )

        chains.sort(key=lambda c: c.risk_score, reverse=True)
        return chains[:limit]

    async def graph_growth(
        self,
        project_id: UUID,
    ) -> list[GraphGrowth]:
        """Compute graph growth metrics."""
        nodes = await self._graph.list_nodes_for_project(project_id)
        edges = await self._graph.list_edges_for_project(project_id)

        growth: list[GraphGrowth] = []

        type_counts: dict[str, int] = {}
        for n in nodes:
            key = n.node_type.value
            type_counts[key] = type_counts.get(key, 0) + 1

        for node_type, count in type_counts.items():
            growth.append(
                GraphGrowth(
                    period=node_type,
                    nodes_added=count,
                    edges_added=0,
                    total_nodes=len(nodes),
                    total_edges=len(edges),
                )
            )

        return growth

    async def generate_report(
        self,
        project_id: UUID,
    ) -> ExecutiveReport:
        """Generate a complete executive intelligence report."""
        risk_assets = await self.highest_risk_assets(project_id)
        connected = await self.most_connected_assets(project_id)
        surface = await self.findings_by_attack_surface(project_id)
        tech_exposure = await self.technologies_with_most_exposure(project_id)
        chains = await self.top_attack_chains(project_id)
        growth = await self.graph_growth(project_id)

        nodes = await self._graph.list_nodes_for_project(project_id)
        edges = await self._graph.list_edges_for_project(project_id)
        findings = await self._findings.list_for_project(project_id, limit=10000)
        assets = await self._assets.list_for_project(project_id, limit=10000)

        return ExecutiveReport(
            highest_risk_assets=risk_assets,
            most_connected_assets=connected,
            findings_by_surface=surface,
            technology_exposure=tech_exposure,
            top_attack_chains=chains,
            graph_growth=growth,
            total_nodes=len(nodes),
            total_edges=len(edges),
            total_findings=len(findings),
            total_assets=len(assets),
        )
