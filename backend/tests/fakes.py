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
from uuid import UUID

from app.domain.entities import (
    Asset,
    AuditLogEntry,
    AuthorizationRecord,
    Evidence,
    Finding,
    GraphEdge,
    GraphNode,
    Organization,
    OrganizationInvitation,
    OrganizationMember,
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
from app.domain.value_objects import (
    AssetType,
    FindingStatus,
    GraphEdgeType,
    GraphNodeType,
    OrganizationRole,
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
            if r.project_id == project_id and r.authorized_from <= on_date.date() <= r.authorized_to
        ]
        return candidates[0] if candidates else None

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

    async def get_by_id(self, asset_id: UUID) -> Asset | None:
        return self._assets.get(asset_id)

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
        self._assets[asset.id] = asset

    async def update(self, asset: Asset) -> None:
        self._assets[asset.id] = asset

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
                return asset
        return None


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

    async def update(self, schedule: Schedule) -> None:
        self._schedules[schedule.id] = schedule

    async def delete(self, schedule_id: UUID) -> None:
        self._schedules.pop(schedule_id, None)
