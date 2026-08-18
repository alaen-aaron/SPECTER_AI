"""
Unit tests for M5: Production plugins, Workflow Suggestion Service, and API schemas.

Tests cover:
- Production plugin capability declarations and metadata
- Plugin registry enhanced methods (category, tag, health, compatibility)
- Workflow suggestion service
- Plugin schemas
"""

from __future__ import annotations

from typing import Any

import pytest

from app.plugins.base import Plugin, PluginCapability, PluginCategory, PluginMetadata, PluginResult
from app.plugins.registry import PluginRegistry

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class _FakePlugin(Plugin):
    """Deterministic plugin for testing."""

    def __init__(
        self,
        plugin_name: str = "fake",
        *,
        cap: PluginCapability | None = None,
        meta: PluginMetadata | None = None,
    ) -> None:
        self._name = plugin_name
        self._cap = cap or PluginCapability()
        self._meta = meta or PluginMetadata()

    def name(self) -> str:
        return self._name

    def description(self) -> str:
        return f"Fake {self._name} plugin"

    def validate_config(self, config: dict[str, Any]) -> None:
        pass

    def execute(self, config: dict[str, Any], timeout_seconds: int) -> PluginResult:
        return PluginResult(
            success=True, stdout="output", stderr="", exit_code=0,
            metadata={"plugin": self._name},
        )

    def capability(self) -> PluginCapability:
        return self._cap

    def metadata(self) -> PluginMetadata:
        return self._meta


# ---------------------------------------------------------------------------
# Production plugin tests
# ---------------------------------------------------------------------------


