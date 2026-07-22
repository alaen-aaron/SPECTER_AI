"""Application service: Knowledge Graph operations (SRS §15A)."""

from __future__ import annotations

from uuid import UUID, uuid4

from app.domain.entities import GraphEdge, GraphNode
from app.domain.repositories import GraphRepository
from app.domain.value_objects import GraphEdgeType, GraphNodeType


class GraphService:
    def __init__(self, graph_repo: GraphRepository) -> None:
        self._repo = graph_repo

    async def upsert_asset_node(
        self, project_id: UUID, asset_id: UUID, label: str, **properties: object
    ) -> GraphNode:
        node = GraphNode(
            id=uuid4(),
            project_id=project_id,
            node_type=GraphNodeType.ASSET,
            source_table="assets",
            source_id=asset_id,
            label=label,
            properties=properties,
        )
        return await self._repo.upsert_node(node)

    async def upsert_finding_node(
        self, project_id: UUID, finding_id: UUID, label: str, **properties: object
    ) -> GraphNode:
        node = GraphNode(
            id=uuid4(),
            project_id=project_id,
            node_type=GraphNodeType.FINDING,
            source_table="findings",
            source_id=finding_id,
            label=label,
            properties=properties,
        )
        return await self._repo.upsert_node(node)

    async def upsert_credential_node(
        self, project_id: UUID, credential_id: UUID, label: str, **properties: object
    ) -> GraphNode:
        node = GraphNode(
            id=uuid4(),
            project_id=project_id,
            node_type=GraphNodeType.CREDENTIAL,
            source_table="credentials",
            source_id=credential_id,
            label=label,
            properties=properties,
        )
        return await self._repo.upsert_node(node)

    async def upsert_technology_node(
        self, project_id: UUID, asset_id: UUID, label: str, **properties: object
    ) -> GraphNode:
        node = GraphNode(
            id=uuid4(),
            project_id=project_id,
            node_type=GraphNodeType.TECHNOLOGY,
            source_table="assets",
            source_id=asset_id,
            label=label,
            properties=properties,
        )
        return await self._repo.upsert_node(node)

    async def upsert_evidence_node(
        self, project_id: UUID, evidence_id: UUID, label: str, **properties: object
    ) -> GraphNode:
        node = GraphNode(
            id=uuid4(),
            project_id=project_id,
            node_type=GraphNodeType.EVIDENCE,
            source_table="evidence",
            source_id=evidence_id,
            label=label,
            properties=properties,
        )
        return await self._repo.upsert_node(node)

    async def add_edge(
        self,
        project_id: UUID,
        from_node_id: UUID,
        to_node_id: UUID,
        relationship_type: GraphEdgeType,
        weight: float = 1.0,
        **properties: object,
    ) -> GraphEdge:
        edge = GraphEdge(
            id=uuid4(),
            project_id=project_id,
            from_node_id=from_node_id,
            to_node_id=to_node_id,
            relationship_type=relationship_type,
            weight=weight,
            properties=properties,
        )
        return await self._repo.upsert_edge(edge)

    async def get_node(self, node_id: UUID) -> GraphNode:
        node = await self._repo.get_node(node_id)
        if node is None:
            from app.domain.exceptions import GraphNodeNotFoundError
            raise GraphNodeNotFoundError(node_id)
        return node

    async def get_edge(self, edge_id: UUID) -> GraphEdge:
        edge = await self._repo.get_edge(edge_id)
        if edge is None:
            from app.domain.exceptions import GraphEdgeNotFoundError
            raise GraphEdgeNotFoundError(edge_id)
        return edge

    async def find_node_by_source(
        self, project_id: UUID, source_table: str, source_id: UUID
    ) -> GraphNode | None:
        """Find a graph node by its source table and source id."""
        from app.domain.value_objects import GraphNodeType
        for nt in GraphNodeType:
            node = await self._repo.find_node(project_id, nt, source_table, source_id)
            if node is not None:
                return node
        return None

    async def get_neighbors(
        self,
        node_id: UUID,
        edge_type: GraphEdgeType | None = None,
        direction: str = "outgoing",
    ) -> list[GraphNode]:
        return await self._repo.get_neighbors(node_id, edge_type, direction)

    async def shortest_path(
        self, from_node_id: UUID, to_node_id: UUID, max_depth: int = 10
    ) -> list[GraphNode] | None:
        return await self._repo.shortest_path(from_node_id, to_node_id, max_depth)

    async def list_nodes(
        self,
        project_id: UUID,
        node_type: GraphNodeType | None = None,
    ) -> list[GraphNode]:
        return await self._repo.list_nodes_for_project(project_id, node_type)

    async def list_edges(
        self,
        project_id: UUID,
        relationship_type: GraphEdgeType | None = None,
    ) -> list[GraphEdge]:
        return await self._repo.list_edges_for_project(project_id, relationship_type)

    async def remove_node(self, node_id: UUID) -> None:
        await self._repo.remove_node(node_id)

    async def remove_edge(self, edge_id: UUID) -> None:
        await self._repo.remove_edge(edge_id)

    async def clear_project(self, project_id: UUID) -> None:
        await self._repo.clear_project(project_id)
