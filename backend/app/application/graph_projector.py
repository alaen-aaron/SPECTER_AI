"""
Graph Projector (Milestone 4 — Knowledge Graph Foundation).

Subscribes to domain events (Asset upserts, Finding creation, Evidence
attachment) and projects them into the Knowledge Graph as typed nodes
and edges.  The relational database remains the source of truth — the
graph is always rebuildable from scratch.

Projection is idempotent: running the same event twice produces the
same graph state (upsert semantics on nodes and edges).

Design rules:
  - No framework imports from `infrastructure/` or `api/`.
  - GraphProjector depends on GraphRepository (domain interface).
  - All projection happens inside the caller's transaction boundary.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import structlog

from app.domain.entities import Asset, Evidence, Finding, GraphEdge, GraphNode
from app.domain.repositories import (
    AssetRepository,
    EvidenceRepository,
    FindingRepository,
    GraphRepository,
)
from app.domain.value_objects import AssetType, GraphEdgeType, GraphNodeType

logger = structlog.get_logger(__name__)

# Mapping from AssetType → GraphNodeType
_ASSET_TYPE_MAP: dict[AssetType, GraphNodeType] = {
    AssetType.HOST: GraphNodeType.ASSET,
    AssetType.SUBDOMAIN: GraphNodeType.ASSET,
    AssetType.SERVICE: GraphNodeType.ASSET,
    AssetType.TECHNOLOGY: GraphNodeType.TECHNOLOGY,
    AssetType.CREDENTIAL: GraphNodeType.CREDENTIAL,
}


class GraphProjector:
    """
    Projects relational domain entities into the Knowledge Graph.

    Each `project_*` method is idempotent — calling it with the same
    entity twice produces identical graph state via upsert.
    """

    def __init__(
        self,
        graph_repo: GraphRepository,
        asset_repo: AssetRepository,
        finding_repo: FindingRepository,
        evidence_repo: EvidenceRepository,
    ) -> None:
        self._graph = graph_repo
        self._assets = asset_repo
        self._findings = finding_repo
        self._evidence = evidence_repo

    # ------------------------------------------------------------------
    # Individual event projections
    # ------------------------------------------------------------------

    async def project_asset(self, asset: Asset) -> GraphNode:
        """Create or update a graph node for an Asset, and wire up
        relationships based on asset type."""
        node_type = _ASSET_TYPE_MAP.get(asset.asset_type, GraphNodeType.ASSET)
        properties: dict[str, object] = {
            "asset_type": asset.asset_type.value,
            "value": asset.value,
            "in_scope": asset.in_scope,
        }
        if asset.metadata:
            properties["metadata"] = asset.metadata

        node = GraphNode(
            id=uuid4(),
            project_id=asset.project_id,
            node_type=node_type,
            source_table="assets",
            source_id=asset.id,
            label=asset.value,
            properties=properties,
        )
        projected = await self._graph.upsert_node(node)
        logger.info(
            "graph_projected_asset",
            asset_id=str(asset.id),
            node_id=str(projected.id),
            node_type=node_type.value,
        )
        return projected

    async def project_finding(self, finding: Finding) -> GraphNode:
        """Create or update a graph node for a Finding.

        Automatically creates ``vulnerable_to`` edges from the finding
        node to any Asset nodes that share the same project and whose
        value appears in the finding's description or title.
        """
        properties: dict[str, object] = {
            "severity": finding.severity.value,
            "status": finding.status.value,
        }
        if finding.cvss_score is not None:
            properties["cvss_score"] = finding.cvss_score

        node = GraphNode(
            id=uuid4(),
            project_id=finding.project_id,
            node_type=GraphNodeType.FINDING,
            source_table="findings",
            source_id=finding.id,
            label=finding.title,
            properties=properties,
        )
        projected = await self._graph.upsert_node(node)
        logger.info(
            "graph_projected_finding",
            finding_id=str(finding.id),
            node_id=str(projected.id),
        )

        # Wire up vulnerable_to edges to related assets
        assets = await self._assets.list_for_project(finding.project_id)
        for asset in assets:
            if asset.value and asset.value in (finding.title + " " + (finding.description or "")):
                asset_node = await self._graph.find_node(
                    finding.project_id,
                    _ASSET_TYPE_MAP.get(asset.asset_type, GraphNodeType.ASSET),
                    "assets",
                    asset.id,
                )
                if asset_node is not None:
                    await self._graph.upsert_edge(
                        GraphEdge(
                            id=uuid4(),
                            project_id=finding.project_id,
                            from_node_id=projected.id,
                            to_node_id=asset_node.id,
                            relationship_type=GraphEdgeType.VULNERABLE_TO,
                            properties={"finding_id": str(finding.id)},
                        )
                    )

        return projected

    async def project_evidence(
        self, evidence: Evidence, finding: Finding | None = None
    ) -> GraphNode:
        """Create or update a graph node for Evidence, and create an
        ``evidenced_by`` edge linking it to the parent Finding."""
        if finding is None:
            finding = await self._findings.get(evidence.finding_id)

        properties: dict[str, object] = {
            "evidence_type": evidence.evidence_type.value,
            "content_hash": evidence.content_hash,
        }
        if evidence.filename:
            properties["filename"] = evidence.filename
        if finding is not None:
            properties["finding_title"] = finding.title

        label = evidence.filename or evidence.evidence_type.value
        node = GraphNode(
            id=uuid4(),
            project_id=finding.project_id if finding else uuid4(),
            node_type=GraphNodeType.EVIDENCE,
            source_table="evidence",
            source_id=evidence.id,
            label=label,
            properties=properties,
        )
        projected = await self._graph.upsert_node(node)
        logger.info(
            "graph_projected_evidence",
            evidence_id=str(evidence.id),
            node_id=str(projected.id),
        )

        # Link evidence → finding via evidenced_by
        if finding is not None:
            finding_node = await self._graph.find_node(
                finding.project_id,
                GraphNodeType.FINDING,
                "findings",
                finding.id,
            )
            if finding_node is not None:
                await self._graph.upsert_edge(
                    GraphEdge(
                        id=uuid4(),
                        project_id=finding.project_id,
                        from_node_id=projected.id,
                        to_node_id=finding_node.id,
                        relationship_type=GraphEdgeType.EVIDENCED_BY,
                    )
                )

        return projected

    # ------------------------------------------------------------------
    # Bulk rebuild
    # ------------------------------------------------------------------

    async def rebuild_graph_from_scratch(self, project_id: UUID) -> dict[str, int]:
        """
        Clear the entire graph for a project and rebuild it from the
        relational source tables.  Deterministic: same input always
        produces the same graph.

        Returns counts of nodes/edges created.
        """
        await self._graph.clear_project(project_id)
        logger.info("graph_cleared", project_id=str(project_id))

        node_count = 0
        edge_count = 0

        # Project all assets
        assets = await self._assets.list_for_project(project_id, limit=10000)
        for asset in assets:
            await self.project_asset(asset)
            node_count += 1

        # Project all findings
        findings = await self._findings.list_for_project(project_id, limit=10000)
        for finding in findings:
            await self.project_finding(finding)
            node_count += 1

        # Project all evidence
        evidence_list = await self._evidence.list_for_project(project_id)
        for evidence in evidence_list:
            await self.project_evidence(evidence)
            node_count += 1

        # Count edges created
        edges = await self._graph.list_edges_for_project(project_id)
        edge_count = len(edges)

        logger.info(
            "graph_rebuilt",
            project_id=str(project_id),
            nodes=node_count,
            edges=edge_count,
        )
        return {"nodes": node_count, "edges": edge_count}