class TestProductionPlugins:
    """Verify all production plugins have proper capability and metadata."""

    def test_subfinder_plugin(self) -> None:
        from app.plugins.subfinder_plugin import SubfinderPlugin
        plugin = SubfinderPlugin()
        assert plugin.name() == "subfinder"
        cap = plugin.capability()
        assert "domain" in cap.input_asset_types
        assert "subdomain" in cap.output_asset_types
        meta = plugin.metadata()
        assert meta.category == PluginCategory.RECONNAISSANCE
        assert "subdomain" in meta.tags

    def test_httpx_plugin(self) -> None:
        from app.plugins.httpx_plugin import HttpxPlugin
        plugin = HttpxPlugin()
        assert plugin.name() == "httpx"
        cap = plugin.capability()
        assert "domain" in cap.input_asset_types or "url" in cap.input_asset_types
        meta = plugin.metadata()
        assert meta.category == PluginCategory.RECONNAISSANCE

    def test_nuclei_plugin(self) -> None:
        from app.plugins.nuclei_plugin import NucleiPlugin
        plugin = NucleiPlugin()
        assert plugin.name() == "nuclei"
        cap = plugin.capability()
        assert cap.produces_findings is True  # nuclei discovers vulnerabilities
        meta = plugin.metadata()
        assert meta.category == PluginCategory.VULNERABILITY

    def test_nikto_plugin(self) -> None:
        from app.plugins.nikto_plugin import NiktoPlugin
        plugin = NiktoPlugin()
        assert plugin.name() == "nikto"
        meta = plugin.metadata()
        assert meta.category == PluginCategory.VULNERABILITY

    def test_sqlmap_plugin(self) -> None:
        from app.plugins.sqlmap_plugin import SqlmapPlugin
        plugin = SqlmapPlugin()
        assert plugin.name() == "sqlmap"
        meta = plugin.metadata()
        assert meta.category == PluginCategory.VULNERABILITY

    def test_dalfox_plugin(self) -> None:
        from app.plugins.dalfox_plugin import DalfoxPlugin
        plugin = DalfoxPlugin()
        assert plugin.name() == "dalfox"
        meta = plugin.metadata()
        assert meta.category == PluginCategory.VULNERABILITY

    def test_wpscan_plugin(self) -> None:
        from app.plugins.wpscan_plugin import WpscanPlugin
        plugin = WpscanPlugin()
        assert plugin.name() == "wpscan"
        meta = plugin.metadata()
        assert meta.category == PluginCategory.VULNERABILITY

    def test_ffuf_plugin(self) -> None:
        from app.plugins.ffuf_plugin import FfufPlugin
        plugin = FfufPlugin()
        assert plugin.name() == "ffuf"
        meta = plugin.metadata()
        assert meta.category == PluginCategory.VULNERABILITY

    def test_gobuster_plugin(self) -> None:
        from app.plugins.gobuster_plugin import GobusterPlugin
        plugin = GobusterPlugin()
        assert plugin.name() == "gobuster"
        meta = plugin.metadata()
        assert meta.category == PluginCategory.ENUMERATION

    def test_trufflehog_plugin(self) -> None:
        from app.plugins.trufflehog_plugin import TrufflehogPlugin
        plugin = TrufflehogPlugin()
        assert plugin.name() == "trufflehog"
        meta = plugin.metadata()
        assert meta.category == PluginCategory.VULNERABILITY

    def test_gitleaks_plugin(self) -> None:
        from app.plugins.gitleaks_plugin import GitleaksPlugin
        plugin = GitleaksPlugin()
        assert plugin.name() == "gitleaks"
        meta = plugin.metadata()
        assert meta.category == PluginCategory.VULNERABILITY

    def test_whatweb_plugin(self) -> None:
        from app.plugins.whatweb_plugin import WhatwebPlugin
        plugin = WhatwebPlugin()
        assert plugin.name() == "whatweb"
        meta = plugin.metadata()
        assert meta.category == PluginCategory.INFORMATION_GATHERING

    def test_sslscan_plugin(self) -> None:
        from app.plugins.sslscan_plugin import SslscanPlugin
        plugin = SslscanPlugin()
        assert plugin.name() == "sslscan"
        meta = plugin.metadata()
        assert meta.category == PluginCategory.SCANNING

    def test_katana_plugin(self) -> None:
        from app.plugins.katana_plugin import KatanaPlugin
        plugin = KatanaPlugin()
        assert plugin.name() == "katana"
        meta = plugin.metadata()
        assert meta.category == PluginCategory.RECONNAISSANCE

    def test_naabu_plugin(self) -> None:
        from app.plugins.naabu_plugin import NaabuPlugin
        plugin = NaabuPlugin()
        assert plugin.name() == "naabu"
        meta = plugin.metadata()
        assert meta.category == PluginCategory.SCANNING

    def test_dnsx_plugin(self) -> None:
        from app.plugins.dnsx_plugin import DnsxPlugin
        plugin = DnsxPlugin()
        assert plugin.name() == "dnsx"
        meta = plugin.metadata()
        assert meta.category == PluginCategory.RECONNAISSANCE

    def test_all_plugins_have_required_binaries(self) -> None:
        """Every production plugin (except echo) should declare required binaries."""
        from app.plugins.echo_plugin import EchoPlugin
        from app.plugins.httpx_plugin import HttpxPlugin
        from app.plugins.nmap_plugin import NmapPlugin
        from app.plugins.nuclei_plugin import NucleiPlugin
        from app.plugins.ping_plugin import PingPlugin
        from app.plugins.subfinder_plugin import SubfinderPlugin

        for plugin_cls in [NmapPlugin, SubfinderPlugin, HttpxPlugin, NucleiPlugin]:
            plugin = plugin_cls()
            meta = plugin.metadata()
            msg = f"{plugin.name()} must declare required binaries"
            assert len(meta.required_binaries) > 0, msg

        # Echo and ping have special cases
        echo = EchoPlugin()
        assert len(echo.metadata().required_binaries) == 0  # echo needs no binary
        ping = PingPlugin()
        assert "ping" in ping.metadata().required_binaries


# ---------------------------------------------------------------------------
# Enhanced registry tests
# ---------------------------------------------------------------------------


