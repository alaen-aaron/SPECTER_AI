"""Knowledge Graph endpoints (SRS §15A)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.v1.deps import (
    get_graph_service,
    require_project_role,
)
from app.api.v1.schemas.graph import (
    AddEdgeRequest,
    GraphEdgeResponse,
    GraphNodeResponse,
    GraphPathResponse,
)
from app.application.graph_service import GraphService
from app.domain.entities import GraphEdge, GraphNode, ProjectMember
from app.domain.value_objects import GraphEdgeType, GraphNodeType

router = APIRouter(tags=["knowledge-graph"])


@router.get(
    "/projects/{project_id}/graph/nodes",
    response_model=list[GraphNodeResponse],
    summary="List graph nodes for a project",
)
async def list_nodes(
    project_id: UUID,
    node_type: str | None = Query(default=None),
    _member: ProjectMember = Depends(require_project_role()),
    service: GraphService = Depends(get_graph_service),
) -> list[GraphNode]:
    nt = GraphNodeType(node_type) if node_type else None
    return await service.list_nodes(project_id, nt)


@router.get(
    "/projects/{project_id}/graph/edges",
    response_model=list[GraphEdgeResponse],
    summary="List graph edges for a project",
)
async def list_edges(
    project_id: UUID,
    relationship_type: str | None = Query(default=None),
    _member: ProjectMember = Depends(require_project_role()),
    service: GraphService = Depends(get_graph_service),
) -> list[GraphEdge]:
    rt = GraphEdgeType(relationship_type) if relationship_type else None
    return await service.list_edges(project_id, rt)


@router.get(
    "/graph/nodes/{node_id}",
    response_model=GraphNodeResponse,
    summary="Get a single graph node",
)
async def get_node(
    node_id: UUID,
    service: GraphService = Depends(get_graph_service),
) -> GraphNode:
    return await service.get_node(node_id)


@router.get(
    "/graph/nodes/{node_id}/neighbors",
    response_model=list[GraphNodeResponse],
    summary="Get neighbor nodes of a graph node",
)
async def get_neighbors(
    node_id: UUID,
    edge_type: str | None = Query(default=None),
    direction: str = Query(default="outgoing"),
    service: GraphService = Depends(get_graph_service),
) -> list[GraphNode]:
    et = GraphEdgeType(edge_type) if edge_type else None
    return await service.get_neighbors(node_id, et, direction)


@router.get(
    "/graph/shortest-path",
    response_model=GraphPathResponse,
    summary="Find shortest path between two nodes",
)
async def find_shortest_path(
    from_node_id: UUID = Query(...),
    to_node_id: UUID = Query(...),
    max_depth: int = Query(default=10, ge=1, le=20),
    service: GraphService = Depends(get_graph_service),
) -> GraphPathResponse:
    path = await service.shortest_path(from_node_id, to_node_id, max_depth)
    if path is None:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No path found between the specified nodes.",
        )
    return GraphPathResponse(
        nodes=[GraphNodeResponse.model_validate(n) for n in path],
        length=len(path),
    )


@router.post(
    "/projects/{project_id}/graph/edges",
    response_model=GraphEdgeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a graph edge",
)
async def add_edge(
    project_id: UUID,
    body: AddEdgeRequest,
    _member: ProjectMember = Depends(require_project_role()),
    service: GraphService = Depends(get_graph_service),
) -> GraphEdge:
    return await service.add_edge(
        project_id,
        body.from_node_id,
        body.to_node_id,
        body.relationship_type,
        body.weight,
    )


@router.delete(
    "/graph/nodes/{node_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete a graph node and its edges",
)
async def delete_node(
    node_id: UUID,
    service: GraphService = Depends(get_graph_service),
) -> Response:
    await service.remove_node(node_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/graph/edges/{edge_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete a graph edge",
)
async def delete_edge(
    edge_id: UUID,
    service: GraphService = Depends(get_graph_service),
) -> Response:
    await service.remove_edge(edge_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
