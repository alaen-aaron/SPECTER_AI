"""
M5.5 Phase 3 — Full-Pipeline Integration Tests.

Uses REAL normalizers (NmapNormalizer, PingNormalizer) with realistic
tool stdout, feeding through real AssetService, CorrelationService, and
GraphService — backed by in-memory fakes for the DB layer.

Verifies the end-to-end flow:
  simulated stdout → normalizer → normalized_payload → ToolResult
  → AssetService.upsert_from_tool_result → CorrelationService.correlate
  → engine links findings → assets → graph projection
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.application.asset_service import AssetService
from app.application.correlation_service import CorrelationService
from app.application.graph_service import GraphService
from app.domain.entities import AuditLogEntry, Finding, GraphEdge, GraphNode, Scan, ToolResult
from app.domain.value_objects import (
    AssetType,
    FindingStatus,
    GraphEdgeType,
    GraphNodeType,
    ScanStatus,
    Severity,
)
from app.infrastructure.execution.engine import ExecutionEngine
from app.plugins.normalizer import ToolOutputNormalizer
from app.plugins.normalizers.nmap_normalizer import NmapNormalizer
from app.plugins.normalizers.ping_normalizer import PingNormalizer


# ---------------------------------------------------------------------------
# Realistic simulated tool stdout
# ---------------------------------------------------------------------------

NMAP_STDOUT = """\
Starting Nmap 7.94 ( https://nmap.org ) at 2025-01-15 10:30 UTC
Nmap scan report for 10.10.10.5
Host is up (0.0012s latency).
22/tcp    open  ssh        OpenSSH 8.9p1 Ubuntu 3ubuntu0.6
80/tcp    open  http       nginx 1.24.0
443/tcp   open  https      nginx 1.24.0
3306/tcp  open  mysql      MySQL 8.0.36
6379/tcp  closed redis
8080/tcp  filtered http-proxy
Nmap done: 1 IP address (1 host up) scanned in 12.34 seconds
        4 ports scanned
"""

NMAP_STDOUT_INSECURE = """\
Nmap scan report for 192.168.1.20
Host is up (0.0020s latency).
21/tcp    open  ftp        vsftpd 3.0.5
23/tcp    open  telnet     Linux telnetd
80/tcp    open  http       Apache httpd 2.4.58
5900/tcp  open  vnc        VNC (protocol 5.3)
Nmap done: 1 IP address (1 host up) scanned in 8.12 seconds
        4 ports scanned
"""

PING_STDOUT = """\
PING 10.10.10.5 (10.10.10.5) 56(84) bytes of data.
64 bytes from 10.10.10.5: icmp_seq=1 ttl=64 time=0.456 ms
64 bytes from 10.10.10.5: icmp_seq=2 ttl=64 time=0.389 ms
64 bytes from 10.10.10.5: icmp_seq=3 ttl=64 time=0.412 ms

--- 10.10.10.5 ping statistics ---
3 packets transmitted, 3 received, 0% packet loss, time 2003ms
rtt min/avg/max/mdev = 0.389/0.419/0.456/0.028 ms
"""

PING_STDOUT_UNREACHABLE = """\
PING 192.168.1.99 (192.168.1.99) 56(84) bytes of data.

--- 192.168.1.99 ping statistics ---
4 packets transmitted, 0 received, 100% packet loss, time 3004ms
"""

NMAP_STDOUT_HOST_DOWN = """\
Nmap scan report for 192.168.1.50
Host seems down. If it is really up, but blocking our ping probes, try -Pn
Nmap done: 1 IP address (0 hosts up) scanned in 2.01 seconds
        1 ports scanned
"""


# ---------------------------------------------------------------------------
# In-memory fakes (shared across Phase 3 tests)
# ---------------------------------------------------------------------------


class _FakeAssetRepo:
    def __init__(self) -> None:
        self.assets: dict[UUID, object] = {}

    async def get_by_id(self, asset_id: UUID):
        return self.assets.get(asset_id)

    async def list_for_project(self, project_id, asset_type=None, limit=20, cursor=None):
        results = [a for a in self.assets.values() if a.project_id == project_id]
        if asset_type is not None:
            results = [a for a in results if a.asset_type == asset_type]
        return results

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
        self.edges: list[GraphEdge] = []

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
            if (
                node.project_id == project_id
                and node.node_type == node_type
                and node.source_table == source_table
                and node.source_id == source_id
            ):
                return node
        return None

    async def find_edge(self, project_id, from_id, to_id, rel_type):
        return None

    async def get_neighbors(self, node_id, edge_type=None, direction="outgoing"):
        return []

    async def shortest_path(self, from_id, to_id, max_depth=10):
        return None

    async def list_nodes_for_project(self, project_id, node_type=None):
        return [
            n
            for n in self.nodes.values()
            if n.project_id == project_id
            and (node_type is None or n.node_type == node_type)
        ]

    async def list_edges_for_project(self, project_id, rel_type=None):
        return [e for e in self.edges if e.project_id == project_id]

    async def remove_node(self, node_id: UUID) -> None:
        self.nodes.pop(node_id, None)

    async def remove_edge(self, edge_id: UUID) -> None:
        self.edges = [e for e in self.edges if e.id != edge_id]

    async def clear_project(self, project_id: UUID) -> None:
        to_remove = [nid for nid, n in self.nodes.items() if n.project_id == project_id]
        for nid in to_remove:
            del self.nodes[nid]
        self.edges = [e for e in self.edges if e.project_id != project_id]

    async def blast_radius(self, project_id, start_id, max_depth=5):
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

    async def list_for_project(self, project_id, severity=None, limit=20, cursor=None):
        results = [f for f in self.findings.values() if f.project_id == project_id]
        if severity is not None:
            results = [f for f in results if f.severity == severity]
        return results

    async def get_by_dedup_key(self, project_id: UUID, dedup_key: str) -> Finding | None:
        return self._dedup.get(dedup_key)

    async def update_status(self, finding_id: UUID, status) -> None:
        f = self.findings.get(finding_id)
        if f is not None:
            f.status = status


class _FakeScanRepo:
    def __init__(self, scan: Scan) -> None:
        self._scans: dict[UUID, Scan] = {scan.id: scan}

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

    async def append_log(self, scan_id: UUID, logs_path: str) -> None:
        pass

    async def complete(self, scan_id: UUID, exit_code: int, artifacts_path: str | None) -> None:
        scan = self._scans.get(scan_id)
        if scan is not None:
            scan.status = ScanStatus.COMPLETED
            scan.exit_code = exit_code

    async def fail(self, scan_id: UUID, error_message: str, exit_code: int | None) -> None:
        scan = self._scans.get(scan_id)
        if scan is not None:
            scan.status = ScanStatus.FAILED


class _FakeScopeGuard:
    async def validate_targets(self, project_id: UUID, target_ids: list) -> None:
        pass


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
        return [r for r in self.results if r.scan_id == scan_id]


class _FakeArtifactStore:
    def write_logs(self, scan_id: UUID, stdout: str, stderr: str) -> str:
        return f"/tmp/logs/{scan_id}"

    def artifacts_directory_if_any(self, scan_id: UUID) -> str | None:
        return None


@dataclass
class _PluginResult:
    stdout: str
    stderr: str
    success: bool
    exit_code: int | None = 0


class _FakePluginManager:
    def __init__(self, stdout: str = "", stderr: str = "", success: bool = True) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self._success = success

    def run(self, plugin: str, config: dict, timeout: int):
        return _PluginResult(
            self._stdout, self._stderr, self._success, 0 if self._success else 1
        )


class _FakeNormalizerRegistry:
    """Registry backed by real normalizer implementations."""

    def __init__(self) -> None:
        self._nmap = NmapNormalizer()
        self._ping = PingNormalizer()

    def get(self, plugin_name: str) -> ToolOutputNormalizer | None:
        if plugin_name == "nmap":
            return self._nmap
        if plugin_name == "ping":
            return self._ping
        return None

    def list(self) -> list[ToolOutputNormalizer]:
        return [self._nmap, self._ping]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_scan(
    project_id: UUID,
    plugin: str = "nmap",
    target: str = "10.10.10.5",
) -> Scan:
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


def _build_engine(
    scan: Scan,
    plugin_stdout: str,
    *,
    asset_repo: _FakeAssetRepo | None = None,
    graph_repo: _FakeGraphRepo | None = None,
    finding_repo: _FakeFindingRepo | None = None,
) -> tuple[ExecutionEngine, _FakeAssetRepo, _FakeGraphRepo, _FakeFindingRepo]:
    """Build a fully-wired ExecutionEngine with real normalizers."""
    a_repo = asset_repo or _FakeAssetRepo()
    g_repo = graph_repo or _FakeGraphRepo()
    f_repo = finding_repo or _FakeFindingRepo()
    graph_service = GraphService(g_repo)
    asset_service = AssetService(a_repo, graph_service)
    correlation = CorrelationService(f_repo)

    engine = ExecutionEngine(
        scan_repository=_FakeScanRepo(scan),
        scope_guard=_FakeScopeGuard(),
        plugin_manager=_FakePluginManager(stdout=plugin_stdout),
        artifact_store=_FakeArtifactStore(),
        audit_log_repository=_FakeAuditRepo(),
        tool_result_repository=_FakeToolResultRepo(),
        normalizer_registry=_FakeNormalizerRegistry(),
        default_timeout_seconds=30,
        correlation_service=correlation,
        asset_service=asset_service,
        graph_service=graph_service,
    )
    return engine, a_repo, g_repo, f_repo


# ---------------------------------------------------------------------------
# Tests — Nmap full pipeline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nmap_full_pipeline_creates_host_and_services():
    """Nmap scan with open+closed+filtered ports → host asset + 4 service assets."""
    project_id = uuid4()
    scan = _make_scan(project_id, plugin="nmap", target="10.10.10.5")
    engine, asset_repo, _, _ = _build_engine(scan, NMAP_STDOUT)

    await engine.run(scan.id)

    hosts = [a for a in asset_repo.assets.values() if a.asset_type == AssetType.HOST]
    services = [a for a in asset_repo.assets.values() if a.asset_type == AssetType.SERVICE]
    assert len(hosts) == 1, f"Expected 1 host, got {len(hosts)}"
    assert hosts[0].value == "10.10.10.5"
    assert hosts[0].metadata.get("host_up") is True
    assert len(services) == 4, f"Expected 4 services (open ports only), got {len(services)}"

    svc_values = {s.value for s in services}
    assert "ssh://10.10.10.5:22/tcp" in svc_values
    assert "http://10.10.10.5:80/tcp" in svc_values
    assert "https://10.10.10.5:443/tcp" in svc_values
    assert "mysql://10.10.10.5:3306/tcp" in svc_values

    closed_or_filtered = [
        a
        for a in asset_repo.assets.values()
        if a.asset_type == AssetType.SERVICE
        and ("6379" in a.value or "8080" in a.value)
    ]
    assert len(closed_or_filtered) == 0, "Closed/filtered ports should not create service assets"


@pytest.mark.asyncio
async def test_nmap_full_pipeline_creates_findings():
    """Nmap scan → findings created by CorrelationService for each open port."""
    project_id = uuid4()
    scan = _make_scan(project_id, plugin="nmap", target="10.10.10.5")
    engine, _, _, finding_repo = _build_engine(scan, NMAP_STDOUT)

    await engine.run(scan.id)

    assert len(finding_repo.findings) == 4, (
        f"Expected 4 findings (one per open port), got {len(finding_repo.findings)}"
    )
    for finding in finding_repo.findings.values():
        assert finding.status == FindingStatus.OPEN
        assert finding.severity == Severity.INFO
        assert finding.title.startswith("Open port:")


@pytest.mark.asyncio
async def test_nmap_insecure_services_create_higher_severity_findings():
    """Nmap scan with ftp/telnet/vnc → findings with MEDIUM/HIGH severity."""
    project_id = uuid4()
    scan = _make_scan(project_id, plugin="nmap", target="192.168.1.20")
    engine, _, _, finding_repo = _build_engine(scan, NMAP_STDOUT_INSECURE)

    await engine.run(scan.id)

    findings_by_title = {f.title: f for f in finding_repo.findings.values()}
    ftp_finding = findings_by_title.get("Insecure service: ftp on 192.168.1.20:21")
    assert ftp_finding is not None, "FTP finding should exist"
    assert ftp_finding.severity == Severity.LOW

    telnet_finding = findings_by_title.get("Insecure service: telnet on 192.168.1.20:23")
    assert telnet_finding is not None, "Telnet finding should exist"
    assert telnet_finding.severity == Severity.MEDIUM

    vnc_finding = findings_by_title.get("Insecure service: vnc on 192.168.1.20:5900")
    assert vnc_finding is not None, "VNC finding should exist"
    assert vnc_finding.severity == Severity.MEDIUM

    http_finding = findings_by_title.get("Open port: http on 192.168.1.20:80")
    assert http_finding is not None, "HTTP finding should exist"
    assert http_finding.severity == Severity.INFO


@pytest.mark.asyncio
async def test_nmap_full_pipeline_creates_graph_nodes_and_edges():
    """Nmap scan → host node, service nodes, and HOSTS edges in the graph."""
    project_id = uuid4()
    scan = _make_scan(project_id, plugin="nmap", target="10.10.10.5")
    engine, _, graph_repo, _ = _build_engine(scan, NMAP_STDOUT)

    await engine.run(scan.id)

    asset_nodes = [
        n for n in graph_repo.nodes.values() if n.node_type == GraphNodeType.ASSET
    ]
    assert len(asset_nodes) == 5, f"Expected 5 asset nodes (1 host + 4 services), got {len(asset_nodes)}"

    host_nodes = [n for n in asset_nodes if "10.10.10.5" == n.label]
    assert len(host_nodes) == 1, "Exactly one host node"
    service_nodes = [n for n in asset_nodes if n.label != "10.10.10.5"]
    assert len(service_nodes) == 4, "4 service nodes"

    hosts_edges = [
        e for e in graph_repo.edges if e.relationship_type == GraphEdgeType.HOSTS
    ]
    assert len(hosts_edges) == 4, f"Expected 4 HOSTS edges (host→each service), got {len(hosts_edges)}"

    host_node_id = host_nodes[0].id
    for edge in hosts_edges:
        assert edge.from_node_id == host_node_id, "All HOSTS edges originate from host node"


@pytest.mark.asyncio
async def test_nmap_finding_to_asset_linking():
    """After engine run, findings should have asset_id pointing to matching service asset."""
    project_id = uuid4()
    scan = _make_scan(project_id, plugin="nmap", target="10.10.10.5")
    engine, asset_repo, _, finding_repo = _build_engine(scan, NMAP_STDOUT)

    await engine.run(scan.id)

    linked = [f for f in finding_repo.findings.values() if f.asset_id is not None]
    assert len(linked) == 4, f"Expected 4 linked findings, got {len(linked)}"
    for finding in linked:
        assert finding.asset_id in asset_repo.assets, (
            f"Finding {finding.id} asset_id {finding.asset_id} not in assets"
        )


@pytest.mark.asyncio
async def test_nmap_finding_nodes_in_graph():
    """After engine run, findings should be projected as FINDING nodes with EVIDENCED_BY edges."""
    project_id = uuid4()
    scan = _make_scan(project_id, plugin="nmap", target="10.10.10.5")
    engine, _, graph_repo, _ = _build_engine(scan, NMAP_STDOUT)

    await engine.run(scan.id)

    finding_nodes = [
        n for n in graph_repo.nodes.values() if n.node_type == GraphNodeType.FINDING
    ]
    assert len(finding_nodes) == 4, f"Expected 4 finding nodes, got {len(finding_nodes)}"

    evidenced_by = [
        e for e in graph_repo.edges if e.relationship_type == GraphEdgeType.EVIDENCED_BY
    ]
    assert len(evidenced_by) == 4, f"Expected 4 EVIDENCED_BY edges, got {len(evidenced_by)}"


# ---------------------------------------------------------------------------
# Tests — Ping full pipeline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ping_full_pipeline_creates_host_asset():
    """Ping scan → host asset with reachable=True and correct metadata."""
    project_id = uuid4()
    scan = _make_scan(project_id, plugin="ping", target="10.10.10.5")
    engine, asset_repo, _, _ = _build_engine(scan, PING_STDOUT)

    await engine.run(scan.id)

    hosts = [a for a in asset_repo.assets.values() if a.asset_type == AssetType.HOST]
    assert len(hosts) == 1
    assert hosts[0].value == "10.10.10.5"
    assert hosts[0].metadata.get("reachable") is True


@pytest.mark.asyncio
async def test_ping_unreachable_host_creates_asset():
    """Ping to unreachable host → asset created with reachable=False."""
    project_id = uuid4()
    scan = _make_scan(project_id, plugin="ping", target="192.168.1.99")
    engine, asset_repo, _, _ = _build_engine(scan, PING_STDOUT_UNREACHABLE)

    await engine.run(scan.id)

    hosts = [a for a in asset_repo.assets.values() if a.asset_type == AssetType.HOST]
    assert len(hosts) == 1
    assert hosts[0].metadata.get("reachable") is False


@pytest.mark.asyncio
async def test_ping_creates_graph_asset_node():
    """Ping scan → host node projected to graph."""
    project_id = uuid4()
    scan = _make_scan(project_id, plugin="ping", target="10.10.10.5")
    engine, _, graph_repo, _ = _build_engine(scan, PING_STDOUT)

    await engine.run(scan.id)

    asset_nodes = [
        n for n in graph_repo.nodes.values() if n.node_type == GraphNodeType.ASSET
    ]
    assert len(asset_nodes) == 1
    assert asset_nodes[0].label == "10.10.10.5"


# ---------------------------------------------------------------------------
# Tests — Engine status transitions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_engine_completes_scan_successfully():
    """Engine marks scan as COMPLETED after successful plugin run."""
    project_id = uuid4()
    scan = _make_scan(project_id, plugin="nmap", target="10.10.10.5")
    engine, _, _, _ = _build_engine(scan, NMAP_STDOUT)

    await engine.run(scan.id)

    assert scan.status == ScanStatus.COMPLETED


@pytest.mark.asyncio
async def test_engine_marks_scan_failed_on_plugin_failure():
    """Engine marks scan as FAILED when plugin returns failure."""
    project_id = uuid4()
    scan = _make_scan(project_id, plugin="nmap", target="10.10.10.5")

    engine = ExecutionEngine(
        scan_repository=_FakeScanRepo(scan),
        scope_guard=_FakeScopeGuard(),
        plugin_manager=_FakePluginManager(stdout="error output", success=False),
        artifact_store=_FakeArtifactStore(),
        audit_log_repository=_FakeAuditRepo(),
        tool_result_repository=_FakeToolResultRepo(),
        normalizer_registry=_FakeNormalizerRegistry(),
        default_timeout_seconds=30,
        correlation_service=None,
        asset_service=None,
        graph_service=None,
    )

    await engine.run(scan.id)

    assert scan.status == ScanStatus.FAILED


@pytest.mark.asyncio
async def test_engine_skips_cancelled_scan():
    """Engine does not run plugin if scan is already CANCELLED."""
    project_id = uuid4()
    scan = _make_scan(project_id, plugin="nmap", target="10.10.10.5")
    scan.status = ScanStatus.CANCELLED
    engine, asset_repo, _, _ = _build_engine(scan, NMAP_STDOUT)

    await engine.run(scan.id)

    assert len(asset_repo.assets) == 0, "No assets should be created for cancelled scan"


# ---------------------------------------------------------------------------
# Tests — Deduplication
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nmap_asset_deduplication_on_second_scan():
    """Scanning the same host twice should update existing assets, not duplicate them."""
    project_id = uuid4()

    scan1 = _make_scan(project_id, plugin="nmap", target="10.10.10.5")
    engine1, asset_repo, _, _ = _build_engine(scan1, NMAP_STDOUT)
    await engine1.run(scan1.id)

    count_after_first = len(asset_repo.assets)

    scan2 = _make_scan(project_id, plugin="nmap", target="10.10.10.5")
    engine2 = ExecutionEngine(
        scan_repository=_FakeScanRepo(scan2),
        scope_guard=_FakeScopeGuard(),
        plugin_manager=_FakePluginManager(stdout=NMAP_STDOUT),
        artifact_store=_FakeArtifactStore(),
        audit_log_repository=_FakeAuditRepo(),
        tool_result_repository=_FakeToolResultRepo(),
        normalizer_registry=_FakeNormalizerRegistry(),
        default_timeout_seconds=30,
        correlation_service=CorrelationService(_FakeFindingRepo()),
        asset_service=AssetService(asset_repo, GraphService(_FakeGraphRepo())),
        graph_service=GraphService(_FakeGraphRepo()),
    )
    await engine2.run(scan2.id)

    assert len(asset_repo.assets) == count_after_first, (
        f"Asset count should be same after second scan (dedup), "
        f"got {len(asset_repo.assets)} vs {count_after_first}"
    )


@pytest.mark.asyncio
async def test_nmap_finding_deduplication_on_second_scan():
    """Scanning the same host twice should dedup findings by dedup_key."""
    project_id = uuid4()
    finding_repo = _FakeFindingRepo()

    scan1 = _make_scan(project_id, plugin="nmap", target="10.10.10.5")
    engine1, asset_repo, _, _ = _build_engine(scan1, NMAP_STDOUT, finding_repo=finding_repo)
    await engine1.run(scan1.id)

    count_after_first = len(finding_repo.findings)

    scan2 = _make_scan(project_id, plugin="nmap", target="10.10.10.5")
    engine2 = ExecutionEngine(
        scan_repository=_FakeScanRepo(scan2),
        scope_guard=_FakeScopeGuard(),
        plugin_manager=_FakePluginManager(stdout=NMAP_STDOUT),
        artifact_store=_FakeArtifactStore(),
        audit_log_repository=_FakeAuditRepo(),
        tool_result_repository=_FakeToolResultRepo(),
        normalizer_registry=_FakeNormalizerRegistry(),
        default_timeout_seconds=30,
        correlation_service=CorrelationService(finding_repo),
        asset_service=AssetService(asset_repo, GraphService(_FakeGraphRepo())),
        graph_service=GraphService(_FakeGraphRepo()),
    )
    await engine2.run(scan2.id)

    assert len(finding_repo.findings) == count_after_first, (
        f"Finding count should be same after second scan (dedup), "
        f"got {len(finding_repo.findings)} vs {count_after_first}"
    )


# ---------------------------------------------------------------------------
# Tests — Normalizer accuracy (real normalizers with realistic output)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nmap_normalizer_parses_all_port_fields():
    """NmapNormalizer correctly extracts port, protocol, state, service, version."""
    normalizer = NmapNormalizer()
    payload = normalizer.normalize(NMAP_STDOUT, "", {"target": "10.10.10.5"})

    assert payload["target"] == "10.10.10.5"
    assert payload["host_up"] is True
    assert payload["open_port_count"] == 4
    assert payload["closed_port_count"] == 1
    assert payload["filtered_port_count"] == 1
    assert payload["total_ports_scanned"] == 6

    open_ports = [p for p in payload["ports"] if p["state"] == "open"]
    assert len(open_ports) == 4

    ssh_port = next(p for p in open_ports if p["port"] == 22)
    assert ssh_port["protocol"] == "tcp"
    assert ssh_port["service"] == "ssh"
    assert "OpenSSH" in ssh_port["version"]


@pytest.mark.asyncio
async def test_ping_normalizer_parses_rtt_and_stats():
    """PingNormalizer correctly extracts packets, loss, and RTT."""
    normalizer = PingNormalizer()
    payload = normalizer.normalize(PING_STDOUT, "", {"hostname": "10.10.10.5"})

    assert payload["host"] == "10.10.10.5"
    assert payload["reachable"] is True
    assert payload["packets_sent"] == 3
    assert payload["packets_received"] == 3
    assert payload["packet_loss_pct"] == 0
    assert payload["reply_count"] == 3
    assert payload["rtt"]["min_ms"] is not None
    assert payload["rtt"]["avg_ms"] is not None
    assert payload["rtt"]["max_ms"] is not None
    assert payload["rtt"]["mdev_ms"] is not None
    assert payload["rtt"]["min_ms"] < payload["rtt"]["avg_ms"] < payload["rtt"]["max_ms"]


# ---------------------------------------------------------------------------
# Tests — Edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nmap_host_down_produces_host_asset_no_services():
    """Nmap output with 'Host seems down' → host asset with host_up=False, no services."""
    project_id = uuid4()
    scan = _make_scan(project_id, plugin="nmap", target="192.168.1.50")
    engine, asset_repo, _, finding_repo = _build_engine(scan, NMAP_STDOUT_HOST_DOWN)

    await engine.run(scan.id)

    hosts = [a for a in asset_repo.assets.values() if a.asset_type == AssetType.HOST]
    assert len(hosts) == 1, "Host asset should still be created for tracking"
    assert hosts[0].metadata.get("host_up") is False
    services = [a for a in asset_repo.assets.values() if a.asset_type == AssetType.SERVICE]
    assert len(services) == 0, "No service assets when host is down"
    assert len(finding_repo.findings) == 0, "No findings when no ports"


@pytest.mark.asyncio
async def test_engine_without_asset_service_still_completes():
    """Engine completes scan even when asset_service is None (backward compat)."""
    project_id = uuid4()
    scan = _make_scan(project_id, plugin="nmap", target="10.10.10.5")
    f_repo = _FakeFindingRepo()
    scan_repo = _FakeScanRepo(scan)

    engine = ExecutionEngine(
        scan_repository=scan_repo,
        scope_guard=_FakeScopeGuard(),
        plugin_manager=_FakePluginManager(stdout=NMAP_STDOUT),
        artifact_store=_FakeArtifactStore(),
        audit_log_repository=_FakeAuditRepo(),
        tool_result_repository=_FakeToolResultRepo(),
        normalizer_registry=_FakeNormalizerRegistry(),
        default_timeout_seconds=30,
        correlation_service=CorrelationService(f_repo),
        asset_service=None,
        graph_service=None,
    )

    await engine.run(scan.id)
    assert scan.status == ScanStatus.COMPLETED
    assert len(f_repo.findings) == 4, "Correlation should still create findings"


@pytest.mark.asyncio
async def test_engine_without_graph_service_still_creates_assets_and_findings():
    """Engine creates assets and findings even when graph_service is None."""
    project_id = uuid4()
    scan = _make_scan(project_id, plugin="nmap", target="10.10.10.5")
    asset_repo = _FakeAssetRepo()
    f_repo = _FakeFindingRepo()

    engine = ExecutionEngine(
        scan_repository=_FakeScanRepo(scan),
        scope_guard=_FakeScopeGuard(),
        plugin_manager=_FakePluginManager(stdout=NMAP_STDOUT),
        artifact_store=_FakeArtifactStore(),
        audit_log_repository=_FakeAuditRepo(),
        tool_result_repository=_FakeToolResultRepo(),
        normalizer_registry=_FakeNormalizerRegistry(),
        default_timeout_seconds=30,
        correlation_service=CorrelationService(f_repo),
        asset_service=AssetService(asset_repo),
        graph_service=None,
    )

    await engine.run(scan.id)
    assert scan.status == ScanStatus.COMPLETED
    hosts = [a for a in asset_repo.assets.values() if a.asset_type == AssetType.HOST]
    assert len(hosts) == 1
    assert len(f_repo.findings) == 4


@pytest.mark.asyncio
async def test_scan_with_no_normalizer_still_persists_tool_result():
    """Plugin without a registered normalizer still persists ToolResult with empty payload."""
    project_id = uuid4()
    scan = _make_scan(project_id, plugin="unknown_tool", target="example.com")

    class _EmptyNormalizerRegistry:
        def get(self, plugin_name: str) -> ToolOutputNormalizer | None:
            return None

        def list(self) -> list[ToolOutputNormalizer]:
            return []

    tool_result_repo = _FakeToolResultRepo()
    engine = ExecutionEngine(
        scan_repository=_FakeScanRepo(scan),
        scope_guard=_FakeScopeGuard(),
        plugin_manager=_FakePluginManager(stdout="raw output here"),
        artifact_store=_FakeArtifactStore(),
        audit_log_repository=_FakeAuditRepo(),
        tool_result_repository=tool_result_repo,
        normalizer_registry=_EmptyNormalizerRegistry(),
        default_timeout_seconds=30,
        correlation_service=None,
        asset_service=None,
        graph_service=None,
    )

    await engine.run(scan.id)

    assert len(tool_result_repo.results) == 1
    tr = tool_result_repo.results[0]
    assert tr.plugin == "unknown_tool"
    assert tr.normalized_payload == {}
    assert tr.target == "example.com"
    assert scan.status == ScanStatus.COMPLETED
