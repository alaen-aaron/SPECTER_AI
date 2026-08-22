"""
M5.5 Phase 4 — Observability Tests.

Tests for:
  - MetricsCollector (counters, gauges, histograms, snapshot)
  - ObservabilityMiddleware (request count, latency tracking)
  - Health endpoint (database, redis, plugins, normalizers checks)
  - Metrics endpoint (returns JSON snapshot)
  - Engine scan metrics (scans_total, scan_duration_seconds, etc.)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.metrics import MetricsCollector, metrics
from app.main import create_app


# ---------------------------------------------------------------------------
# MetricsCollector unit tests
# ---------------------------------------------------------------------------


class TestMetricsCollector:
    def test_inc_counter_default_tags(self) -> None:
        m = MetricsCollector()
        m.inc_counter("requests")
        m.inc_counter("requests")
        snap = m.snapshot()
        assert snap["counters"]["requests"][""] == 2

    def test_inc_counter_with_tags(self) -> None:
        m = MetricsCollector()
        m.inc_counter("requests", tags={"method": "GET"})
        m.inc_counter("requests", tags={"method": "POST"})
        m.inc_counter("requests", tags={"method": "GET"})
        snap = m.snapshot()
        assert snap["counters"]["requests"]["method=GET"] == 2
        assert snap["counters"]["requests"]["method=POST"] == 1

    def test_set_gauge(self) -> None:
        m = MetricsCollector()
        m.set_gauge("connections", 42)
        snap = m.snapshot()
        assert snap["gauges"]["connections"] == 42

    def test_inc_gauge(self) -> None:
        m = MetricsCollector()
        m.inc_gauge("inflight", 1)
        m.inc_gauge("inflight", 1)
        m.inc_gauge("inflight", -1)
        snap = m.snapshot()
        assert snap["gauges"]["inflight"] == 1.0

    def test_set_gauge_with_tags(self) -> None:
        m = MetricsCollector()
        m.set_gauge("pool_size", 10, tags={"pool": "primary"})
        snap = m.snapshot()
        assert snap["gauges"]["pool_size#pool=primary"] == 10

    def test_observe_histogram(self) -> None:
        m = MetricsCollector()
        for v in [0.1, 0.2, 0.3, 0.4, 0.5]:
            m.observe_histogram("latency", v)
        snap = m.snapshot()
        h = snap["histograms"]["latency"][""]
        assert h["count"] == 5
        assert h["min"] == 0.1
        assert h["max"] == 0.5
        assert h["sum"] == 1.5
        assert h["mean"] == 0.3

    def test_observe_histogram_with_tags(self) -> None:
        m = MetricsCollector()
        m.observe_histogram("latency", 1.0, tags={"path": "/api/v1/health"})
        m.observe_histogram("latency", 2.0, tags={"path": "/api/v1/scans"})
        snap = m.snapshot()
        assert "path=/api/v1/health" in snap["histograms"]["latency"]
        assert "path=/api/v1/scans" in snap["histograms"]["latency"]

    def test_snapshot_includes_uptime(self) -> None:
        m = MetricsCollector()
        snap = m.snapshot()
        assert "uptime_seconds" in snap
        assert isinstance(snap["uptime_seconds"], float)

    def test_reset_clears_all(self) -> None:
        m = MetricsCollector()
        m.inc_counter("c")
        m.set_gauge("g", 1)
        m.observe_histogram("h", 1.0)
        m.reset()
        snap = m.snapshot()
        assert snap["counters"] == {}
        assert snap["gauges"] == {}
        assert snap["histograms"] == {}

    def test_histogram_p95_p99(self) -> None:
        m = MetricsCollector()
        for i in range(100):
            m.observe_histogram("resp", float(i))
        snap = m.snapshot()
        h = snap["histograms"]["resp"][""]
        assert h["p95"] == 95.0
        assert h["p99"] == 99.0


# ---------------------------------------------------------------------------
# Process-wide singleton sanity
# ---------------------------------------------------------------------------


class TestGlobalMetrics:
    def test_singleton_is_metrics_collector(self) -> None:
        assert isinstance(metrics, MetricsCollector)

    def test_singleton_is_reusable(self) -> None:
        from app.core.metrics import metrics as m2

        assert metrics is m2


# ---------------------------------------------------------------------------
# Middleware integration test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_middleware_records_request_metrics() -> None:
    """ObservabilityMiddleware increments counters for incoming requests."""
    app = create_app()
    transport = ASGITransport(app=app)
    metrics.reset()

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/api/v1/health")
        assert resp.status_code == 200

    snap = metrics.snapshot()
    total = snap["counters"].get("http_requests_total", {})
    assert sum(total.values()) >= 1, "At least one request should be counted"


# ---------------------------------------------------------------------------
# Metrics endpoint test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metrics_endpoint_returns_snapshot() -> None:
    """GET /api/v1/metrics returns a JSON snapshot with expected keys."""
    app = create_app()
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/api/v1/metrics")
        assert resp.status_code == 200
        body = resp.json()
        assert "uptime_seconds" in body
        assert "counters" in body
        assert "gauges" in body
        assert "histograms" in body


# ---------------------------------------------------------------------------
# Health endpoint tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_endpoint_includes_plugins() -> None:
    """Health endpoint checks database, redis, plugins, and normalizers."""
    app = create_app()
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/api/v1/health")
        assert resp.status_code == 200
        body = resp.json()
        names = {c["name"] for c in body["components"]}
        assert "database" in names
        assert "redis" in names
        assert "plugins" in names
        assert "normalizers" in names


@pytest.mark.asyncio
async def test_health_endpoint_shows_component_details() -> None:
    """Health endpoint returns all expected component details."""
    app = create_app()
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/api/v1/health")
        body = resp.json()
        # Each component should have name and healthy keys
        for comp in body["components"]:
            assert "name" in comp
            assert "healthy" in comp
            assert isinstance(comp["healthy"], bool)
        # Overall status should be ok or degraded
        assert body["status"] in ("ok", "degraded")


# ---------------------------------------------------------------------------
# Engine scan metrics test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_engine_increments_scan_metrics() -> None:
    """ExecutionEngine records scans_total and scan_duration_seconds."""
    from app.application.asset_service import AssetService
    from app.application.correlation_service import CorrelationService
    from app.application.graph_service import GraphService
    from app.domain.entities import AuditLogEntry, Scan
    from app.domain.value_objects import ScanStatus
    from app.infrastructure.execution.engine import ExecutionEngine
    from app.plugins.normalizer import ToolOutputNormalizer

    class _FakeScanRepo:
        def __init__(self, scan: Scan) -> None:
            self._scans: dict[UUID, Scan] = {scan.id: scan}

        async def get(self, scan_id: UUID) -> Scan | None:
            return self._scans.get(scan_id)

        async def list(self, project_id: UUID, limit: int = 20, cursor=None) -> list[Scan]:
            return []

        async def update_status(self, scan_id: UUID, status: ScanStatus) -> None:
            s = self._scans.get(scan_id)
            if s is not None:
                s.status = status

        async def append_log(self, scan_id: UUID, logs_path: str) -> None:
            pass

        async def complete(self, scan_id: UUID, exit_code: int, artifacts_path: str | None) -> None:
            s = self._scans.get(scan_id)
            if s is not None:
                s.status = ScanStatus.COMPLETED

        async def fail(self, scan_id: UUID, error_message: str, exit_code: int | None) -> None:
            s = self._scans.get(scan_id)
            if s is not None:
                s.status = ScanStatus.FAILED

    class _FakeScopeGuard:
        async def validate_targets(self, project_id: UUID, target_ids: list) -> None:
            pass

    @dataclass
    class _Result:
        stdout: str = "OK"
        stderr: str = ""
        success: bool = True
        exit_code: int | None = 0

    class _FakePluginManager:
        def run(self, plugin: str, config: dict, timeout: int, runner=None) -> _Result:
            return _Result()

    class _FakeAuditRepo:
        async def add(self, entry: AuditLogEntry) -> None:
            pass
        async def list_for_organization(self, organization_id: UUID) -> list:
            return []

    class _FakeToolResultRepo:
        async def add(self, tool_result: object) -> None:
            pass
        async def get(self, tool_result_id: UUID) -> None:
            return None
        async def list_for_scan(self, scan_id: UUID) -> list:
            return []

    class _FakeArtifactStore:
        def write_logs(self, scan_id: UUID, stdout: str, stderr: str) -> str:
            return f"/tmp/logs/{scan_id}"
        def artifacts_directory_if_any(self, scan_id: UUID) -> str | None:
            return None

    class _EmptyNormalizerRegistry:
        def get(self, plugin_name: str) -> ToolOutputNormalizer | None:
            return None
        def list(self) -> list[ToolOutputNormalizer]:
            return []

    metrics.reset()
    now = datetime.now(UTC)
    scan = Scan(
        id=uuid4(),
        project_id=uuid4(),
        initiated_by=uuid4(),
        plugin="nmap",
        status=ScanStatus.QUEUED,
        target_ids=[],
        plugin_config={"target": "10.0.0.1"},
        created_at=now,
    )

    engine = ExecutionEngine(
        scan_repository=_FakeScanRepo(scan),
        scope_guard=_FakeScopeGuard(),
        plugin_manager=_FakePluginManager(),
        artifact_store=_FakeArtifactStore(),
        audit_log_repository=_FakeAuditRepo(),
        tool_result_repository=_FakeToolResultRepo(),
        normalizer_registry=_EmptyNormalizerRegistry(),
        default_timeout_seconds=30,
        correlation_service=None,
        asset_service=None,
        graph_service=None,
    )

    await engine.run(scan.id)

    snap = metrics.snapshot()
    scan_counters = snap["counters"].get("scans_total", {})
    assert sum(scan_counters.values()) >= 2, (
        "Should have at least 'started' and 'completed' counters"
    )
    scan_hist = snap["histograms"].get("scan_duration_seconds", {})
    assert len(scan_hist) > 0, "scan_duration_seconds histogram should have data"
