"""Pydantic v2 request/response schemas for Knowledge Graph endpoints."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.domain.value_objects import GraphEdgeType, GraphNodeType


class GraphNodeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    project_id: UUID
    node_type: GraphNodeType
    source_table: str
    source_id: UUID
    label: str
    properties: dict[str, object]
    created_at: datetime | None


class GraphEdgeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    project_id: UUID
    from_node_id: UUID
    to_node_id: UUID
    relationship_type: GraphEdgeType
    weight: float
    properties: dict[str, object]
    created_at: datetime | None


class AddEdgeRequest(BaseModel):
    from_node_id: UUID
    to_node_id: UUID
    relationship_type: GraphEdgeType
    weight: float = 1.0


class GraphPathResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    nodes: list[GraphNodeResponse]
    length: int
