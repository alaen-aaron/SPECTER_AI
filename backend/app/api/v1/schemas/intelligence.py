"""Pydantic schemas for Intelligence API responses (Milestone 4.5)."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AttackPathResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    node_ids: list[UUID]
    node_labels: list[str]
    length: int
    risk_score: float


class LateralMovementChainResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_label: str
    target_label: str
    hops: list[UUID]
    hop_labels: list[str]
    chain_length: int


class CrownJewelResultResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    crown_jewel_id: UUID
    crown_jewel_label: str
    reachable_count: int
    paths_to_count: int


class ImpactAnalysisResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_node_id: UUID
    source_label: str
    source_type: str
    affected_assets: list[UUID]
    affected_asset_labels: list[str]
    downstream_findings: list[UUID]
    downstream_finding_labels: list[str]
    blast_radius_count: int
    confidence: float
    risk_level: str


class AssetDeltaResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    new_count: int
    disappeared_count: int
    stable_count: int
    surface_change_percent: float


class FindingTrendResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    severity: str
    current_count: int
    previous_count: int
    delta: int
    trend_direction: str


class RecurringFindingResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    title: str
    severity: str
    occurrence_count: int


class TechnologyChangeResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    technology: str
    change_type: str


class HistoricalReportResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    asset_delta: AssetDeltaResponse
    finding_trends: list[FindingTrendResponse]
    recurring_findings: list[RecurringFindingResponse]
    technology_changes: list[TechnologyChangeResponse]
    scan_count: int
    surface_expanding: bool


class RiskAssetResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    node_id: UUID
    label: str
    risk_score: float
    finding_count: int
    connection_count: int


class ConnectedAssetResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    node_id: UUID
    label: str
    connection_count: int
    connected_types: list[str]


class TechnologyExposureResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    node_id: UUID
    label: str
    exposure_score: float
    connected_finding_count: int


class AttackChainResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    node_ids: list[UUID]
    node_labels: list[str]
    chain_length: int
    risk_score: float


class GraphGrowthResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    period: str
    nodes_added: int
    edges_added: int
    total_nodes: int
    total_edges: int


class ExecutiveReportResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    highest_risk_assets: list[RiskAssetResponse]
    most_connected_assets: list[ConnectedAssetResponse]
    findings_by_surface: list[dict[str, object]]
    technology_exposure: list[TechnologyExposureResponse]
    top_attack_chains: list[AttackChainResponse]
    graph_growth: list[GraphGrowthResponse]
    total_nodes: int
    total_edges: int
    total_findings: int
    total_assets: int
