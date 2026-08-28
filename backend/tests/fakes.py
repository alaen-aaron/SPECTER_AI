"""
In-memory fake repositories.

Each class here satisfies its corresponding `domain.repositories`
Protocol *structurally* (no inheritance needed — that's the point of
using `Protocol` in the domain layer). These let every `application/`
service be unit-tested in full isolation, with no Postgres, no Docker,
and no network — a deliberate consequence of Dependency Inversion
(SRS §10.1, §21).

These are test doubles, not a "second implementation to maintain" —
they intentionally skip things like SQL-level cascade behavior, which
is instead covered by the smaller set of real-database integration
tests in `tests/integration/` (skipped automatically if no DB is
reachable).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.action_validator import ProposalValidation
from app.application.planner_service import PlanOutcome, ProposedAction
from app.domain.entities import (
    Asset,
    AssetObservation,
    AuditLogEntry,
    AuthorizationRecord,
    AutonomousRun,
    AutonomousRunAction,
    Evidence,
    Finding,
    GraphEdge,
    GraphNode,
    Organization,
    OrganizationInvitation,
    OrganizationMember,
    PlannedAction,
    Project,
    ProjectMember,
    Report,
    ReportVersion,
    Scan,
    Schedule,
    Session,
    Target,
    ToolResult,
    User,
    Workflow,
    WorkflowExecution,
    WorkflowStep,
)
from app.domain.exceptions import (
    ActionNotExecutableError,
    ActionRejectedByValidatorError,
    PlannedActionNotFoundError,
)
from app.domain.value_objects import (
    AssetType,
    AuthorizationStatus,
    AutonomousRunStatus,
    FindingStatus,
    GraphEdgeType,
    GraphNodeType,
    OrganizationRole,
    PlannedActionStatus,
    ProjectRole,
    ReportStatus,
    ScanStatus,
    Severity,
)


class FakeUserRepository:
    def __init__(self) -> None:
        self._by_id: dict[UUID, User] = {}

    async def get_by_id(self, user_id: UUID) -> User | None:
        return self._by_id.get(user_id)

    async def get_by_email(self, email: str) -> User | None:
        normalized = email.lower()
        for user in self._by_id.values():
            if user.email.lower() == normalized:
                return user
        return None

    async def add(self, user: User) -> None:
        self._by_id[user.id] = user


class FakeSessionRepository:
    def __init__(self) -> None:
        self._by_id: dict[UUID, Session] = {}

    async def add(self, session: Session) -> None:
        self._by_id[session.id] = session

    async def get_by_id(self, session_id: UUID) -> Session | None:
        return self._by_id.get(session_id)

    async def get_by_token_hash(self, token_hash: str) -> Session | None:
        for session in self._by_id.values():
            if session.refresh_token_hash == token_hash:
                return session
        return None

    async def revoke(self, session_id: UUID) -> None:
        session = self._by_id.get(session_id)
        if session is not None:
            session.revoked_at = datetime.now(UTC)

    async def revoke_all_for_user(self, user_id: UUID) -> None:
        for session in self._by_id.values():
            if session.user_id == user_id and session.revoked_at is None:
                session.revoked_at = datetime.now(UTC)


class FakeOrganizationRepository:
    def __init__(self) -> None:
        self._orgs: dict[UUID, Organization] = {}
        self._members: dict[tuple[UUID, UUID], OrganizationMember] = {}
        self._invitations: dict[UUID, OrganizationInvitation] = {}

    async def get_by_id(self, organization_id: UUID) -> Organization | None:
        org = self._orgs.get(organization_id)
        if org is None or org.is_deleted:
            return None
        return org

    async def list_for_user(self, user_id: UUID) -> list[Organization]:
        org_ids = {oid for (oid, uid) in self._members if uid == user_id}
        return [self._orgs[oid] for oid in org_ids if not self._orgs[oid].is_deleted]

    async def add(self, organization: Organization) -> None:
        self._orgs[organization.id] = organization

    async def update(self, organization: Organization) -> None:
        self._orgs[organization.id] = organization

    async def soft_delete(self, organization_id: UUID) -> None:
        org = self._orgs.get(organization_id)
        if org is not None:
            org.deleted_at = datetime.now(UTC)

    async def add_member(self, member: OrganizationMember) -> None:
        self._members[(member.organization_id, member.user_id)] = member

    async def get_member(self, organization_id: UUID, user_id: UUID) -> OrganizationMember | None:
        return self._members.get((organization_id, user_id))

    async def list_members(self, organization_id: UUID) -> list[OrganizationMember]:
        return [m for (oid, _), m in self._members.items() if oid == organization_id]

    async def update_member_role(
        self, organization_id: UUID, user_id: UUID, role: OrganizationRole
    ) -> None:
        member = self._members.get((organization_id, user_id))
        if member is not None:
            member.role = role

    async def add_invitation(self, invitation: OrganizationInvitation) -> None:
        self._invitations[invitation.id] = invitation

    async def list_invitations(self, organization_id: UUID) -> list[OrganizationInvitation]:
        return [i for i in self._invitations.values() if i.organization_id == organization_id]


class FakeProjectRepository:
    def __init__(self) -> None:
        self._projects: dict[UUID, Project] = {}
        self._members: dict[tuple[UUID, UUID], ProjectMember] = {}

    async def get_by_id(self, project_id: UUID) -> Project | None:
        project = self._projects.get(project_id)
        if project is None or project.is_deleted:
            return None
        return project

    async def list_for_organization(self, organization_id: UUID) -> list[Project]:
        return [
            p
            for p in self._projects.values()
            if p.organization_id == organization_id and not p.is_deleted
        ]

    async def add(self, project: Project) -> None:
        self._projects[project.id] = project

    async def update(self, project: Project) -> None:
        self._projects[project.id] = project

    async def soft_delete(self, project_id: UUID) -> None:
        project = self._projects.get(project_id)
        if project is not None:
            project.deleted_at = datetime.now(UTC)

    async def add_member(self, member: ProjectMember) -> None:
        self._members[(member.project_id, member.user_id)] = member

    async def get_member(self, project_id: UUID, user_id: UUID) -> ProjectMember | None:
        return self._members.get((project_id, user_id))

    async def list_members(self, project_id: UUID) -> list[ProjectMember]:
        return [m for (pid, _), m in self._members.items() if pid == project_id]

    async def update_member_role(self, project_id: UUID, user_id: UUID, role: ProjectRole) -> None:
        member = self._members.get((project_id, user_id))
        if member is not None:
            member.role = role


class FakeTargetRepository:
    def __init__(self) -> None:
        self._targets: dict[UUID, Target] = {}

    async def get_by_id(self, target_id: UUID) -> Target | None:
        target = self._targets.get(target_id)
        if target is None or target.is_deleted:
            return None
        return target

    async def list_for_project(self, project_id: UUID) -> list[Target]:
        return [
            t for t in self._targets.values() if t.project_id == project_id and not t.is_deleted
        ]

    async def add(self, target: Target) -> None:
        self._targets[target.id] = target

    async def update(self, target: Target) -> None:
        self._targets[target.id] = target

    async def soft_delete(self, target_id: UUID) -> None:
        target = self._targets.get(target_id)
        if target is not None:
            target.deleted_at = datetime.now(UTC)


class FakeAuthorizationRecordRepository:
    def __init__(self) -> None:
        self._records: dict[UUID, AuthorizationRecord] = {}

    async def get_by_id(self, record_id: UUID) -> AuthorizationRecord | None:
        return self._records.get(record_id)

    async def get_active_for_project(
        self, project_id: UUID, on_date: datetime
    ) -> AuthorizationRecord | None:
        candidates = [
            r
            for r in self._records.values()
            if r.project_id == project_id
            and r.status == AuthorizationStatus.ACTIVE
            and r.authorized_from <= on_date.date() <= r.authorized_to
        ]
        return candidates[0] if candidates else None

    async def list_active_for_project(
        self, project_id: UUID, on_date: datetime
    ) -> list[AuthorizationRecord]:
        return [
            r
            for r in self._records.values()
            if r.project_id == project_id
            and r.status == AuthorizationStatus.ACTIVE
            and r.authorized_from <= on_date.date() <= r.authorized_to
        ]

    async def list_for_project(self, project_id: UUID) -> list[AuthorizationRecord]:
        return [r for r in self._records.values() if r.project_id == project_id]

    async def add(self, record: AuthorizationRecord) -> None:
        self._records[record.id] = record


class FakeAuditLogRepository:
    def __init__(self) -> None:
        self._entries: list[AuditLogEntry] = []

    async def add(self, entry: AuditLogEntry) -> None:
        self._entries.append(entry)

    async def list_for_organization(self, organization_id: UUID) -> list[AuditLogEntry]:
        return [e for e in self._entries if e.organization_id == organization_id]


class FakeScanRepository:
    def __init__(self) -> None:
        self._scans: dict[UUID, Scan] = {}

    async def create(self, scan: Scan) -> None:
        self._scans[scan.id] = scan

    async def get(self, scan_id: UUID) -> Scan | None:
        return self._scans.get(scan_id)

    async def list(
        self, project_id: UUID, limit: int = 20, cursor: datetime | None = None
    ) -> list[Scan]:
        results = sorted(
            (s for s in self._scans.values() if s.project_id == project_id),
            key=lambda s: s.created_at,
            reverse=True,
        )
        if cursor is not None:
            results = [s for s in results if s.created_at < cursor]
        return results[: limit + 1]

    async def update_status(self, scan_id: UUID, status: ScanStatus) -> None:
        scan = self._scans.get(scan_id)
        if scan is not None:
            scan.status = status
            if status is ScanStatus.RUNNING:
                scan.started_at = datetime.now(UTC)

    async def append_log(self, scan_id: UUID, logs_path: str) -> None:
        scan = self._scans.get(scan_id)
        if scan is not None:
            scan.logs_path = logs_path

    async def complete(self, scan_id: UUID, exit_code: int, artifacts_path: str | None) -> None:
        scan = self._scans.get(scan_id)
        if scan is not None:
            scan.status = ScanStatus.COMPLETED
            scan.exit_code = exit_code
            scan.artifacts_path = artifacts_path
            scan.completed_at = datetime.now(UTC)

    async def fail(self, scan_id: UUID, error_message: str, exit_code: int | None) -> None:
        scan = self._scans.get(scan_id)
        if scan is not None:
            scan.status = ScanStatus.FAILED
            scan.error_message = error_message
            scan.exit_code = exit_code
            scan.completed_at = datetime.now(UTC)


class FakeToolResultRepository:
    def __init__(self) -> None:
        self._results: dict[UUID, ToolResult] = {}

    async def add(self, tool_result: ToolResult) -> None:
        self._results[tool_result.id] = tool_result

    async def get(self, tool_result_id: UUID) -> ToolResult | None:
        return self._results.get(tool_result_id)

    async def list_for_scan(self, scan_id: UUID) -> list[ToolResult]:
        return [r for r in self._results.values() if r.scan_id == scan_id]


class FakeAssetRepository:
    def __init__(self) -> None:
        self._assets: dict[UUID, Asset] = {}

    @staticmethod
    def _snapshot(asset: Asset) -> Asset:
        """Store/return detached copies so caller-side mutations of a
        previously returned entity can never rewrite history before an
        update() call — mirroring real DB column semantics."""
        from dataclasses import replace

        return replace(asset)

    async def get_by_id(self, asset_id: UUID) -> Asset | None:
        asset = self._assets.get(asset_id)
        return self._snapshot(asset) if asset else None

    async def list_for_project(
        self,
        project_id: UUID,
        asset_type: AssetType | None = None,
        limit: int = 20,
        cursor: datetime | None = None,
    ) -> list[Asset]:
        results = [a for a in self._assets.values() if a.project_id == project_id]
        if asset_type is not None:
            results = [a for a in results if a.asset_type == asset_type]
        results.sort(key=lambda a: a.created_at or a.first_seen, reverse=True)
        if cursor is not None:
            results = [a for a in results if (a.created_at or a.first_seen) < cursor]
        return results[: limit + 1]

    async def add(self, asset: Asset) -> None:
        self._assets[asset.id] = self._snapshot(asset)

    async def update(self, asset: Asset) -> None:
        existing = self._assets.get(asset.id)
        if existing is None:
            return
        # Refresh mutable fields; NEVER overwrite identity_key with None.
        existing.asset_type = asset.asset_type
        existing.value = asset.value
        existing.last_seen = asset.last_seen
        existing.in_scope = asset.in_scope
        existing.source_scan_id = asset.source_scan_id
        existing.metadata = asset.metadata
        if asset.identity_key is not None:
            existing.identity_key = asset.identity_key

    async def upsert(self, asset: Asset) -> Asset:
        for existing in self._assets.values():
            if (
                existing.project_id == asset.project_id
                and existing.asset_type == asset.asset_type
                and existing.value == asset.value
            ):
                existing.last_seen = asset.last_seen
                existing.source_scan_id = asset.source_scan_id
                existing.metadata = asset.metadata
                existing.in_scope = asset.in_scope
                if asset.identity_key is not None:
                    existing.identity_key = asset.identity_key
                return self._snapshot(existing)
        self._assets[asset.id] = self._snapshot(asset)
        return self._snapshot(asset)

    async def upsert(self, asset: Asset) -> Asset:
        for existing in self._assets.values():
            if (
                existing.project_id == asset.project_id
                and existing.asset_type == asset.asset_type
                and existing.value == asset.value
            ):
                existing.last_seen = asset.last_seen
                existing.source_scan_id = asset.source_scan_id
                existing.metadata = asset.metadata
                existing.in_scope = asset.in_scope
                if asset.identity_key is not None:
                    existing.identity_key = asset.identity_key
                return existing
        self._assets[asset.id] = asset
        return asset

    async def get_by_dedup(
        self, project_id: UUID, asset_type: AssetType, value: str
    ) -> Asset | None:
        for asset in self._assets.values():
            if (
                asset.project_id == project_id
                and asset.asset_type == asset_type
                and asset.value == value
            ):
                return self._snapshot(asset)
        return None

    async def get_by_identity(
        self, project_id: UUID, asset_type: AssetType, identity_key: str
    ) -> Asset | None:
        for asset in self._assets.values():
            if (
                asset.project_id == project_id
                and asset.asset_type == asset_type
                and asset.identity_key == identity_key
            ):
                return self._snapshot(asset)
        return None


class FakeAssetObservationRepository:
    """M7.3 Phase 2: in-memory provenance store mirroring the DB unique
    constraint uq(tool_result_id, asset_id)."""

    def __init__(self) -> None:
        self._observations: dict[UUID, AssetObservation] = {}

    async def add(self, observation: AssetObservation) -> None:
        self._observations[observation.id] = observation

    async def exists_for(self, tool_result_id: UUID, asset_id: UUID) -> bool:
        return any(
            o.tool_result_id == tool_result_id and o.asset_id == asset_id
            for o in self._observations.values()
        )

    async def list_for_asset(
        self, asset_id: UUID, limit: int = 100
    ) -> list[AssetObservation]:
        found = [
            o for o in self._observations.values() if o.asset_id == asset_id
        ]
        found.sort(key=lambda o: o.observed_at or datetime.min.replace(tzinfo=UTC), reverse=True)
        return found[:limit]

    @property
    def count(self) -> int:
        return len(self._observations)


class FakeFindingRepository:
    def __init__(self) -> None:
        self._findings: dict[UUID, Finding] = {}

    async def add(self, finding: Finding) -> None:
        self._findings[finding.id] = finding

    async def get(self, finding_id: UUID) -> Finding | None:
        return self._findings.get(finding_id)

    async def list_for_project(
        self,
        project_id: UUID,
        severity: Severity | None = None,
        limit: int = 20,
        cursor: datetime | None = None,
    ) -> list[Finding]:
        results = [f for f in self._findings.values() if f.project_id == project_id]
        if severity is not None:
            results = [f for f in results if f.severity == severity]
        results.sort(
            key=lambda f: f.created_at or datetime.min.replace(tzinfo=UTC), reverse=True
        )
        if cursor is not None:
            results = [
                f
                for f in results
                if (f.created_at or datetime.min.replace(tzinfo=UTC)) < cursor
            ]
        return results[: limit + 1]

    async def get_by_dedup_key(
        self, project_id: UUID, dedup_key: str
    ) -> Finding | None:
        for finding in self._findings.values():
            if finding.project_id == project_id and finding.dedup_key == dedup_key:
                return finding
        return None

    async def update_status(self, finding_id: UUID, status: FindingStatus) -> None:
        finding = self._findings.get(finding_id)
        if finding is not None:
            finding.status = status


class FakeEvidenceRepository:
    def __init__(self) -> None:
        self._evidence: dict[UUID, Evidence] = {}
        self._findings: FakeFindingRepository | None = None

    def set_findings(self, findings: FakeFindingRepository) -> None:
        self._findings = findings

    async def add(self, evidence: Evidence) -> None:
        self._evidence[evidence.id] = evidence

    async def get(self, evidence_id: UUID) -> Evidence | None:
        return self._evidence.get(evidence_id)

    async def list_for_finding(self, finding_id: UUID) -> list[Evidence]:
        return [
            e for e in self._evidence.values() if e.finding_id == finding_id
        ]

    async def list_for_project(self, project_id: UUID) -> list[Evidence]:
        if self._findings is None:
            return []
        finding_ids = {
            f.id for f in self._findings._findings.values()
            if f.project_id == project_id
        }
        return [
            e for e in self._evidence.values() if e.finding_id in finding_ids
        ]


class FakeReportRepository:
    def __init__(self) -> None:
        self._reports: dict[UUID, Report] = {}

    async def add(self, report: Report) -> None:
        self._reports[report.id] = report

    async def get(self, report_id: UUID) -> Report | None:
        return self._reports.get(report_id)

    async def list_for_project(self, project_id: UUID) -> list[Report]:
        return sorted(
            [r for r in self._reports.values() if r.project_id == project_id],
            key=lambda r: r.created_at or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )

    async def update_status(self, report_id: UUID, status: ReportStatus) -> None:
        report = self._reports.get(report_id)
        if report is not None:
            report.status = status


class FakeReportVersionRepository:
    def __init__(self) -> None:
        self._versions: dict[UUID, ReportVersion] = {}

    async def add(self, version: ReportVersion) -> None:
        self._versions[version.id] = version

    async def get(self, version_id: UUID) -> ReportVersion | None:
        return self._versions.get(version_id)

    async def list_for_report(self, report_id: UUID) -> list[ReportVersion]:
        return sorted(
            [v for v in self._versions.values() if v.report_id == report_id],
            key=lambda v: v.version_number,
        )

    async def get_latest(self, report_id: UUID) -> ReportVersion | None:
        versions = [v for v in self._versions.values() if v.report_id == report_id]
        if not versions:
            return None
        return max(versions, key=lambda v: v.version_number)


class FakeGraphRepository:
    def __init__(self) -> None:
        self._nodes: dict[UUID, GraphNode] = {}
        self._edges: dict[UUID, GraphEdge] = {}

    async def upsert_node(self, node: GraphNode) -> GraphNode:
        existing = await self.find_node(
            node.project_id, node.node_type, node.source_table, node.source_id
        )
        if existing is not None:
            self._nodes[existing.id].label = node.label
            self._nodes[existing.id].properties = node.properties
            return self._nodes[existing.id]
        self._nodes[node.id] = node
        return node

    async def upsert_edge(self, edge: GraphEdge) -> GraphEdge:
        existing = await self.find_edge(
            edge.project_id, edge.from_node_id, edge.to_node_id, edge.relationship_type
        )
        if existing is not None:
            self._edges[existing.id].weight = edge.weight
            self._edges[existing.id].properties = edge.properties
            return self._edges[existing.id]
        self._edges[edge.id] = edge
        return edge

    async def get_node(self, node_id: UUID) -> GraphNode | None:
        return self._nodes.get(node_id)

    async def get_edge(self, edge_id: UUID) -> GraphEdge | None:
        return self._edges.get(edge_id)

    async def find_node(
        self,
        project_id: UUID,
        node_type: GraphNodeType,
        source_table: str,
        source_id: UUID,
    ) -> GraphNode | None:
        for node in self._nodes.values():
            if (
                node.project_id == project_id
                and node.node_type == node_type
                and node.source_table == source_table
                and node.source_id == source_id
            ):
                return node
        return None

    async def find_node_by_source(
        self, project_id: UUID, source_table: str, source_id: UUID
    ) -> GraphNode | None:
        for node in self._nodes.values():
            if (
                node.project_id == project_id
                and node.source_table == source_table
                and node.source_id == source_id
            ):
                return node
        return None

    async def find_edge(
        self,
        project_id: UUID,
        from_node_id: UUID,
        to_node_id: UUID,
        relationship_type: GraphEdgeType,
    ) -> GraphEdge | None:
        for edge in self._edges.values():
            if (
                edge.project_id == project_id
                and edge.from_node_id == from_node_id
                and edge.to_node_id == to_node_id
                and edge.relationship_type == relationship_type
            ):
                return edge
        return None

    async def get_neighbors(
        self,
        node_id: UUID,
        edge_type: GraphEdgeType | None = None,
        direction: str = "outgoing",
    ) -> list[GraphNode]:
        neighbor_ids: list[UUID] = []
        for edge in self._edges.values():
            if (
                direction == "outgoing"
                and edge.from_node_id == node_id
                and (edge_type is None or edge.relationship_type == edge_type)
            ):
                neighbor_ids.append(edge.to_node_id)
            elif (
                direction == "incoming"
                and edge.to_node_id == node_id
                and (edge_type is None or edge.relationship_type == edge_type)
            ):
                neighbor_ids.append(edge.from_node_id)
        return [self._nodes[nid] for nid in neighbor_ids if nid in self._nodes]

    async def shortest_path(
        self, from_node_id: UUID, to_node_id: UUID, max_depth: int = 10
    ) -> list[GraphNode] | None:
        from collections import deque

        if from_node_id == to_node_id:
            node = self._nodes.get(from_node_id)
            return [node] if node else None

        visited: set[UUID] = {from_node_id}
        queue: deque[tuple[UUID, list[UUID]]] = deque([(from_node_id, [from_node_id])])

        while queue:
            current, path = queue.popleft()
            if len(path) > max_depth:
                break
            for edge in self._edges.values():
                if edge.from_node_id == current and edge.to_node_id not in visited:
                    new_path = path + [edge.to_node_id]
                    if edge.to_node_id == to_node_id:
                        return [self._nodes[nid] for nid in new_path if nid in self._nodes]
                    visited.add(edge.to_node_id)
                    queue.append((edge.to_node_id, new_path))
        return None

    async def list_nodes_for_project(
        self,
        project_id: UUID,
        node_type: GraphNodeType | None = None,
    ) -> list[GraphNode]:
        nodes = [n for n in self._nodes.values() if n.project_id == project_id]
        if node_type is not None:
            nodes = [n for n in nodes if n.node_type == node_type]
        return nodes

    async def list_edges_for_project(
        self,
        project_id: UUID,
        relationship_type: GraphEdgeType | None = None,
    ) -> list[GraphEdge]:
        edges = [e for e in self._edges.values() if e.project_id == project_id]
        if relationship_type is not None:
            edges = [e for e in edges if e.relationship_type == relationship_type]
        return edges

    async def remove_node(self, node_id: UUID) -> None:
        self._edges = {
            eid: e
            for eid, e in self._edges.items()
            if e.from_node_id != node_id and e.to_node_id != node_id
        }
        self._nodes.pop(node_id, None)

    async def remove_edge(self, edge_id: UUID) -> None:
        self._edges.pop(edge_id, None)

    async def remove_edges_for_node(self, node_id: UUID) -> None:
        self._edges = {
            eid: e
            for eid, e in self._edges.items()
            if e.from_node_id != node_id and e.to_node_id != node_id
        }

    async def clear_project(self, project_id: UUID) -> None:
        self._edges = {
            eid: e for eid, e in self._edges.items() if e.project_id != project_id
        }
        self._nodes = {
            nid: n for nid, n in self._nodes.items() if n.project_id != project_id
        }

    async def blast_radius(
        self,
        project_id: UUID,
        start_node_id: UUID,
        max_depth: int = 5,
    ) -> list[GraphNode]:
        """BFS traversal returning all reachable nodes from start_node_id."""
        from collections import deque

        visited: set[UUID] = set()
        result: list[GraphNode] = []
        queue: deque[tuple[UUID, int]] = deque([(start_node_id, 0)])

        while queue:
            current, depth = queue.popleft()
            if current in visited or depth > max_depth:
                continue
            visited.add(current)
            node = self._nodes.get(current)
            if node is not None:
                if current != start_node_id:
                    result.append(node)
                for edge in self._edges.values():
                    if edge.from_node_id == current and edge.to_node_id not in visited:
                        queue.append((edge.to_node_id, depth + 1))

        return result


class FakeWorkflowRepository:
    def __init__(self) -> None:
        self._workflows: dict[UUID, Workflow] = {}

    async def create(self, workflow: Workflow) -> None:
        self._workflows[workflow.id] = workflow

    async def get(self, workflow_id: UUID) -> Workflow | None:
        return self._workflows.get(workflow_id)

    async def list_for_project(self, project_id: UUID) -> list[Workflow]:
        return sorted(
            [w for w in self._workflows.values() if w.project_id == project_id],
            key=lambda w: w.created_at or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )

    async def update(self, workflow: Workflow) -> None:
        self._workflows[workflow.id] = workflow

    async def delete(self, workflow_id: UUID) -> None:
        self._workflows.pop(workflow_id, None)


class FakeWorkflowStepRepository:
    def __init__(self) -> None:
        self._steps: dict[UUID, WorkflowStep] = {}

    async def add(self, step: WorkflowStep) -> None:
        self._steps[step.id] = step

    async def get(self, step_id: UUID) -> WorkflowStep | None:
        return self._steps.get(step_id)

    async def list_for_workflow(self, workflow_id: UUID) -> list[WorkflowStep]:
        return sorted(
            [s for s in self._steps.values() if s.workflow_id == workflow_id],
            key=lambda s: s.order,
        )

    async def update(self, step: WorkflowStep) -> None:
        self._steps[step.id] = step

    async def delete(self, step_id: UUID) -> None:
        self._steps.pop(step_id, None)

    async def delete_for_workflow(self, workflow_id: UUID) -> None:
        self._steps = {
            sid: s for sid, s in self._steps.items() if s.workflow_id != workflow_id
        }


class FakeWorkflowExecutionRepository:
    def __init__(self) -> None:
        self._executions: dict[UUID, WorkflowExecution] = {}

    async def create(self, execution: WorkflowExecution) -> None:
        self._executions[execution.id] = execution

    async def get(self, execution_id: UUID) -> WorkflowExecution | None:
        return self._executions.get(execution_id)

    async def list_for_workflow(self, workflow_id: UUID) -> list[WorkflowExecution]:
        return sorted(
            [e for e in self._executions.values() if e.workflow_id == workflow_id],
            key=lambda e: e.created_at or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )

    async def list_for_project(self, project_id: UUID) -> list[WorkflowExecution]:
        return sorted(
            [e for e in self._executions.values() if e.project_id == project_id],
            key=lambda e: e.created_at or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )

    async def update_status(self, execution_id: UUID, status: ScanStatus) -> None:
        execution = self._executions.get(execution_id)
        if execution is not None:
            execution.status = status
            if status is ScanStatus.RUNNING:
                execution.started_at = datetime.now(UTC)
            elif status in (ScanStatus.COMPLETED, ScanStatus.FAILED, ScanStatus.CANCELLED):
                execution.completed_at = datetime.now(UTC)

    async def set_step_result(
        self,
        execution_id: UUID,
        step_id: str,
        result: dict[str, object],
    ) -> None:
        execution = self._executions.get(execution_id)
        if execution is not None:
            execution.step_results[step_id] = result


class FakeScheduleRepository:
    def __init__(self) -> None:
        self._schedules: dict[UUID, Schedule] = {}

    async def create(self, schedule: Schedule) -> None:
        self._schedules[schedule.id] = schedule

    async def get(self, schedule_id: UUID) -> Schedule | None:
        return self._schedules.get(schedule_id)

    async def list_for_project(self, project_id: UUID) -> list[Schedule]:
        return sorted(
            [s for s in self._schedules.values() if s.project_id == project_id],
            key=lambda s: s.created_at or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )

    async def list_active(self) -> list[Schedule]:
        return sorted(
            [s for s in self._schedules.values() if s.is_active],
            key=lambda s: s.next_run_at or datetime.max.replace(tzinfo=UTC),
        )

    async def list_due(self, now: datetime) -> list[Schedule]:
        return sorted(
            [
                s for s in self._schedules.values()
                if s.is_active and s.next_run_at is not None and s.next_run_at <= now
            ],
            key=lambda s: s.next_run_at or datetime.max.replace(tzinfo=UTC),
        )

    async def update(self, schedule: Schedule) -> None:
        self._schedules[schedule.id] = schedule

    async def delete(self, schedule_id: UUID) -> None:
        self._schedules.pop(schedule_id, None)


# --- AI Decision Engine fakes (Phase 4) -------------------------------------


class FakePlannedActionRepository:
    def __init__(self) -> None:
        self._actions: dict[UUID, object] = {}

    async def create(self, action: object) -> None:
        self._actions[action.id] = action

    async def get(self, action_id: UUID) -> object | None:
        return self._actions.get(action_id)

    async def list_for_project(
        self,
        project_id: UUID,
        status: object | None = None,
        limit: int = 20,
        cursor: datetime | None = None,
    ) -> list[object]:
        results = [
            a for a in self._actions.values()
            if a.project_id == project_id
            and (status is None or a.status == status)
        ]
        return sorted(
            results,
            key=lambda a: a.created_at or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )[:limit]

    async def update(self, action: object) -> None:
        self._actions[action.id] = action


class FakeRiskScoreRepository:
    def __init__(self) -> None:
        self._scores: dict[UUID, object] = {}

    async def create(self, score: object) -> None:
        self._scores[score.id] = score

    async def get(self, score_id: UUID) -> object | None:
        return self._scores.get(score_id)

    async def get_by_finding(self, finding_id: UUID) -> object | None:
        for score in self._scores.values():
            if score.finding_id == finding_id:
                return score
        return None

    async def list_for_project(self, project_id: UUID) -> list[object]:
        return list(self._scores.values())

    async def update(self, score: object) -> None:
        self._scores[score.id] = score


class FakePromptTemplateRepository:
    def __init__(self) -> None:
        self._templates: dict[UUID, object] = {}

    async def create(self, template: object) -> None:
        self._templates[template.id] = template

    async def get(self, template_id: UUID) -> object | None:
        return self._templates.get(template_id)

    async def get_active_by_name(self, name: str) -> object | None:
        candidates = [
            t for t in self._templates.values()
            if t.name == name and t.is_active
        ]
        if candidates:
            return max(candidates, key=lambda t: t.version)
        return None

    async def list_all(self) -> list[object]:
        return sorted(
            self._templates.values(),
            key=lambda t: (t.name, -t.version),
        )

    async def update(self, template: object) -> None:
        self._templates[template.id] = template

    async def delete(self, template_id: UUID) -> None:
        self._templates.pop(template_id, None)


class FakeAIContextMemoryRepository:
    def __init__(self) -> None:
        self._memories: dict[UUID, object] = {}

    async def add(self, memory: object) -> None:
        self._memories[memory.id] = memory

    async def list_for_project(self, project_id: UUID) -> list[object]:
        return sorted(
            [m for m in self._memories.values() if m.project_id == project_id],
            key=lambda m: m.created_at or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )

    async def list_for_project_by_type(
        self, project_id: UUID, memory_type: str
    ) -> list[object]:
        return sorted(
            [
                m for m in self._memories.values()
                if m.project_id == project_id and m.memory_type == memory_type
            ],
            key=lambda m: m.created_at or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )

    async def delete(self, memory_id: UUID) -> None:
        self._memories.pop(memory_id, None)


# --- Autonomous Orchestration fakes (M7.4) ----------------------------------


class FakeAutonomousRunRepository:
    def __init__(self) -> None:
        self._runs: dict[UUID, AutonomousRun] = {}

    async def create(self, run: AutonomousRun) -> None:
        self._runs[run.id] = run

    async def get(self, run_id: UUID) -> AutonomousRun | None:
        return self._runs.get(run_id)

    async def list_for_project(
        self,
        project_id: UUID,
        status: AutonomousRunStatus | None = None,
        limit: int = 20,
        cursor: datetime | None = None,
    ) -> list[AutonomousRun]:
        results = [
            r for r in self._runs.values()
            if r.project_id == project_id
            and (status is None or r.status == status)
        ]
        results.sort(
            key=lambda r: r.created_at or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )
        if cursor is not None:
            results = [
                r for r in results
                if (r.created_at or datetime.min.replace(tzinfo=UTC)) < cursor
            ]
        return results[:limit]

    async def get_active_for_project(self, project_id: UUID) -> AutonomousRun | None:
        terminal = {
            AutonomousRunStatus.COMPLETED,
            AutonomousRunStatus.CANCELLED,
            AutonomousRunStatus.FAILED,
        }
        for run in self._runs.values():
            if run.project_id == project_id and run.status not in terminal:
                return run
        return None

    async def update(self, run: AutonomousRun) -> None:
        self._runs[run.id] = run

    async def count_actions(self, run_id: UUID) -> int:
        return sum(1 for a in self._action_runs if a.run_id == run_id)

    # Set by FakeAutonomousRunActionRepository for count_actions
    _action_runs: list[AutonomousRunAction] = []


class FakeAutonomousRunActionRepository:
    def __init__(self) -> None:
        self._actions: dict[UUID, AutonomousRunAction] = {}
        self._run_repo: FakeAutonomousRunRepository | None = None

    def set_run_repo(self, run_repo: FakeAutonomousRunRepository) -> None:
        self._run_repo = run_repo

    async def create(self, action: AutonomousRunAction) -> None:
        self._actions[action.id] = action
        if self._run_repo is not None:
            self._run_repo._action_runs.append(action)

    async def get(self, action_id: UUID) -> AutonomousRunAction | None:
        return self._actions.get(action_id)

    async def list_for_run(
        self,
        run_id: UUID,
        status: str | None = None,
    ) -> list[AutonomousRunAction]:
        results = [
            a for a in self._actions.values()
            if a.run_id == run_id and (status is None or a.status == status)
        ]
        results.sort(key=lambda a: (a.cycle, a.created_at or datetime.min.replace(tzinfo=UTC)))
        return results

    async def update(self, action: AutonomousRunAction) -> None:
        self._actions[action.id] = action

    async def get_last_action_fingerprint(self, run_id: UUID) -> str | None:
        candidates = [a for a in self._actions.values() if a.run_id == run_id]
        if not candidates:
            return None
        last = max(
            candidates,
            key=lambda a: (a.cycle, a.created_at or datetime.min.replace(tzinfo=UTC)),
        )
        return f"{last.action_type}:{last.plugin}:{last.target_ids}:{last.status}"


class FakeScanLauncher:
    """Stands in for `ScanService.create` — records calls, returns a QUEUED Scan."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.scans: dict[UUID, Scan] = {}
        self.fail: Exception | None = None

    async def __call__(
        self,
        project_id: UUID,
        plugin_name: str,
        plugin_config: dict[str, object],
        target_ids: list[UUID],
        initiated_by: UUID,
    ) -> Scan:
        if self.fail is not None:
            raise self.fail
        scan = Scan(
            id=uuid4(),
            project_id=project_id,
            initiated_by=initiated_by,
            plugin=plugin_name,
            status=ScanStatus.QUEUED,
            target_ids=list(target_ids),
            plugin_config=dict(plugin_config),
            created_at=datetime.now(UTC),
        )
        self.calls.append(
            {
                "project_id": project_id,
                "plugin_name": plugin_name,
                "plugin_config": dict(plugin_config),
                "target_ids": list(target_ids),
                "initiated_by": initiated_by,
            }
        )
        self.scans[scan.id] = scan
        return scan


