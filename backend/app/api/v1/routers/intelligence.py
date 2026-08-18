"""Intelligence API endpoints (Milestone 4.5 — Knowledge Graph Intelligence)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.v1.deps import (
    get_attack_path_service,
    get_executive_intelligence_service,
    get_historical_intelligence_service,
    get_impact_analysis_service,
    require_project_role,
)
from app.application.attack_path_service import AttackPathService
from app.application.executive_intelligence_service import (
    ExecutiveIntelligenceService,
)
from app.application.historical_intelligence_service import (
    HistoricalIntelligenceService,
)
from app.application.impact_analysis_service import ImpactAnalysisService
from app.domain.entities import ProjectMember

router = APIRouter(tags=["intelligence"])


# -------------------------------------------------------------------
# Attack Paths
# -------------------------------------------------------------------


@router.get(
    "/projects/{project_id}/intelligence/attack-paths",
    response_model=None,
    summary="Find shortest attack path between two nodes",
)
async def shortest_attack_path(
    project_id: UUID,
    from_node_id: UUID = Query(...),
    to_node_id: UUID = Query(...),
    max_depth: int = Query(default=10, ge=1, le=20),
    _member: ProjectMember = Depends(require_project_role()),
    service: AttackPathService = Depends(get_attack_path_service),
) -> dict[str, object] | Response:
    result = await service.shortest_attack_path(
        project_id, from_node_id, to_node_id, max_depth
    )
    if result is None:
        return Response(
            status_code=status.HTTP_404_NOT_FOUND,
            content='{"detail": "No attack path found."}',
            media_type="application/json",
        )
    return {
        "node_ids": [n.id for n in result.nodes],
        "node_labels": [n.label for n in result.nodes],
        "length": result.length,
        "risk_score": result.risk_score,
    }


@router.get(
    "/projects/{project_id}/intelligence/multiple-paths",
    summary="Find multiple candidate attack paths",
)
async def multiple_attack_paths(
    project_id: UUID,
    from_node_id: UUID = Query(...),
    to_node_id: UUID = Query(...),
    max_paths: int = Query(default=5, ge=1, le=10),
    max_depth: int = Query(default=10, ge=1, le=20),
    _member: ProjectMember = Depends(require_project_role()),
    service: AttackPathService = Depends(get_attack_path_service),
) -> list[dict[str, object]]:
    results = await service.multiple_attack_paths(
        project_id, from_node_id, to_node_id, max_paths, max_depth
    )
    return [
        {
            "node_ids": [n.id for n in r.nodes],
            "node_labels": [n.label for n in r.nodes],
            "length": r.length,
            "risk_score": r.risk_score,
        }
        for r in results
    ]


@router.get(
    "/projects/{project_id}/intelligence/reachable",
    summary="Get all reachable assets from a starting node",
)
async def reachable_assets(
    project_id: UUID,
    from_node_id: UUID = Query(...),
    max_depth: int = Query(default=5, ge=1, le=20),
    _member: ProjectMember = Depends(require_project_role()),
    service: AttackPathService = Depends(get_attack_path_service),
) -> dict[str, object]:
    nodes = await service.reachable_assets(
        project_id, from_node_id, max_depth
    )
    return {
        "count": len(nodes),
        "nodes": [
            {"id": n.id, "label": n.label, "type": n.node_type.value}
            for n in nodes
        ],
    }


@router.get(
    "/projects/{project_id}/intelligence/crown-jewels",
    summary="Crown jewel analysis — paths to high-value targets",
)
async def crown_jewel_analysis(
    project_id: UUID,
    node_id: UUID | None = Query(default=None),
    _member: ProjectMember = Depends(require_project_role()),
    service: AttackPathService = Depends(get_attack_path_service),
) -> list[dict[str, object]]:
    results = await service.crown_jewel_analysis(project_id, node_id)
    return [
        {
            "crown_jewel_id": r.crown_jewel.id,
            "crown_jewel_label": r.crown_jewel.label,
            "reachable_count": len(r.reachable_from),
            "paths_to_count": len(r.paths_to),
        }
        for r in results
    ]


@router.get(
    "/projects/{project_id}/intelligence/lateral-movement",
    summary="Discover lateral movement chains",
)
async def lateral_movement_chains(
    project_id: UUID,
    from_node_id: UUID | None = Query(default=None),
    max_depth: int = Query(default=6, ge=1, le=20),
    _member: ProjectMember = Depends(require_project_role()),
    service: AttackPathService = Depends(get_attack_path_service),
) -> list[dict[str, object]]:
    chains = await service.lateral_movement_chains(
        project_id, from_node_id, max_depth
    )
    return [
        {
            "source_label": c.source_asset.label,
            "target_label": c.target_asset.label,
            "hops": [n.id for n in c.hops],
            "hop_labels": [n.label for n in c.hops],
            "chain_length": c.chain_length,
        }
        for c in chains
    ]


# -------------------------------------------------------------------
# Impact Analysis
# -------------------------------------------------------------------


@router.get(
    "/projects/{project_id}/intelligence/impact",
    response_model=None,
    summary="Impact analysis for a graph node",
)
async def impact_analysis(
    project_id: UUID,
    node_id: UUID = Query(...),
    max_depth: int = Query(default=5, ge=1, le=20),
    _member: ProjectMember = Depends(require_project_role()),
    service: ImpactAnalysisService = Depends(get_impact_analysis_service),
) -> dict[str, object] | Response:
    result = await service.analyze(project_id, node_id, max_depth)
    if result is None:
        return Response(
            status_code=status.HTTP_404_NOT_FOUND,
            content='{"detail": "Node not found."}',
            media_type="application/json",
        )
    return {
        "source_node_id": result.source_node.id,
        "source_label": result.source_node.label,
        "source_type": result.source_node.node_type.value,
        "affected_assets": [n.id for n in result.affected_assets],
        "affected_asset_labels": [n.label for n in result.affected_assets],
        "downstream_findings": [n.id for n in result.downstream_findings],
        "downstream_finding_labels": [
            n.label for n in result.downstream_findings
        ],
        "blast_radius_count": result.blast_radius_count,
        "confidence": result.confidence,
        "risk_level": result.risk_level,
    }


# -------------------------------------------------------------------
# Historical Intelligence
# -------------------------------------------------------------------


@router.get(
    "/projects/{project_id}/intelligence/historical",
    summary="Historical intelligence report",
)
async def historical_report(
    project_id: UUID,
    _member: ProjectMember = Depends(require_project_role()),
    service: HistoricalIntelligenceService = Depends(
        get_historical_intelligence_service
    ),
) -> dict[str, object]:
    report = await service.generate_report(project_id)
    return {
        "asset_delta": {
            "new_count": report.asset_delta.new_count,
            "disappeared_count": report.asset_delta.disappeared_count,
            "stable_count": len(report.asset_delta.stable_assets),
            "surface_change_percent": report.asset_delta.surface_change_percent,
        },
        "finding_trends": [
            {
                "severity": t.severity,
                "current_count": t.current_count,
                "previous_count": t.previous_count,
                "delta": t.delta,
                "trend_direction": t.trend_direction,
            }
            for t in report.finding_trends
        ],
        "recurring_findings": [
            {
                "title": r.title,
                "severity": r.severity,
                "occurrence_count": r.occurrence_count,
            }
            for r in report.recurring_findings
        ],
        "technology_changes": [
            {
                "technology": tc.technology,
                "change_type": tc.change_type,
            }
            for tc in report.technology_changes
        ],
        "scan_count": report.scan_count,
        "surface_expanding": report.surface_expanding,
    }


# -------------------------------------------------------------------
# Executive Intelligence
# -------------------------------------------------------------------


@router.get(
    "/projects/{project_id}/intelligence/executive",
    summary="Executive intelligence report for dashboards",
)
async def executive_report(
    project_id: UUID,
    _member: ProjectMember = Depends(require_project_role()),
    service: ExecutiveIntelligenceService = Depends(
        get_executive_intelligence_service
    ),
) -> dict[str, object]:
    report = await service.generate_report(project_id)
    return {
        "highest_risk_assets": [
            {
                "node_id": ra.node.id,
                "label": ra.node.label,
                "risk_score": ra.risk_score,
                "finding_count": ra.finding_count,
                "connection_count": ra.connection_count,
            }
            for ra in report.highest_risk_assets
        ],
        "most_connected_assets": [
            {
                "node_id": ca.node.id,
                "label": ca.node.label,
                "connection_count": ca.connection_count,
                "connected_types": ca.connected_types,
            }
            for ca in report.most_connected_assets
        ],
        "findings_by_surface": [
            {
                "edge_type": sf.edge_type,
                "finding_count": sf.finding_count,
            }
            for sf in report.findings_by_surface
        ],
        "technology_exposure": [
            {
                "node_id": te.node.id,
                "label": te.node.label,
                "exposure_score": te.exposure_score,
                "connected_finding_count": te.connected_finding_count,
            }
            for te in report.technology_exposure
        ],
        "top_attack_chains": [
            {
                "node_ids": [n.id for n in ac.nodes],
                "node_labels": [n.label for n in ac.nodes],
                "chain_length": ac.chain_length,
                "risk_score": ac.risk_score,
            }
            for ac in report.top_attack_chains
        ],
        "graph_growth": [
            {
                "period": g.period,
                "nodes_added": g.nodes_added,
                "edges_added": g.edges_added,
                "total_nodes": g.total_nodes,
                "total_edges": g.total_edges,
            }
            for g in report.graph_growth
        ],
        "total_nodes": report.total_nodes,
        "total_edges": report.total_edges,
        "total_findings": report.total_findings,
        "total_assets": report.total_assets,
    }
