"""
Analyzer / Correlation Engine (SRS §8.1, FR-7.2).

Deduplicates findings across tools (via dedup_key), links related
findings into attack chains. Consumes domain events, may merge into
existing findings via dedup_key.

Milestone 4.5: Graph-aware correlation — uses Knowledge Graph
relationships to find related findings that share graph connections,
not just dedup keys.
"""

from __future__ import annotations

from uuid import UUID

from app.domain.entities import Finding
from app.domain.repositories import FindingRepository, GraphRepository
from app.domain.value_objects import GraphNodeType


class AnalyzerService:
    """
    Correlates findings across tools to reduce duplicate/noisy findings
    into unified Findings.

    The analyzer queries the finding repository for existing findings
    with the same dedup_key and merges tool_result_ids when duplicates
    are found.

    Milestone 4.5: When a GraphRepository is available, also finds
    graph-connected findings via shared blast radius and edge relationships.
    """

    def __init__(
        self,
        finding_repo: FindingRepository,
        graph_repo: GraphRepository | None = None,
    ) -> None:
        self._finding_repo = finding_repo
        self._graph_repo = graph_repo

    async def correlate_findings(
        self,
        project_id: UUID,
    ) -> dict[str, object]:
        """
        Run correlation analysis on all findings in a project.

        Returns a summary of correlations found.
        """
        findings = await self._finding_repo.list_for_project(project_id, limit=1000)

        dedup_groups: dict[str, list[Finding]] = {}
        for finding in findings:
            key = finding.dedup_key or f"{finding.title}:{finding.severity.value}"
            dedup_groups.setdefault(key, []).append(finding)

        correlations = 0
        merged_count = 0

        for _key, group in dedup_groups.items():
            if len(group) > 1:
                correlations += 1
                primary = group[0]
                for duplicate in group[1:]:
                    merged_tool_ids = list(
                        set(primary.tool_result_ids + duplicate.tool_result_ids)
                    )
                    primary.tool_result_ids = merged_tool_ids
                    merged_count += 1

        return {
            "total_findings": len(findings),
            "unique_findings": len(dedup_groups),
            "correlations_found": correlations,
            "duplicates_merged": merged_count,
        }

    async def get_finding_correlations(
        self,
        finding_id: UUID,
    ) -> list[dict[str, object]]:
        """Get all findings correlated with a specific finding.

        Combines tool-result-based correlation with graph-based
        correlation (findings that share blast radius or are connected
        via graph edges).
        """
        finding = await self._finding_repo.get(finding_id)
        if finding is None:
            return []

        all_findings = await self._finding_repo.list_for_project(
            finding.project_id, limit=1000
        )

        related: list[dict[str, object]] = []
        seen_ids: set[UUID] = set()

        for other in all_findings:
            if other.id == finding_id:
                continue
            shared_tool_results = set(finding.tool_result_ids) & set(other.tool_result_ids)
            if shared_tool_results:
                related.append({
                    "finding_id": str(other.id),
                    "title": other.title,
                    "severity": other.severity.value,
                    "shared_tool_results": len(shared_tool_results),
                    "correlation_type": "tool_result",
                })
                seen_ids.add(other.id)

        if self._graph_repo is not None:
            graph_related = await self._graph_based_correlations(
                finding, all_findings, seen_ids
            )
            related.extend(graph_related)

        return related

    async def _graph_based_correlations(
        self,
        finding: Finding,
        all_findings: list[Finding],
        seen_ids: set[UUID],
    ) -> list[dict[str, object]]:
        """Find graph-connected findings via blast radius overlap."""
        if self._graph_repo is None:
            return []

        finding_node = await self._graph_repo.find_node(
            finding.project_id,
            GraphNodeType.FINDING,
            "findings",
            finding.id,
        )
        if finding_node is None:
            return []

        finding_blast = await self._graph_repo.blast_radius(
            finding.project_id, finding_node.id, max_depth=3
        )
        finding_blast_ids = {n.source_id for n in finding_blast}

        related: list[dict[str, object]] = []
        for other in all_findings:
            if other.id == finding.id or other.id in seen_ids:
                continue

            other_node = await self._graph_repo.find_node(
                other.project_id,
                GraphNodeType.FINDING,
                "findings",
                other.id,
            )
            if other_node is None:
                continue

            other_blast = await self._graph_repo.blast_radius(
                other.project_id, other_node.id, max_depth=3
            )
            other_blast_ids = {n.source_id for n in other_blast}

            overlap = finding_blast_ids & other_blast_ids
            if overlap:
                related.append({
                    "finding_id": str(other.id),
                    "title": other.title,
                    "severity": other.severity.value,
                    "shared_assets": len(overlap),
                    "correlation_type": "graph_blast_radius",
                })

        return related