class TestEnhancedRegistry:
    @pytest.fixture
    def registry(self) -> PluginRegistry:
        return PluginRegistry()

    def test_find_compatible(self, registry: PluginRegistry) -> None:
        registry.register(_FakePlugin(
            "nmap",
            cap=PluginCapability(output_asset_types=frozenset({"host", "port", "service"})),
        ))
        registry.register(_FakePlugin(
            "nikto",
            cap=PluginCapability(input_asset_types=frozenset({"host", "service"})),
        ))
        registry.register(_FakePlugin(
            "subfinder",
            cap=PluginCapability(output_asset_types=frozenset({"subdomain"})),
        ))
        compatible = registry.find_compatible("nmap")
        names = {p.name() for p in compatible}
        assert "nikto" in names
        # subfinder has no inputs declared — excluded by find_compatible
        assert "subfinder" not in names

    def test_validate_compatibility(self, registry: PluginRegistry) -> None:
        registry.register(_FakePlugin(
            "producer",
            cap=PluginCapability(output_asset_types=frozenset({"port"})),
        ))
        registry.register(_FakePlugin(
            "consumer",
            cap=PluginCapability(input_asset_types=frozenset({"port"})),
        ))
        registry.register(_FakePlugin(
            "incompatible",
            cap=PluginCapability(input_asset_types=frozenset({"credential"})),
        ))
        ok, msg = registry.validate_compatibility("producer", "consumer")
        assert ok is True
        ok, msg = registry.validate_compatibility("producer", "incompatible")
        assert ok is False

    def test_health_check(self, registry: PluginRegistry) -> None:
        registry.register(_FakePlugin("a"))
        registry.register(_FakePlugin("b"))
        health = registry.check_health()
        assert health["a"] is True
        assert health["b"] is True


# ---------------------------------------------------------------------------
# API schema tests
# ---------------------------------------------------------------------------


class TestPluginSchemas:
    def test_plugin_response(self) -> None:
        from app.api.v1.schemas.plugins import PluginResponse
        resp = PluginResponse(
            name="nmap",
            description="Port scanner",
            category="scanning",
            version="1.0.0",
            tags=["ports"],
        )
        assert resp.name == "nmap"

    def test_plugin_capability_response(self) -> None:
        from app.api.v1.schemas.plugins import PluginCapabilityResponse
        resp = PluginCapabilityResponse(
            input_asset_types=["host"],
            output_asset_types=["port"],
            produces_findings=True,
            requires_host=True,
            requires_open_ports=False,
            max_targets=1,
        )
        assert resp.produces_findings is True

    def test_plugin_health_check_response(self) -> None:
        from app.api.v1.schemas.plugins import PluginHealthCheckResponse
        resp = PluginHealthCheckResponse(
            healthy=["nmap", "ping"],
            unhealthy=["nuclei"],
            total=3,
        )
        assert resp.total == 3

    def test_workflow_compatibility_response(self) -> None:
        from app.api.v1.schemas.plugins import WorkflowCompatibilityResponse
        resp = WorkflowCompatibilityResponse(
            upstream="nmap",
            downstream="nikto",
            is_compatible=True,
            reason="compatible",
        )
        assert resp.is_compatible is True

    def test_workflow_template_response(self) -> None:
        from app.api.v1.schemas.plugins import WorkflowTemplateResponse
        resp = WorkflowTemplateResponse(
            id="full_port_scan",
            name="Full Port Scan",
            description="Comprehensive port scanning",
            version="1.0.0",
            tags=["port-scan"],
            category="reconnaissance",
            target_types=["ip"],
            steps=[],
        )
        assert resp.id == "full_port_scan"


# ---------------------------------------------------------------------------
# Route precedence tests
# ---------------------------------------------------------------------------


