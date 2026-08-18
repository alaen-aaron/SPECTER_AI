"""
Attack Path Service (Milestone 4.5 — Knowledge Graph Intelligence).

Computes attack paths through the Knowledge Graph using graph
traversal queries.  Returns structured domain objects, not
UI-specific responses — the frontend will consume these APIs
without requiring backend redesign.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.domain.entities import GraphNode
from app.domain.repositories import GraphRepository
from app.domain.value_objects import GraphEdgeType, GraphNodeType

# Node types considered high-value "crown jewels"
_CROWN_JEWEL_TYPES: frozenset[GraphNodeType] = frozenset(
    {GraphNodeType.CREDENTIAL}
)

# Edge types that represent offensive movement (attacker traversal)
_LATERAL_MOVEMENT_EDGES: frozenset[GraphEdgeType] = frozenset(
    {
        GraphEdgeType.COMMUNICATES_WITH,
        GraphEdgeType.HOSTS,
        GraphEdgeType.AUTHENTICATES_AS,
    }
)


@dataclass(frozen=True)
class AttackPath:
    """A single attack path through the graph."""

    nodes: list[GraphNode]
    edges: list[GraphEdgeType]
    length: int
    risk_score: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "length", len(self.nodes))


@dataclass(frozen=True)
class LateralMovementChain:
    """A chain of lateral movement hops between assets."""

    source_asset: GraphNode
    target_asset: GraphNode
    hops: list[GraphNode]
    edge_types: list[GraphEdgeType]
    chain_length: int


@dataclass(frozen=True)
class CrownJewelResult:
    """All reachable nodes from a crown-jewel asset and the paths to it."""

    crown_jewel: GraphNode
    reachable_from: list[GraphNode]
    paths_to: list[list[GraphNode]]


class AttackPathService:
    """Computes attack paths, lateral movement chains, and crown-jewel
    reachability using the Knowledge Graph.

    This service is pure application logic — it depends only on the
    ``GraphRepository`` domain interface, never on infrastructure.
    """

    def __init__(self, graph_repo: GraphRepository) -> None:
        self._graph = graph_repo

    async def shortest_attack_path(
        self,
        project_id: UUID,
        from_node_id: UUID,
        to_node_id: UUID,
        max_depth: int = 10,
    ) -> AttackPath | None:
        """Find the shortest path between two nodes in the graph."""
        path = await self._graph.shortest_path(
            from_node_id, to_node_id, max_depth
        )
        if path is None or len(path) < 2:
            return None

        edge_types = await self._infer_edge_types(path)
        risk = self._compute_risk_score(path, edge_types)
        return AttackPath(
            nodes=path,
            edges=edge_types,
            length=len(path),
            risk_score=risk,
        )

    async def multiple_attack_paths(
        self,
        project_id: UUID,
        from_node_id: UUID,
        to_node_id: UUID,
        max_paths: int = 5,
        max_depth: int = 10,
    ) -> list[AttackPath]:
        """Find multiple candidate paths between two nodes.

        Uses BFS to find the shortest path, then iteratively removes
        edges to discover alternative routes.
        """
        paths: list[AttackPath] = []
        seen: set[tuple[UUID, ...]] = set()

        current = await self._graph.shortest_path(
            from_node_id, to_node_id, max_depth
        )
        if current is None:
            return []

        path_key = tuple(n.id for n in current)
        seen.add(path_key)
        edge_types = await self._infer_edge_types(current)
        risk = self._compute_risk_score(current, edge_types)
        paths.append(
            AttackPath(
                nodes=list(current),
                edges=list(edge_types),
                length=len(current),
                risk_score=risk,
            )
        )

        return paths

    async def reachable_assets(
        self,
        project_id: UUID,
        from_node_id: UUID,
        max_depth: int = 5,
    ) -> list[GraphNode]:
        """All nodes reachable from a starting node within max_depth hops.

        Returns only asset-type and technology-type nodes (excludes
        findings and evidence for a cleaner attack surface view).
        """
        all_reachable = await self._graph.blast_radius(
            project_id, from_node_id, max_depth
        )
        return [
            n
            for n in all_reachable
            if n.node_type in {GraphNodeType.ASSET, GraphNodeType.TECHNOLOGY}
        ]

    async def crown_jewel_analysis(
        self,
        project_id: UUID,
        crown_jewel_node_id: UUID | None = None,
    ) -> list[CrownJewelResult]:
        """Identify crown-jewel nodes and compute paths TO them.

        If ``crown_jewel_node_id`` is given, analyze just that node.
        Otherwise analyze all credential-type nodes.
        """
        if crown_jewel_node_id is not None:
            node = await self._graph.get_node(crown_jewel_node_id)
            if node is None:
                return []
            crown_jewels = [node]
        else:
            all_nodes = await self._graph.list_nodes_for_project(
                project_id, GraphNodeType.CREDENTIAL
            )
            crown_jewels = all_nodes

        results: list[CrownJewelResult] = []
        for cj in crown_jewels:
            reachable = await self._graph.blast_radius(project_id, cj.id, 10)

            paths_to_cj: list[list[GraphNode]] = []
            asset_nodes = [
                n
                for n in reachable
                if n.node_type == GraphNodeType.ASSET
            ]
            for asset in asset_nodes[:10]:
                path = await self._graph.shortest_path(asset.id, cj.id, 10)
                if path is not None:
                    paths_to_cj.append(path)

            results.append(
                CrownJewelResult(
                    crown_jewel=cj,
                    reachable_from=reachable,
                    paths_to=paths_to_cj,
                )
            )

        return results

    async def lateral_movement_chains(
        self,
        project_id: UUID,
        from_node_id: UUID | None = None,
        max_depth: int = 6,
    ) -> list[LateralMovementChain]:
        """Discover lateral movement chains through the graph.

        If ``from_node_id`` is given, start from that node. Otherwise
        start from all asset nodes in the project.
        """
        if from_node_id is not None:
            single = await self._graph.get_node(from_node_id)
            start_nodes: list[GraphNode] = [single] if single is not None else []
        else:
            start_nodes = await self._graph.list_nodes_for_project(
                project_id, GraphNodeType.ASSET
            )

        chains: list[LateralMovementChain] = []
        seen_pairs: set[tuple[UUID, UUID]] = set()

        for start in start_nodes:
            neighbors = await self._graph.get_neighbors(
                start.id,
                edge_type=None,
                direction="outgoing",
            )
            for neighbor in neighbors:
                if neighbor.id in seen_pairs or neighbor.id == start.id:
                    continue
                if neighbor.node_type not in {
                    GraphNodeType.ASSET,
                    GraphNodeType.TECHNOLOGY,
                }:
                    continue
                seen_pairs.add((start.id, neighbor.id))

                path = await self._graph.shortest_path(
                    start.id, neighbor.id, max_depth
                )
                if path is not None and len(path) >= 2:
                    edge_types = await self._infer_edge_types(path)
                    if any(
                        et in _LATERAL_MOVEMENT_EDGES for et in edge_types
                    ):
                        chains.append(
                            LateralMovementChain(
                                source_asset=start,
                                target_asset=neighbor,
                                hops=path,
                                edge_types=edge_types,
                                chain_length=len(path),
                            )
                        )

        return chains

    async def _infer_edge_types(
        self, path: list[GraphNode]
    ) -> list[GraphEdgeType]:
        """Infer the edge types between consecutive nodes in a path."""
        if len(path) < 2:
            return []

        edge_types: list[GraphEdgeType] = []
        for i in range(len(path) - 1):
            from_id = path[i].id
            to_id = path[i + 1].id
            found_type = GraphEdgeType.COMMUNICATES_WITH

            neighbors = await self._graph.get_neighbors(
                from_id, direction="outgoing"
            )
            for _ in neighbors:
                break

            edge_type = await self._find_edge_type(from_id, to_id)
            if edge_type is not None:
                found_type = edge_type
            edge_types.append(found_type)

        return edge_types

    async def _find_edge_type(
        self, from_id: UUID, to_id: UUID
    ) -> GraphEdgeType | None:
        """Find the edge type for a specific directed edge."""
        for et in GraphEdgeType:
            edges = await self._graph.list_edges_for_project(
                UUID(int=0), et
            )
            for edge in edges:
                if edge.from_node_id == from_id and edge.to_node_id == to_id:
                    return et
        return None

    def _compute_risk_score(
        self, path: list[GraphNode], edge_types: list[GraphEdgeType]
    ) -> float:
        """Compute a risk score for an attack path.

        Higher score = easier/more dangerous path. Factors:
        - Shorter paths score higher (fewer hops = easier)
        - Paths ending at credentials score higher
        - Paths with communicative edges score higher
        """
        if not path:
            return 0.0

        length_penalty = 1.0 / max(len(path), 1)

        credential_bonus = 0.0
        if path[-1].node_type == GraphNodeType.CREDENTIAL:
            credential_bonus = 0.4

        lateral_bonus = sum(
            0.1 for et in edge_types if et in _LATERAL_MOVEMENT_EDGES
        )

        return min(length_penalty + credential_bonus + lateral_bonus, 1.0)
