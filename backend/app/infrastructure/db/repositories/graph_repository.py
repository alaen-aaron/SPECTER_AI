"""SQLAlchemy implementation of `GraphRepository` (SRS §15A)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession as SqlAsyncSession

from app.domain.entities import GraphEdge, GraphNode
from app.domain.value_objects import GraphEdgeType, GraphNodeType
from app.infrastructure.db.models.graph import GraphEdgeModel, GraphNodeModel


def _node_to_entity(row: GraphNodeModel) -> GraphNode:
    return GraphNode(
        id=row.id,
        project_id=row.project_id,
        node_type=GraphNodeType(row.node_type),
        source_table=row.source_table,
        source_id=row.source_id,
        label=row.label,
        properties=row.properties or {},
        created_at=row.created_at,
    )


def _edge_to_entity(row: GraphEdgeModel) -> GraphEdge:
    return GraphEdge(
        id=row.id,
        project_id=row.project_id,
        from_node_id=row.from_node_id,
        to_node_id=row.to_node_id,
        relationship_type=GraphEdgeType(row.relationship_type),
        weight=row.weight,
        properties=row.properties or {},
        created_at=row.created_at,
    )


class SqlAlchemyGraphRepository:
    def __init__(self, session: SqlAsyncSession) -> None:
        self._session = session

    async def upsert_node(self, node: GraphNode) -> GraphNode:
        existing = await self.find_node(
            node.project_id, node.node_type, node.source_table, node.source_id
        )
        if existing is not None:
            stmt = (
                select(GraphNodeModel).where(GraphNodeModel.id == existing.id)
            )
            result = await self._session.execute(stmt)
            model = result.scalar_one()
            model.label = node.label
            model.properties = node.properties or {}
            await self._session.flush()
            return _node_to_entity(model)

        model = GraphNodeModel(
            id=node.id,
            project_id=node.project_id,
            node_type=node.node_type.value,
            source_table=node.source_table,
            source_id=node.source_id,
            label=node.label,
            properties=node.properties or {},
        )
        self._session.add(model)
        await self._session.flush()
        return _node_to_entity(model)

    async def upsert_edge(self, edge: GraphEdge) -> GraphEdge:
        existing = await self.find_edge(
            edge.project_id,
            edge.from_node_id,
            edge.to_node_id,
            edge.relationship_type,
        )
        if existing is not None:
            stmt = select(GraphEdgeModel).where(GraphEdgeModel.id == existing.id)
            result = await self._session.execute(stmt)
            model = result.scalar_one()
            model.weight = edge.weight
            model.properties = edge.properties or {}
            await self._session.flush()
            return _edge_to_entity(model)

        model = GraphEdgeModel(
            id=edge.id,
            project_id=edge.project_id,
            from_node_id=edge.from_node_id,
            to_node_id=edge.to_node_id,
            relationship_type=edge.relationship_type.value,
            weight=edge.weight,
            properties=edge.properties or {},
        )
        self._session.add(model)
        await self._session.flush()
        return _edge_to_entity(model)

    async def get_node(self, node_id: UUID) -> GraphNode | None:
        row = await self._session.get(GraphNodeModel, node_id)
        return _node_to_entity(row) if row else None

    async def get_edge(self, edge_id: UUID) -> GraphEdge | None:
        row = await self._session.get(GraphEdgeModel, edge_id)
        return _edge_to_entity(row) if row else None

    async def find_node(
        self,
        project_id: UUID,
        node_type: GraphNodeType,
        source_table: str,
        source_id: UUID,
    ) -> GraphNode | None:
        stmt = select(GraphNodeModel).where(
            GraphNodeModel.project_id == project_id,
            GraphNodeModel.node_type == node_type.value,
            GraphNodeModel.source_table == source_table,
            GraphNodeModel.source_id == source_id,
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        return _node_to_entity(row) if row else None

    async def find_edge(
        self,
        project_id: UUID,
        from_node_id: UUID,
        to_node_id: UUID,
        relationship_type: GraphEdgeType,
    ) -> GraphEdge | None:
        stmt = select(GraphEdgeModel).where(
            GraphEdgeModel.project_id == project_id,
            GraphEdgeModel.from_node_id == from_node_id,
            GraphEdgeModel.to_node_id == to_node_id,
            GraphEdgeModel.relationship_type == relationship_type.value,
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        return _edge_to_entity(row) if row else None

    async def get_neighbors(
        self,
        node_id: UUID,
        edge_type: GraphEdgeType | None = None,
        direction: str = "outgoing",
    ) -> list[GraphNode]:
        if direction == "outgoing":
            stmt = (
                select(GraphNodeModel)
                .join(GraphEdgeModel, GraphEdgeModel.to_node_id == GraphNodeModel.id)
                .where(GraphEdgeModel.from_node_id == node_id)
            )
        else:
            stmt = (
                select(GraphNodeModel)
                .join(GraphEdgeModel, GraphEdgeModel.from_node_id == GraphNodeModel.id)
                .where(GraphEdgeModel.to_node_id == node_id)
            )

        if edge_type is not None:
            stmt = stmt.where(GraphEdgeModel.relationship_type == edge_type.value)

        result = await self._session.execute(stmt)
        return [_node_to_entity(row) for row in result.scalars().all()]

    async def shortest_path(
        self,
        from_node_id: UUID,
        to_node_id: UUID,
        max_depth: int = 10,
    ) -> list[GraphNode] | None:
        """BFS shortest path using recursive CTE."""
        from sqlalchemy import text

        # Use a recursive CTE for BFS
        cte_query = text("""
            WITH RECURSIVE graph_path AS (
                SELECT
                    n.id as node_id,
                    ARRAY[n.id] as path,
                    0 as depth
                FROM graph_nodes n
                WHERE n.id = :start_id

                UNION ALL

                SELECT
                    e.to_node_id as node_id,
                    gp.path || e.to_node_id,
                    gp.depth + 1
                FROM graph_path gp
                JOIN graph_edges e ON e.from_node_id = gp.node_id
                WHERE gp.depth < :max_depth
                    AND e.to_node_id != ALL(gp.path)
            )
            SELECT node_id FROM graph_path
            WHERE node_id = :end_id
            ORDER BY depth
            LIMIT 1
        """)

        result = await self._session.execute(
            cte_query, {"start_id": from_node_id, "end_id": to_node_id, "max_depth": max_depth}
        )
        row = result.fetchone()
        if row is None:
            return None

        # Now reconstruct the full path
        path_query = text("""
            WITH RECURSIVE graph_path AS (
                SELECT
                    n.id as node_id,
                    ARRAY[n.id] as path,
                    0 as depth
                FROM graph_nodes n
                WHERE n.id = :start_id

                UNION ALL

                SELECT
                    e.to_node_id as node_id,
                    gp.path || e.to_node_id,
                    gp.depth + 1
                FROM graph_path gp
                JOIN graph_edges e ON e.from_node_id = gp.node_id
                WHERE gp.depth < :max_depth
                    AND e.to_node_id != ALL(gp.path)
            )
            SELECT node_id FROM graph_path
            WHERE node_id = :end_id
            ORDER BY depth
            LIMIT 1
        """)

        result = await self._session.execute(
            path_query, {"start_id": from_node_id, "end_id": to_node_id, "max_depth": max_depth}
        )

        # Reconstruct path from the path array
        cte_full = text("""
            WITH RECURSIVE graph_path AS (
                SELECT
                    n.id as node_id,
                    ARRAY[n.id] as path,
                    0 as depth
                FROM graph_nodes n
                WHERE n.id = :start_id

                UNION ALL

                SELECT
                    e.to_node_id as node_id,
                    gp.path || e.to_node_id,
                    gp.depth + 1
                FROM graph_path gp
                JOIN graph_edges e ON e.from_node_id = gp.node_id
                WHERE gp.depth < :max_depth
                    AND e.to_node_id != ALL(gp.path)
            )
            SELECT path FROM graph_path
            WHERE node_id = :end_id
            ORDER BY depth
            LIMIT 1
        """)
        result = await self._session.execute(
            cte_full, {"start_id": from_node_id, "end_id": to_node_id, "max_depth": max_depth}
        )
        path_row = result.fetchone()
        if path_row is None:
            return None

        path_ids = path_row[0]
        nodes: list[GraphNode] = []
        for nid in path_ids:
            node = await self.get_node(nid)
            if node is not None:
                nodes.append(node)
        return nodes

    async def list_nodes_for_project(
        self,
        project_id: UUID,
        node_type: GraphNodeType | None = None,
    ) -> list[GraphNode]:
        stmt = select(GraphNodeModel).where(GraphNodeModel.project_id == project_id)
        if node_type is not None:
            stmt = stmt.where(GraphNodeModel.node_type == node_type.value)
        stmt = stmt.order_by(GraphNodeModel.created_at.desc())
        result = await self._session.execute(stmt)
        return [_node_to_entity(row) for row in result.scalars().all()]

    async def list_edges_for_project(
        self,
        project_id: UUID,
        relationship_type: GraphEdgeType | None = None,
    ) -> list[GraphEdge]:
        stmt = select(GraphEdgeModel).where(GraphEdgeModel.project_id == project_id)
        if relationship_type is not None:
            stmt = stmt.where(
                GraphEdgeModel.relationship_type == relationship_type.value
            )
        stmt = stmt.order_by(GraphEdgeModel.created_at.desc())
        result = await self._session.execute(stmt)
        return [_edge_to_entity(row) for row in result.scalars().all()]

    async def remove_node(self, node_id: UUID) -> None:
        await self._session.execute(
            delete(GraphEdgeModel).where(
                or_(
                    GraphEdgeModel.from_node_id == node_id,
                    GraphEdgeModel.to_node_id == node_id,
                )
            )
        )
        await self._session.execute(
            delete(GraphNodeModel).where(GraphNodeModel.id == node_id)
        )
        await self._session.flush()

    async def remove_edge(self, edge_id: UUID) -> None:
        await self._session.execute(
            delete(GraphEdgeModel).where(GraphEdgeModel.id == edge_id)
        )
        await self._session.flush()

    async def remove_edges_for_node(self, node_id: UUID) -> None:
        await self._session.execute(
            delete(GraphEdgeModel).where(
                or_(
                    GraphEdgeModel.from_node_id == node_id,
                    GraphEdgeModel.to_node_id == node_id,
                )
            )
        )
        await self._session.flush()

    async def clear_project(self, project_id: UUID) -> None:
        await self._session.execute(
            delete(GraphEdgeModel).where(GraphEdgeModel.project_id == project_id)
        )
        await self._session.execute(
            delete(GraphNodeModel).where(GraphNodeModel.project_id == project_id)
        )
        await self._session.flush()