class FakePlannerService:
    """Deterministic test double for `PlannerService.plan/approve/reject/execute_approved`.

    `proposal_specs` drives `plan()`: each spec is a dict with the shape
    of a grounded proposal plus `accepted` / `risk_level`. `outcome` lets
    a test bypass proposal-building entirely (e.g. planner exhausted).
    """

    def __init__(self) -> None:
        self.proposal_specs: list[dict[str, object]] = []
        self.outcome: PlanOutcome | None = None
        self.stopped_because: str = "no_more_candidates"
        self.raise_validation_rejection = False
        self.plan_calls: list[dict[str, object]] = []
        self.planned: list[PlannedAction] = []
        self.approved: list[UUID] = []
        self.rejected: list[UUID] = []
        self.executed: list[tuple[UUID, Scan]] = []
        self._store: dict[UUID, PlannedAction] = {}

    async def plan(
        self,
        project_id: UUID,
        created_by: UUID,
        objective: str = "",
        max_actions: int = 3,
        session_timeout_seconds: float = 15.0,
        cancelled_check=None,
    ) -> PlanOutcome:
        self.plan_calls.append(
            {
                "project_id": project_id,
                "created_by": created_by,
                "objective": objective,
                "max_actions": max_actions,
                "session_timeout_seconds": session_timeout_seconds,
            }
        )
        if cancelled_check is not None and cancelled_check():
            return PlanOutcome(
                proposals=(),
                skipped_duplicates=0,
                ungrounded=0,
                stopped_because="cancelled",
                context_summary={},
                runner_mode="subprocess",
            )
        if self.outcome is not None:
            return self.outcome

        proposals: list[ProposedAction] = []
        for spec in self.proposal_specs[:max_actions]:
            action = PlannedAction(
                id=uuid4(),
                project_id=project_id,
                action_type=str(spec.get("action_type", "recon")),
                title=str(spec.get("title", "")),
                description=str(spec.get("description", "")),
                justification=str(spec.get("justification", "")),
                plugin=str(spec.get("plugin", "ping")),
                target_ids=[UUID(str(t)) for t in spec.get("target_ids", [])],
                plugin_config=dict(spec.get("plugin_config") or {}),
                status=PlannedActionStatus.PENDING_REVIEW,
                created_by=created_by,
                objective=objective or None,
                risk_level=str(spec.get("risk_level", "low")),
            )
            self._store[action.id] = action
            self.planned.append(action)
            accepted = bool(spec.get("accepted", True))
            proposals.append(
                ProposedAction(
                    action=action,
                    validation=ProposalValidation(
                        accepted=accepted, checks=(), runner_mode="subprocess"
                    ),
                    persisted=accepted,
                )
            )
        return PlanOutcome(
            proposals=tuple(proposals),
            skipped_duplicates=0,
            ungrounded=0,
            stopped_because=self.stopped_because,
            context_summary={},
            runner_mode="subprocess",
        )

    async def approve(self, action_id: UUID, approved_by: UUID) -> PlannedAction:
        action = self._get(action_id)
        if not action.is_approvable:
            from app.domain.exceptions import PlannedActionNotApprovableError

            raise PlannedActionNotApprovableError(action_id, action.status.value)
        action.status = PlannedActionStatus.APPROVED
        action.approved_by = approved_by
        action.approved_at = datetime.now(UTC)
        self.approved.append(action_id)
        return action

    async def reject(self, action_id: UUID, rejected_by: UUID, reason: str = "") -> PlannedAction:
        action = self._get(action_id)
        action.status = PlannedActionStatus.REJECTED
        action.rejection_reason = reason
        self.rejected.append(action_id)
        return action

    async def get(self, action_id: UUID) -> PlannedAction:
        return self._get(action_id)

    async def list_for_project(
        self,
        project_id: UUID,
        status: PlannedActionStatus | None = None,
        limit: int = 20,
    ) -> list[PlannedAction]:
        return [
            a
            for a in self.planned
            if a.project_id == project_id
            and (status is None or a.status == status)
        ][:limit]

    async def execute_approved(
        self,
        action_id: UUID,
        initiated_by: UUID,
        launch_scan,
        expected_project_id: UUID | None = None,
    ) -> tuple[PlannedAction, Scan]:
        action = self._get(action_id)
        if expected_project_id is not None and action.project_id != expected_project_id:
            raise PlannedActionNotFoundError(action_id)
        if action.status is not PlannedActionStatus.APPROVED:
            raise ActionNotExecutableError(action_id, action.status.value)
        if self.raise_validation_rejection:
            raise ActionRejectedByValidatorError(
                action_id, ["fake execution-time re-validation rejection"]
            )
        scan = await launch_scan(
            project_id=action.project_id,
            plugin_name=str(action.plugin),
            plugin_config=dict(action.plugin_config),
            target_ids=list(action.target_ids),
            initiated_by=initiated_by,
        )
        action.status = PlannedActionStatus.EXECUTED
        action.scan_id = scan.id
        self.executed.append((action_id, scan))
        return action, scan

    def _get(self, action_id: UUID) -> PlannedAction:
        action = self._store.get(action_id)
        if action is None:
            raise PlannedActionNotFoundError(action_id)
        return action
