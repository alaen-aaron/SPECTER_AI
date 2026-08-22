"""
M5.5 Phase 2 — Execution Pipeline Wiring Tests.

Tests that:
  - ExecutionEngine calls AssetService.upsert_from_tool_result after ToolResult persistence
  - ExecutionEngine links findings to their matching assets (asset_id set)
  - ExecutionEngine projects findings to the knowledge graph
  - WorkflowExecutor also calls AssetService for each step
  - Graceful degradation when asset_service or graph_service is None
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.application.asset_service import AssetService
from app.application.correlation_service import CorrelationService
from app.application.graph_service import GraphService
from app.domain.entities import (
    AuditLogEntry,
    Finding,
    GraphEdge,
    GraphNode,
    Scan,
    ToolResult,
)
from app.domain.value_objects import (
    GraphEdgeType,
    GraphNodeType,
    ScanStatus,
)
from app.infrastructure.execution.engine import ExecutionEngine
from app.plugins.normalizer import ToolOutputNormalizer

# ---------------------------------------------------------------------------
# Test fakes (lightweight, inline)
# ---------------------------------------------------------------------------


NMAP_PAYLOAD: dict[str, object] = {
    "target": "192.168.1.1",
    "host_up": True,
    "ports": [
        {"port": 22, "state": "open", "service": "ssh", "version": "OpenSSH 8.9"},
        {"port": 80, "state": "open", "service": "http", "version": "nginx"},
    ],
}


class _FakeNormalizer(ToolOutputNormalizer):
    @property
    def plugin_name(self) -> str:
        return "nmap"

    def normalize(
        self, stdout: str, stderr: str, config: dict[str, object]
    ) -> dict[str, object]:
        return NMAP_PAYLOAD


class _FakeNormalizerRegistry:
    def get(self, plugin_name: str) -> ToolOutputNormalizer | None:
        if plugin_name == "nmap":
            return _FakeNormalizer()
        return None

    def list(self) -> list[ToolOutputNormalizer]:
        return [_FakeNormalizer()]


class _FakeScanRepo:
    def __init__(self, scan: Scan) -> None:
        self._scans: dict[UUID, Scan] = {scan.id: scan}
        self.updates: list[tuple[UUID, ScanStatus]] = []
        self.completions: list[tuple[UUID, int, str | None]] = []
        self.failures: list[tuple[UUID, str, int | None]] = []
        self.logs: list[tuple[UUID, str]] = []

    async def create(self, scan: Scan) -> None:
        self._scans[scan.id] = scan

    async def get(self, scan_id: UUID) -> Scan | None:
        return self._scans.get(scan_id)

    async def list(self, project_id: UUID, limit: int = 20, cursor=None) -> list[Scan]:
        return []

    async def update_status(self, scan_id: UUID, status: ScanStatus) -> None:
        scan = self._scans.get(scan_id)
        if scan is not None:
            scan.status = status
            self.updates.append((scan_id, status))

    async def append_log(self, scan_id: UUID, logs_path: str) -> None:
        self.logs.append((scan_id, logs_path))

    async def complete(self, scan_id: UUID, exit_code: int, artifacts_path: str | None) -> None:
        scan = self._scans.get(scan_id)
        if scan is not None:
            scan.status = ScanStatus.COMPLETED
            scan.exit_code = exit_code
        self.completions.append((scan_id, exit_code, artifacts_path))

    async def fail(self, scan_id: UUID, error_message: str, exit_code: int | None) -> None:
        scan = self._scans.get(scan_id)
        if scan is not None:
            scan.status = ScanStatus.FAILED
        self.failures.append((scan_id, error_message, exit_code))


class _FakeScopeGuard:
    async def validate_targets(self, project_id: UUID, target_ids: list) -> None:
        pass


class _FakePluginManager:
    def __init__(self, stdout: str = "OK", stderr: str = "", success: bool = True) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self._success = success

    def run(self, plugin: str, config: dict, timeout: int, runner=None):
        return _PluginResult(self._stdout, self._stderr, self._success, 0 if self._success else 1)


@dataclass
class _PluginResult:
    stdout: str
    stderr: str
    success: bool
    exit_code: int | None


class _FakeArtifactStore:
    def __init__(self) -> None:
        self.written: list[tuple[UUID, str, str]] = []

    def write_logs(self, scan_id: UUID, stdout: str, stderr: str) -> str:
        self.written.append((scan_id, stdout, stderr))
        return f"/tmp/logs/{scan_id}"

    def artifacts_directory_if_any(self, scan_id: UUID) -> str | None:
        return None


class _FakeAuditRepo:
    async def add(self, entry: AuditLogEntry) -> None:
        pass

    async def list_for_organization(self, organization_id: UUID) -> list:
        return []


class _FakeToolResultRepo:
    def __init__(self) -> None:
        self.results: list[ToolResult] = []

    async def add(self, tool_result: ToolResult) -> None:
        self.results.append(tool_result)

    async def get(self, tool_result_id: UUID) -> ToolResult | None:
        return None

    async def list_for_scan(self, scan_id: UUID) -> list[ToolResult]:
        return []


class _FakeFindingRepo:
    def __init__(self) -> None:
        self.findings: dict[UUID, Finding] = {}
        self._dedup: dict[str, Finding] = {}

    async def add(self, finding: Finding) -> None:
        self.findings[finding.id] = finding
        if finding.dedup_key:
            self._dedup[finding.dedup_key] = finding

    async def get(self, finding_id: UUID) -> Finding | None:
        return self.findings.get(finding_id)

    async def list_for_project(self, project_id: UUID, severity=None, limit=20, cursor=None):
        return []

    async def get_by_dedup_key(self, project_id: UUID, dedup_key: str) -> Finding | None:
        return self._dedup.get(dedup_key)

    async def update_status(self, finding_id: UUID, status) -> None:
        pass


class _FakeAssetRepo:
    def __init__(self) -> None:
        self.assets: dict[UUID, object] = {}

    async def get_by_id(self, asset_id: UUID):
        return self.assets.get(asset_id)

    async def list_for_project(self, project_id, asset_type=None, limit=20, cursor=None):
        return []

    async def add(self, asset):
        self.assets[asset.id] = asset

    async def update(self, asset):
        self.assets[asset.id] = asset

    async def upsert(self, asset):
        self.assets[asset.id] = asset
        return asset

    async def get_by_dedup(self, project_id, asset_type, value):
        for a in self.assets.values():
            if a.project_id == project_id and a.asset_type == asset_type and a.value == value:
                return a
        return None


class _FakeGraphRepo:
    def __init__(self) -> None:
        self.nodes: dict[UUID, GraphNode] = {}
        self.edges: dict[UUID, GraphEdge] = []

    async def upsert_node(self, node: GraphNode) -> GraphNode:
        existing = await self.find_node(
            node.project_id, node.node_type, node.source_table, node.source_id
        )
        if existing is not None:
            self.nodes[existing.id].label = node.label
            self.nodes[existing.id].properties = node.properties
            return self.nodes[existing.id]
        self.nodes[node.id] = node
        return node

    async def upsert_edge(self, edge: GraphEdge) -> GraphEdge:
        self.edges.append(edge)
        return edge

    async def get_node(self, node_id: UUID):
        return self.nodes.get(node_id)

    async def get_edge(self, edge_id: UUID):
        return None

    async def find_node(self, project_id, node_type, source_table, source_id):
        for node in self.nodes.values():
            if (node.project_id == project_id and node.node_type == node_type
                    and node.source_table == source_table and node.source_id == source_id):
                return node
        return None

    async def find_edge(self, project_id, from_id, to_id, rel_type):
        return None

    async def get_neighbors(self, node_id, edge_type=None, direction="outgoing"):
        return []

    async def shortest_path(self, from_id, to_id, max_depth=10):
        return None

    async def list_nodes_for_project(self, project_id, node_type=None):
        return [n for n in self.nodes.values() if n.project_id == project_id]

    async def list_edges_for_project(self, project_id, rel_type=None):
        return self.edges

    async def remove_node(self, node_id: UUID) -> None:
        pass

    async def remove_edge(self, edge_id: UUID) -> None:
        pass

    async def clear_project(self, project_id: UUID) -> None:
        pass

    async def blast_radius(self, project_id, start_id, max_depth=5):
        return []


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_scan(project_id: UUID, plugin: str = "nmap", target: str = "192.168.1.1") -> Scan:
    now = datetime.now(UTC)
    return Scan(
        id=uuid4(),
        project_id=project_id,
        initiated_by=uuid4(),
        plugin=plugin,
        status=ScanStatus.QUEUED,
        target_ids=[],
        plugin_config={"target": target, "hostname": target},
        created_at=now,
    )


def _make_nmap_tool_result(scan_id: UUID, target: str = "192.168.1.1") -> ToolResult:
    return ToolResult(
        id=uuid4(),
        scan_id=scan_id,
        plugin="nmap",
        target=target,
        normalized_payload={
            "target": target,
            "host_up": True,
            "ports": [
                {"port": 22, "state": "open", "service": "ssh", "version": "OpenSSH 8.9"},
                {"port": 80, "state": "open", "service": "http", "version": "nginx"},
            ],
        },
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_engine_calls_asset_service_after_tool_result_persisted():
    """AssetService.upsert_from_tool_result is called for each ToolResult."""
    project_id = uuid4()
    scan = _make_scan(project_id)
    scan_repo = _FakeScanRepo(scan)

    asset_repo = _FakeAssetRepo()
    graph_repo = _FakeGraphRepo()
    graph_service = GraphService(graph_repo)
    asset_service = AssetService(asset_repo, graph_service)
    finding_repo = _FakeFindingRepo()
    correlation = CorrelationService(finding_repo)

    engine = ExecutionEngine(
        scan_repository=scan_repo,
        scope_guard=_FakeScopeGuard(),
        plugin_manager=_FakePluginManager(),
        artifact_store=_FakeArtifactStore(),
        audit_log_repository=_FakeAuditRepo(),
        tool_result_repository=_FakeToolResultRepo(),
        normalizer_registry=_FakeNormalizerRegistry(),
        default_timeout_seconds=30,
        correlation_service=correlation,
        asset_service=asset_service,
        graph_service=graph_service,
    )

    await engine.run(scan.id)

    assert len(asset_repo.assets) > 0, "Assets should be created from nmap output"
    asset_values = [a.value for a in asset_repo.assets.values()]
    assert "192.168.1.1" in asset_values, "Host asset should be created"


@pytest.mark.asyncio
async def test_engine_links_findings_to_assets():
    """Findings created by correlation should be linked to matching assets via asset_id."""
    project_id = uuid4()
    scan = _make_scan(project_id)
    scan_repo = _FakeScanRepo(scan)

    asset_repo = _FakeAssetRepo()
    graph_repo = _FakeGraphRepo()
    graph_service = GraphService(graph_repo)
    asset_service = AssetService(asset_repo, graph_service)
    finding_repo = _FakeFindingRepo()
    correlation = CorrelationService(finding_repo)

    engine = ExecutionEngine(
        scan_repository=scan_repo,
        scope_guard=_FakeScopeGuard(),
        plugin_manager=_FakePluginManager(),
        artifact_store=_FakeArtifactStore(),
        audit_log_repository=_FakeAuditRepo(),
        tool_result_repository=_FakeToolResultRepo(),
        normalizer_registry=_FakeNormalizerRegistry(),
        default_timeout_seconds=30,
        correlation_service=correlation,
        asset_service=asset_service,
        graph_service=graph_service,
    )

    await engine.run(scan.id)

    linked = [f for f in finding_repo.findings.values() if f.asset_id is not None]
    assert len(linked) > 0, "At least one finding should be linked to an asset"
    for finding in linked:
        assert finding.asset_id in asset_repo.assets


@pytest.mark.asyncio
async def test_engine_projects_findings_to_graph():
    """Findings and assets should be projected to the knowledge graph."""
    project_id = uuid4()
    scan = _make_scan(project_id)
    scan_repo = _FakeScanRepo(scan)

    asset_repo = _FakeAssetRepo()
    graph_repo = _FakeGraphRepo()
    graph_service = GraphService(graph_repo)
    asset_service = AssetService(asset_repo, graph_service)
    finding_repo = _FakeFindingRepo()
    correlation = CorrelationService(finding_repo)

    engine = ExecutionEngine(
        scan_repository=scan_repo,
        scope_guard=_FakeScopeGuard(),
        plugin_manager=_FakePluginManager(),
        artifact_store=_FakeArtifactStore(),
        audit_log_repository=_FakeAuditRepo(),
        tool_result_repository=_FakeToolResultRepo(),
        normalizer_registry=_FakeNormalizerRegistry(),
        default_timeout_seconds=30,
        correlation_service=correlation,
        asset_service=asset_service,
        graph_service=graph_service,
    )

    await engine.run(scan.id)

    asset_nodes = [
        n for n in graph_repo.nodes.values() if n.node_type == GraphNodeType.ASSET
    ]
    finding_nodes = [
        n for n in graph_repo.nodes.values() if n.node_type == GraphNodeType.FINDING
    ]
    assert len(asset_nodes) > 0, "Asset nodes should be in the graph"
    assert len(finding_nodes) > 0, "Finding nodes should be in the graph"


@pytest.mark.asyncio
async def test_engine_creates_graph_edge_finding_to_asset():
    """An EVIDENCED_BY edge should connect finding nodes to asset nodes."""
    project_id = uuid4()
    scan = _make_scan(project_id)
    scan_repo = _FakeScanRepo(scan)

    asset_repo = _FakeAssetRepo()
    graph_repo = _FakeGraphRepo()
    graph_service = GraphService(graph_repo)
    asset_service = AssetService(asset_repo, graph_service)
    finding_repo = _FakeFindingRepo()
    correlation = CorrelationService(finding_repo)

    engine = ExecutionEngine(
        scan_repository=scan_repo,
        scope_guard=_FakeScopeGuard(),
        plugin_manager=_FakePluginManager(),
        artifact_store=_FakeArtifactStore(),
        audit_log_repository=_FakeAuditRepo(),
        tool_result_repository=_FakeToolResultRepo(),
        normalizer_registry=_FakeNormalizerRegistry(),
        default_timeout_seconds=30,
        correlation_service=correlation,
        asset_service=asset_service,
        graph_service=graph_service,
    )

    await engine.run(scan.id)

    edges = graph_repo.edges
    evidenced_by = [e for e in edges if e.relationship_type == GraphEdgeType.EVIDENCED_BY]
    assert len(evidenced_by) > 0, "At least one EVIDENCED_BY edge should exist"


@pytest.mark.asyncio
async def test_engine_works_without_asset_service():
    """Engine runs correctly when asset_service is None (backward compat)."""
    project_id = uuid4()
    scan = _make_scan(project_id)
    scan_repo = _FakeScanRepo(scan)

    finding_repo = _FakeFindingRepo()
    correlation = CorrelationService(finding_repo)

    engine = ExecutionEngine(
        scan_repository=scan_repo,
        scope_guard=_FakeScopeGuard(),
        plugin_manager=_FakePluginManager(),
        artifact_store=_FakeArtifactStore(),
        audit_log_repository=_FakeAuditRepo(),
        tool_result_repository=_FakeToolResultRepo(),
        normalizer_registry=_FakeNormalizerRegistry(),
        default_timeout_seconds=30,
        correlation_service=correlation,
        asset_service=None,
        graph_service=None,
    )

    await engine.run(scan.id)
    assert scan_repo.completions, "Scan should complete normally without asset_service"


@pytest.mark.asyncio
async def test_engine_works_without_graph_service():
    """Engine runs correctly when graph_service is None."""
    project_id = uuid4()
    scan = _make_scan(project_id)
    scan_repo = _FakeScanRepo(scan)

    asset_repo = _FakeAssetRepo()
    asset_service = AssetService(asset_repo)
    finding_repo = _FakeFindingRepo()
    correlation = CorrelationService(finding_repo)

    engine = ExecutionEngine(
        scan_repository=scan_repo,
        scope_guard=_FakeScopeGuard(),
        plugin_manager=_FakePluginManager(),
        artifact_store=_FakeArtifactStore(),
        audit_log_repository=_FakeAuditRepo(),
        tool_result_repository=_FakeToolResultRepo(),
        normalizer_registry=_FakeNormalizerRegistry(),
        default_timeout_seconds=30,
        correlation_service=correlation,
        asset_service=asset_service,
        graph_service=None,
    )

    await engine.run(scan.id)
    assert scan_repo.completions, "Scan should complete without graph_service"
    assert len(asset_repo.assets) > 0, "Assets should still be created without graph_service"


@pytest.mark.asyncio
async def test_engine_asset_failure_is_non_fatal():
    """If asset_service raises, the scan still completes."""
    project_id = uuid4()
    scan = _make_scan(project_id)
    scan_repo = _FakeScanRepo(scan)

    class _BrokenAssetService:
        async def upsert_from_tool_result(self, project_id, tool_result):
            raise RuntimeError("boom")

    engine = ExecutionEngine(
        scan_repository=scan_repo,
        scope_guard=_FakeScopeGuard(),
        plugin_manager=_FakePluginManager(),
        artifact_store=_FakeArtifactStore(),
        audit_log_repository=_FakeAuditRepo(),
        tool_result_repository=_FakeToolResultRepo(),
        normalizer_registry=_FakeNormalizerRegistry(),
        default_timeout_seconds=30,
        correlation_service=None,
        asset_service=_BrokenAssetService(),  # type: ignore[arg-type]
        graph_service=None,
    )

    await engine.run(scan.id)
    assert scan_repo.completions, "Scan should complete even when asset_service fails"


@pytest.mark.asyncio
async def test_engine_graph_failure_is_non_fatal():
    """If graph projection fails, the scan still completes."""
    project_id = uuid4()
    scan = _make_scan(project_id)
    scan_repo = _FakeScanRepo(scan)

    asset_repo = _FakeAssetRepo()
    finding_repo = _FakeFindingRepo()
    correlation = CorrelationService(finding_repo)

    class _BrokenGraphService:
        async def upsert_finding_node(self, *args, **kwargs):
            raise RuntimeError("graph boom")

        async def find_node_by_source(self, *args, **kwargs):
            return None

        async def add_edge(self, *args, **kwargs):
            raise RuntimeError("graph boom")

    engine = ExecutionEngine(
        scan_repository=scan_repo,
        scope_guard=_FakeScopeGuard(),
        plugin_manager=_FakePluginManager(),
        artifact_store=_FakeArtifactStore(),
        audit_log_repository=_FakeAuditRepo(),
        tool_result_repository=_FakeToolResultRepo(),
        normalizer_registry=_FakeNormalizerRegistry(),
        default_timeout_seconds=30,
        correlation_service=correlation,
        asset_service=AssetService(asset_repo),
        graph_service=_BrokenGraphService(),  # type: ignore[arg-type]
    )

    await engine.run(scan.id)
    assert scan_repo.completions, "Scan should complete even when graph_service fails"
