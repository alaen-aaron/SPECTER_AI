"""
Historical Intelligence Service (Milestone 4.5 — Knowledge Graph Intelligence).

Uses previous scans together with graph relationships to identify
trends: newly introduced assets, disappeared assets, recurring
findings, expanding attack surface, technology changes, and
finding trends over time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.entities import Finding, GraphNode
from app.domain.repositories import (
    AssetRepository,
    FindingRepository,
    GraphRepository,
    ScanRepository,
)
from app.domain.value_objects import GraphNodeType, ScanStatus


@dataclass(frozen=True)
class AssetDelta:
    """Change in asset inventory between two points in time."""

    new_assets: list[GraphNode]
    disappeared_assets: list[GraphNode]
    stable_assets: list[GraphNode]
    new_count: int
    disappeared_count: int
    surface_change_percent: float


@dataclass(frozen=True)
class FindingTrend:
    """Trend in finding counts over time."""

    severity: str
    current_count: int
    previous_count: int
    delta: int
    trend_direction: str  # "increasing", "decreasing", "stable"


@dataclass(frozen=True)
class RecurringFinding:
    """A finding that appears across multiple scans."""

    title: str
    severity: str
    occurrence_count: int
    first_seen: datetime | None
    last_seen: datetime | None
    scan_ids: list[UUID]


@dataclass(frozen=True)
class TechnologyChange:
    """A change in technology stack detected between scans."""

    technology: str
    change_type: str  # "added", "removed"
    detected_at: datetime | None


@dataclass(frozen=True)
class HistoricalReport:
    """Complete historical intelligence report for a project."""

    asset_delta: AssetDelta
    finding_trends: list[FindingTrend]
    recurring_findings: list[RecurringFinding]
    technology_changes: list[TechnologyChange]
    scan_count: int
    surface_expanding: bool


class HistoricalIntelligenceService:
    """Analyzes historical scan and graph data to identify trends.

    Depends only on domain interfaces — no infrastructure imports.
    """

    def __init__(
        self,
        graph_repo: GraphRepository,
        asset_repo: AssetRepository,
        finding_repo: FindingRepository,
        scan_repo: ScanRepository,
    ) -> None:
        self._graph = graph_repo
        self._assets = asset_repo
        self._findings = finding_repo
        self._scans = scan_repo

    async def compute_asset_delta(
        self,
        project_id: UUID,
        before_scan_id: UUID | None = None,
        after_scan_id: UUID | None = None,
    ) -> AssetDelta:
        """Compare asset inventory between two scan points.

        If scan IDs are not provided, compares the two most recent
        completed scans.
        """
        scans = await self._scans.list(project_id, limit=100)
        completed = [
            s
            for s in scans
            if s.status == ScanStatus.COMPLETED
        ]

        if len(completed) < 2:
            all_nodes = await self._graph.list_nodes_for_project(
                project_id, GraphNodeType.ASSET
            )
            return AssetDelta(
                new_assets=[],
                disappeared_assets=[],
                stable_assets=all_nodes,
                new_count=0,
                disappeared_count=0,
                surface_change_percent=0.0,
            )

        if before_scan_id and after_scan_id:
            before_scan = await self._scans.get(before_scan_id)
            after_scan = await self._scans.get(after_scan_id)
        else:
            after_scan = completed[0]
            before_scan = completed[1]

        before_assets = set()
        after_assets = set()

        if before_scan:
            for tid in before_scan.target_ids:
                before_assets.add(tid)
        if after_scan:
            for tid in after_scan.target_ids:
                after_assets.add(tid)

        graph_nodes = await self._graph.list_nodes_for_project(
            project_id, GraphNodeType.ASSET
        )
        node_map = {n.source_id: n for n in graph_nodes}

        new_ids = after_assets - before_assets
        disappeared_ids = before_assets - after_assets
        stable_ids = before_assets & after_assets

        new_nodes = [node_map[nid] for nid in new_ids if nid in node_map]
        disappeared_nodes = [
            node_map[nid] for nid in disappeared_ids if nid in node_map
        ]
        stable_nodes = [node_map[nid] for nid in stable_ids if nid in node_map]

        total = max(len(before_assets | after_assets), 1)
        change_pct = len(new_ids) / total * 100

        return AssetDelta(
            new_assets=new_nodes,
            disappeared_assets=disappeared_nodes,
            stable_assets=stable_nodes,
            new_count=len(new_ids),
            disappeared_count=len(disappeared_ids),
            surface_change_percent=round(change_pct, 1),
        )

    async def compute_finding_trends(
        self,
        project_id: UUID,
    ) -> list[FindingTrend]:
        """Compare finding severity distributions across recent scans."""
        scans = await self._scans.list(project_id, limit=100)
        completed = [
            s for s in scans if s.status == ScanStatus.COMPLETED
        ]

        if len(completed) < 2:
            return []

        current_scan = completed[0]
        previous_scan = completed[1]

        current_findings = [
            f
            for f in await self._findings.list_for_project(project_id, limit=1000)
            if f.created_at and current_scan.created_at
            and f.created_at >= current_scan.created_at
        ]
        previous_findings = [
            f
            for f in await self._findings.list_for_project(project_id, limit=1000)
            if f.created_at and previous_scan.created_at
            and f.created_at < previous_scan.created_at
        ]

        current_counts: dict[str, int] = {}
        for f in current_findings:
            sev = f.severity.value
            current_counts[sev] = current_counts.get(sev, 0) + 1

        previous_counts: dict[str, int] = {}
        for f in previous_findings:
            sev = f.severity.value
            previous_counts[sev] = previous_counts.get(sev, 0) + 1

        all_severities = set(current_counts.keys()) | set(previous_counts.keys())
        trends: list[FindingTrend] = []
        for sev in sorted(all_severities):
            curr = current_counts.get(sev, 0)
            prev = previous_counts.get(sev, 0)
            delta = curr - prev
            if delta > 0:
                direction = "increasing"
            elif delta < 0:
                direction = "decreasing"
            else:
                direction = "stable"
            trends.append(
                FindingTrend(
                    severity=sev,
                    current_count=curr,
                    previous_count=prev,
                    delta=delta,
                    trend_direction=direction,
                )
            )

        return trends

    async def find_recurring_findings(
        self,
        project_id: UUID,
        min_occurrences: int = 2,
    ) -> list[RecurringFinding]:
        """Identify findings that appear across multiple scans."""
        findings = await self._findings.list_for_project(
            project_id, limit=1000
        )

        title_groups: dict[str, list[Finding]] = {}
        for f in findings:
            key = f.title.lower().strip()
            title_groups.setdefault(key, []).append(f)

        recurring: list[RecurringFinding] = []
        for _key, group in title_groups.items():
            if len(group) < min_occurrences:
                continue

            scan_ids = []
            dates: list[datetime] = []
            for f in group:
                scan_ids.extend(f.tool_result_ids)
                if f.created_at:
                    dates.append(f.created_at)

            recurring.append(
                RecurringFinding(
                    title=group[0].title,
                    severity=group[0].severity.value,
                    occurrence_count=len(group),
                    first_seen=min(dates) if dates else None,
                    last_seen=max(dates) if dates else None,
                    scan_ids=scan_ids,
                )
            )

        recurring.sort(key=lambda r: r.occurrence_count, reverse=True)
        return recurring

    async def detect_technology_changes(
        self,
        project_id: UUID,
    ) -> list[TechnologyChange]:
        """Detect changes in technology nodes between scans."""
        tech_nodes = await self._graph.list_nodes_for_project(
            project_id, GraphNodeType.TECHNOLOGY
        )

        current_nodes = [
            n for n in tech_nodes if n.properties.get("in_scope") is not False
        ]
        all_nodes = tech_nodes

        current_labels = {n.label for n in current_nodes}
        all_labels = {n.label for n in all_nodes}

        changes: list[TechnologyChange] = []
        for label in current_labels - all_labels:
            matching = [n for n in current_nodes if n.label == label]
            ts = matching[0].created_at if matching else None
            changes.append(
                TechnologyChange(
                    technology=label, change_type="added", detected_at=ts
                )
            )

        for label in all_labels - current_labels:
            matching = [n for n in all_nodes if n.label == label]
            ts = matching[0].created_at if matching else None
            changes.append(
                TechnologyChange(
                    technology=label, change_type="removed", detected_at=ts
                )
            )

        return changes

    async def is_surface_expanding(
        self,
        project_id: UUID,
    ) -> bool:
        """Determine if the attack surface is growing."""
        delta = await self.compute_asset_delta(project_id)
        return delta.new_count > delta.disappeared_count

    async def generate_report(
        self,
        project_id: UUID,
    ) -> HistoricalReport:
        """Generate a complete historical intelligence report."""
        delta = await self.compute_asset_delta(project_id)
        trends = await self.compute_finding_trends(project_id)
        recurring = await self.find_recurring_findings(project_id)
        tech_changes = await self.detect_technology_changes(project_id)
        expanding = await self.is_surface_expanding(project_id)

        scans = await self._scans.list(project_id, limit=100)
        completed = [s for s in scans if s.status == ScanStatus.COMPLETED]

        return HistoricalReport(
            asset_delta=delta,
            finding_trends=trends,
            recurring_findings=recurring,
            technology_changes=tech_changes,
            scan_count=len(completed),
            surface_expanding=expanding,
        )