class TestPluginRoutePrecedence:
    """Ensure static/prefix routes are not shadowed by /{plugin_name}.

    These tests inspect the FastAPI router's route table directly to
    verify that static paths appear before parameterized paths.
    """

    @pytest.fixture(autouse=True)
    def _load_router(self) -> None:  # noqa: D401
        from app.api.v1.routers.plugins import router as _r
        self.router = _r

    def _get_paths(self) -> list[tuple[str, str]]:
        """Return (path, endpoint_name) for every GET route in order."""
        paths: list[tuple[str, str]] = []
        for route in self.router.routes:
            if hasattr(route, "methods") and "GET" in route.methods:
                paths.append((route.path, route.name))  # type: ignore[union-attr]
        return paths

    def test_health_before_dynamic_plugin(self) -> None:
        """GET /plugins/health must appear before GET /plugins/{plugin_name}."""
        paths = [p for p, _ in self._get_paths() if p.startswith("/plugins")]
        health_idx = next(i for i, p in enumerate(paths) if p == "/plugins/health")
        dynamic_idx = next(i for i, p in enumerate(paths) if p == "/plugins/{plugin_name}")
        assert health_idx < dynamic_idx, (
            f"/plugins/health (idx={health_idx}) must precede "
            f"/plugins/{{plugin_name}} (idx={dynamic_idx})"
        )

    def test_compatible_before_dynamic_plugin(self) -> None:
        """GET /plugins/compatible/{upstream} must appear before /{plugin_name}."""
        paths = [p for p, _ in self._get_paths() if p.startswith("/plugins")]
        compat_idx = next(
            i for i, p in enumerate(paths) if p == "/plugins/compatible/{upstream_name}"
        )
        dynamic_idx = next(i for i, p in enumerate(paths) if p == "/plugins/{plugin_name}")
        assert compat_idx < dynamic_idx

    def test_compatibility_before_dynamic_plugin(self) -> None:
        """GET /plugins/compatibility/{up}/{down} must appear before /{plugin_name}."""
        paths = [p for p, _ in self._get_paths() if p.startswith("/plugins")]
        compat_idx = next(
            i for i, p in enumerate(paths)
            if p == "/plugins/compatibility/{upstream_name}/{downstream_name}"
        )
        dynamic_idx = next(i for i, p in enumerate(paths) if p == "/plugins/{plugin_name}")
        assert compat_idx < dynamic_idx

    def test_category_before_dynamic_plugin(self) -> None:
        """GET /plugins/category/{category} must appear before /{plugin_name}."""
        paths = [p for p, _ in self._get_paths() if p.startswith("/plugins")]
        cat_idx = next(
            i for i, p in enumerate(paths) if p == "/plugins/category/{category}"
        )
        dynamic_idx = next(i for i, p in enumerate(paths) if p == "/plugins/{plugin_name}")
        assert cat_idx < dynamic_idx

    def test_tag_before_dynamic_plugin(self) -> None:
        """GET /plugins/tag/{tag} must appear before /{plugin_name}."""
        paths = [p for p, _ in self._get_paths() if p.startswith("/plugins")]
        tag_idx = next(
            i for i, p in enumerate(paths) if p == "/plugins/tag/{tag}"
        )
        dynamic_idx = next(i for i, p in enumerate(paths) if p == "/plugins/{plugin_name}")
        assert tag_idx < dynamic_idx

    def test_all_static_before_dynamic(self) -> None:
        """Every static /plugins/... path must precede /plugins/{plugin_name}."""
        paths = [p for p, _ in self._get_paths() if p.startswith("/plugins")]
        dynamic_idx = next(i for i, p in enumerate(paths) if p == "/plugins/{plugin_name}")
        static_prefixes = (
            "/plugins/health",
            "/plugins/compatible/",
            "/plugins/compatibility/",
            "/plugins/category/",
            "/plugins/tag/",
        )
        for i, p in enumerate(paths):
            if any(p.startswith(prefix) for prefix in static_prefixes):
                assert i < dynamic_idx, (
                    f"Static route {p} (idx={i}) must precede "
                    f"/plugins/{{plugin_name}} (idx={dynamic_idx})"
                )
