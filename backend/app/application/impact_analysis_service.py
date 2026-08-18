"""
Impact Analysis Service (Milestone 4.5 — Knowledge Graph Intelligence).

Given an asset, finding, credential, or technology node, computes
the full impact: affected downstream assets, downstream findings,
blast radius, confidence score, and supporting evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.domain.entities import GraphNode
from app.domain.repositories import (
    AssetRepository,
    EvidenceRepository,
    FindingRepository,
    GraphRepository,
)
from app.domain.value_objects import GraphNodeType

# Severity weights for impact scoring
_SEVERITY_WEIGHTS: dict[str, float] = {
    "critical": 1.0,
    "high": 0.8,
    "medium": 0.5,
    "low": 0.2,
    "info": 0.05,
}


@dataclass(frozen=True)
class ImpactResult:
    """Full impact analysis for a given node."""

    source_node: GraphNode
    affected_assets: list[GraphNode]
    downstream_findings: list[GraphNode]
    reachable_nodes: list[GraphNode]
    blast_radius_count: int
    confidence: float
    evidence: list[GraphNode]
    risk_level: str


class ImpactAnalysisService:
    """Computes impact analysis using graph traversal.

    Depends only on domain interfaces — no infrastructure imports.
    """

    def __init__(
        self,
        graph_repo: GraphRepository,
        finding_repo: FindingRepository,
        evidence_repo: EvidenceRepository,
        asset_repo: AssetRepository,
    ) -> None:
        self._graph = graph_repo
        self._findings = finding_repo
        self._evidence = evidence_repo
        self._assets = asset_repo

    async def analyze(
        self,
        project_id: UUID,
        node_id: UUID,
        max_depth: int = 5,
    ) -> ImpactResult | None:
        """Perform full impact analysis starting from a graph node."""
        node = await self._graph.get_node(node_id)
        if node is None:
            return None

        reachable = await self._graph.blast_radius(
            project_id, node_id, max_depth
        )

        affected_assets = [
            n
            for n in reachable
            if n.node_type in {GraphNodeType.ASSET, GraphNodeType.TECHNOLOGY}
        ]

        downstream_findings = [
            n
            for n in reachable
            if n.node_type == GraphNodeType.FINDING
        ]

        evidence_nodes = [
            n
            for n in reachable
            if n.node_type == GraphNodeType.EVIDENCE
        ]

        confidence = self._compute_confidence(
            node, affected_assets, downstream_findings
        )

        risk_level = self._classify_risk(
            len(affected_assets),
            len(downstream_findings),
            confidence,
            downstream_findings,
        )

        return ImpactResult(
            source_node=node,
            affected_assets=affected_assets,
            downstream_findings=downstream_findings,
            reachable_nodes=reachable,
            blast_radius_count=len(reachable),
            confidence=confidence,
            evidence=evidence_nodes,
            risk_level=risk_level,
        )

    async def analyze_asset(
        self,
        project_id: UUID,
        asset_id: UUID,
    ) -> ImpactResult | None:
        """Impact analysis for a specific asset."""
        node = await self._find_asset_node(project_id, asset_id)
        if node is None:
            return None
        return await self.analyze(project_id, node.id)

    async def analyze_finding(
        self,
        project_id: UUID,
        finding_id: UUID,
    ) -> ImpactResult | None:
        """Impact analysis for a specific finding."""
        node = await self._find_finding_node(project_id, finding_id)
        if node is None:
            return None
        return await self.analyze(project_id, node.id)

    async def analyze_credential(
        self,
        project_id: UUID,
        credential_id: UUID,
    ) -> ImpactResult | None:
        """Impact analysis for a specific credential."""
        node = await self._find_credential_node(project_id, credential_id)
        if node is None:
            return None
        return await self.analyze(project_id, node.id)

    async def analyze_technology(
        self,
        project_id: UUID,
        technology_id: UUID,
    ) -> ImpactResult | None:
        """Impact analysis for a specific technology."""
        node = await self._find_technology_node(project_id, technology_id)
        if node is None:
            return None
        return await self.analyze(project_id, node.id)

    async def _find_asset_node(
        self, project_id: UUID, asset_id: UUID
    ) -> GraphNode | None:
        return await self._graph.find_node(
            project_id, GraphNodeType.ASSET, "assets", asset_id
        )

    async def _find_finding_node(
        self, project_id: UUID, finding_id: UUID
    ) -> GraphNode | None:
        return await self._graph.find_node(
            project_id, GraphNodeType.FINDING, "findings", finding_id
        )

    async def _find_credential_node(
        self, project_id: UUID, credential_id: UUID
    ) -> GraphNode | None:
        return await self._graph.find_node(
            project_id, GraphNodeType.CREDENTIAL, "credentials", credential_id
        )

    async def _find_technology_node(
        self, project_id: UUID, technology_id: UUID
    ) -> GraphNode | None:
        return await self._graph.find_node(
            project_id, GraphNodeType.TECHNOLOGY, "assets", technology_id
        )

    def _compute_confidence(
        self,
        source_node: GraphNode,
        affected_assets: list[GraphNode],
        downstream_findings: list[GraphNode],
    ) -> float:
        """Compute a confidence score (0.0-1.0) for the impact analysis.

        Confidence increases when:
        - The source node has many reachable assets
        - There are concrete downstream findings
        - The source is a high-value node type
        """
        base = 0.3

        if affected_assets:
            asset_factor = min(len(affected_assets) * 0.05, 0.3)
            base += asset_factor

        if downstream_findings:
            finding_factor = min(len(downstream_findings) * 0.08, 0.3)
            base += finding_factor

        if source_node.node_type == GraphNodeType.CREDENTIAL:
            base += 0.1
        elif source_node.node_type == GraphNodeType.FINDING:
            severity = source_node.properties.get("severity", "info")
            base += _SEVERITY_WEIGHTS.get(str(severity), 0.0) * 0.1

        return min(base, 1.0)

    def _classify_risk(
        self,
        affected_count: int,
        findings_count: int,
        confidence: float,
        finding_nodes: list[GraphNode],
    ) -> str:
        """Classify the overall risk level."""
        severity_score = 0.0
        for fn in finding_nodes:
            sev = str(fn.properties.get("severity", "info"))
            severity_score += _SEVERITY_WEIGHTS.get(sev, 0.0)

        combined = (affected_count * 0.1) + severity_score + (confidence * 0.3)

        if combined >= 1.5:
            return "critical"
        if combined >= 1.0:
            return "high"
        if combined >= 0.5:
            return "medium"
        if combined > 0.0:
            return "low"
        return "info"
