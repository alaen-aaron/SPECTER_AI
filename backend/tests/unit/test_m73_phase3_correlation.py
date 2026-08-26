"""
M7.3 Phase 3 — Cross-tool Correlation Tests (A–S).

Verifies the canonical identity → service resolution → technology
correlation → deterministic finding linking → graph projection → planner
context pipeline.  All tests run against fakes (no Postgres required);
order-independence and idempotency are covered.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.correlation_service import CorrelationService
from app.application.graph_projector import GraphProjector
from app.application.graph_service import GraphService
from app.application.planner_service import PlanningContext
from app.domain.asset_identity import service_identity
from app.domain.entities import Asset, Finding, ToolResult
from app.domain.repositories import (
    AssetObservationRepository,
    AssetRepository,
    FindingRepository,
    GraphRepository,
)
from app.domain.value_objects import (
    AssetType,
    FindingStatus,
    GraphEdgeType,
    Severity,
)
from tests.fakes import (
    FakeAssetObservationRepository,
    FakeAssetRepository,
    FakeEvidenceRepository,
    FakeFindingRepository,
    FakeGraphRepository,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pid() -> UUID:
    return uuid4()


def _tr(
    project_id: UUID,
    plugin: str,
    payload: dict[str,
 object],
    scan_id: UUID | None = None,
) -> ToolResult:
    return ToolResult(
        id=uuid4(),
        scan_id=scan_id or uuid4(),
        plugin=plugin,
        target=str(payload.get("target", "")),
        normalized_payload=payload,
        raw_output_path="/dev/null",
        created_at=datetime.now(UTC),
    )


def _svc(
    project_id: UUID,
    host: str,
    port: int,
    transport: str = "tcp",
    scheme: str | None = None,
    display: str | None = None,
) -> Asset:
    identity = service_identity(host, port, transport, scheme)
    now = datetime.now(UTC)
    return Asset(
        id=uuid4(),
        project_id=project_id,
        asset_type=AssetType.SERVICE,
        value=display or identity,
        first_seen=now,
        last_seen=now,
        source_scan_id=None,
        metadata={},
        created_at=now,
        identity_key=identity,
    )


def _build_svc(
    project_id: UUID,
    finding_repo: FindingRepository,
    asset_repo: AssetRepository | None = None,
    obs_repo: AssetObservationRepository | None = None,
    graph_repo: GraphRepository | None = None,
) -> CorrelationService:
    if asset_repo is None:
        asset_repo = FakeAssetRepository()
    if obs_repo is None:
        obs_repo = FakeAssetObservationRepository()
    if graph_repo is None:
        gs = None
    elif isinstance(graph_repo, GraphService):
        gs = graph_repo
    else:
        gs = GraphService(graph_repo)
    return CorrelationService(finding_repo, asset_repo, obs_repo, gs)


# ===========================================================================
# A. Nmap + HTTPX same service
# ===========================================================================
class TestA_NmapHttpxSameService:
    async def test_converges_to_one_service(self) -> None:
        pid = _pid()
        fakes = FakeFindingRepository()
        assets = FakeAssetRepository()
        obs = FakeAssetObservationRepository()
        gr = FakeGraphRepository()
        gs = GraphService(gr)
        svc = _build_svc(pid, fakes, assets, obs, gs)

        nmap_payload = {
            "target": "172.18.0.10",
            "ports": [
                {"port": 3000, "protocol": "tcp", "state": "open", "service": "http"}
            ],
        }
        httpx_payload = {
            "target": "http://172.18.0.10:3000",
            "results": [
                {"url": "http://172.18.0.10:3000", "status_code": 200, "title": "Juice Shop"}
            ],
        }

        await svc.correlate(pid, [_tr(pid, "nmap", nmap_payload), _tr(pid, "httpx", httpx_payload)])

        services = await assets.list_for_project(pid, AssetType.SERVICE, limit=50)
        assert len(services) == 1
        assert services[0].identity_key == "tcp/172.18.0.10:3000"

        hosts = await assets.list_for_project(pid, AssetType.HOST, limit=50)
        assert len(hosts) == 1
        assert hosts[0].identity_key == "172.18.0.10"


# ===========================================================================
# B. Nmap + WhatWeb same service
# ===========================================================================
class TestB_NmapWhatWebSameService:
    async def test_converges_to_one_service(self) -> None:
        pid = _pid()
        fakes = FakeFindingRepository()
        assets = FakeAssetRepository()
        obs = FakeAssetObservationRepository()
        gr = FakeGraphRepository()
        gs = GraphService(gr)
        svc = _build_svc(pid, fakes, assets, obs, gs)

        nmap_payload = {
            "target": "172.18.0.10",
            "ports": [
                {"port": 3000, "protocol": "tcp", "state": "open", "service": "http"}
            ],
        }
        whatweb_payload = {
            "target": "http://172.18.0.10:3000",
            "results": [
                {"url": "http://172.18.0.10:3000", "server": "Node.js", "technologies": ["Node.js"]}
            ],
        }

        await svc.correlate(
            pid, [_tr(pid, "nmap", nmap_payload), _tr(pid, "whatweb", whatweb_payload)]
        )

        services = await assets.list_for_project(pid, AssetType.SERVICE, limit=50)
        assert len(services) == 1
        assert services[0].identity_key == "tcp/172.18.0.10:3000"


# ===========================================================================
# C. HTTPX + Nuclei same service
# ===========================================================================
class TestC_HttpxNucleiSameService:
    async def test_converges_to_one_service(self) -> None:
        pid = _pid()
        fakes = FakeFindingRepository()
        assets = FakeAssetRepository()
        obs = FakeAssetObservationRepository()
        gr = FakeGraphRepository()
        gs = GraphService(gr)
        svc = _build_svc(pid, fakes, assets, obs, gs)

        httpx_payload = {
            "target": "http://172.18.0.10:3000",
            "results": [
                {"url": "http://172.18.0.10:3000", "status_code": 200}
            ],
        }
        nuclei_payload = {
            "target": "http://172.18.0.10:3000",
            "vulnerabilities": [
                {
                    "template_id": "prometheus-metrics",
                    "title": "Prometheus Metrics Exposed",
                    "severity": "info",
                    "matched_url": "http://172.18.0.10:3000/metrics",
                    "description": "Prometheus endpoint found.",
                }
            ],
        }

        await svc.correlate(
            pid, [_tr(pid, "httpx", httpx_payload), _tr(pid, "nuclei", nuclei_payload)]
        )

        services = await assets.list_for_project(pid, AssetType.SERVICE, limit=50)
        assert len(services) == 1
        assert services[0].identity_key == "tcp/172.18.0.10:3000"


# ===========================================================================
# D. Nmap + Nuclei affected service
# ===========================================================================
class TestD_NmapNucleiAffectedService:
    async def test_finding_links_to_service(self) -> None:
        pid = _pid()
        fakes = FakeFindingRepository()
        assets = FakeAssetRepository()
        obs = FakeAssetObservationRepository()
        gr = FakeGraphRepository()
        gs = GraphService(gr)
        svc = _build_svc(pid, fakes, assets, obs, gs)

        nmap_payload = {
            "target": "172.18.0.10",
            "ports": [
                {"port": 3000, "protocol": "tcp", "state": "open", "service": "http"}
            ],
        }
        nuclei_payload = {
            "target": "http://172.18.0.10:3000",
            "vulnerabilities": [
                {
                    "template_id": "prometheus-metrics",
                    "title": "Prometheus Metrics Exposed",
                    "severity": "info",
                    "matched_url": "http://172.18.0.10:3000/metrics",
                    "description": "Prometheus endpoint found.",
                }
            ],
        }

        await svc.correlate(
            pid, [_tr(pid, "nmap", nmap_payload), _tr(pid, "nuclei", nuclei_payload)]
        )

        services = await assets.list_for_project(pid, AssetType.SERVICE, limit=50)
        assert len(services) == 1
        svc_id = services[0].id

        findings = await fakes.list_for_project(pid, limit=50)
        web_findings = [f for f in findings if "Prometheus" in f.title]
        assert len(web_findings) == 1
        assert web_findings[0].asset_id == svc_id


# ===========================================================================
# E. Technology deduplication
# ===========================================================================
class TestE_TechnologyDedup:
    async def test_same_tech_from_two_tools_creates_one_asset(self) -> None:
        pid = _pid()
        fakes = FakeFindingRepository()
        assets = FakeAssetRepository()
        obs = FakeAssetObservationRepository()
        svc = _build_svc(pid, fakes, assets, obs)

        httpx_payload = {
            "target": "http://172.18.0.10:3000",
            "results": [
                {
                    "url": "http://172.18.0.10:3000",
                    "status_code": 200,
                    "technologies": [{"name": "Node.js"}],
                }
            ],
        }
        whatweb_payload = {
            "target": "http://172.18.0.10:3000",
            "results": [
                {
                    "url": "http://172.18.0.10:3000",
                    "technologies": ["Node.js"],
                }
            ],
        }

        await svc.correlate(
            pid,
            [_tr(pid, "httpx", httpx_payload), _tr(pid, "whatweb", whatweb_payload)],
        )

        techs = await assets.list_for_project(pid, AssetType.TECHNOLOGY, limit=50)
        assert len(techs) == 1
        assert techs[0].value.lower() == "node.js"


# ===========================================================================
# F. USES edge creation
# ===========================================================================
class TestF_UsesEdgeCreation:
    async def test_uses_edge_between_service_and_technology(self) -> None:
        pid = _pid()
        fakes = FakeFindingRepository()
        assets = FakeAssetRepository()
        obs = FakeAssetObservationRepository()
        gr = FakeGraphRepository()
        gs = GraphService(gr)
        svc = _build_svc(pid, fakes, assets, obs, gs)

        httpx_payload = {
            "target": "http://172.18.0.10:3000",
            "results": [
                {
                    "url": "http://172.18.0.10:3000",
                    "status_code": 200,
                    "technologies": [{"name": "Express"}],
                }
            ],
        }

        await svc.correlate(pid, [_tr(pid, "httpx", httpx_payload)])

        edges = await gs.list_edges(pid, GraphEdgeType.USES)
        assert len(edges) >= 1

        # Verify node types
        svc_assets = await assets.list_for_project(pid, AssetType.SERVICE, limit=10)
        tech_assets = await assets.list_for_project(pid, AssetType.TECHNOLOGY, limit=10)
        assert len(svc_assets) == 1
        assert len(tech_assets) == 1


# ===========================================================================
# G. Finding.asset_id deterministic linking
# ===========================================================================
class TestG_FindingAssetIdDeterministic:
    async def test_nuclei_finding_gets_asset_id(self) -> None:
        pid = _pid()
        fakes = FakeFindingRepository()
        assets = FakeAssetRepository()
        obs = FakeAssetObservationRepository()
        svc = _build_svc(pid, fakes, assets, obs)

        httpx_payload = {
            "target": "http://172.18.0.10:3000",
            "results": [{"url": "http://172.18.0.10:3000", "status_code": 200}],
        }
        nuclei_payload = {
            "target": "http://172.18.0.10:3000",
            "vulnerabilities": [
                {
                    "template_id": "prometheus-metrics",
                    "title": "Prometheus Metrics Exposed",
                    "severity": "info",
                    "matched_url": "http://172.18.0.10:3000/metrics",
                    "description": "Found.",
                }
            ],
        }

        await svc.correlate(
            pid, [_tr(pid, "httpx", httpx_payload), _tr(pid, "nuclei", nuclei_payload)]
        )

        findings = await fakes.list_for_project(pid, limit=50)
        web = [f for f in findings if "Prometheus" in f.title]
        assert len(web) == 1
        assert web[0].asset_id is not None

        svc_assets = await assets.list_for_project(pid, AssetType.SERVICE, limit=10)
        assert svc_assets[0].id == web[0].asset_id


# ===========================================================================
# H. Finding enrichment
# ===========================================================================
class TestH_FindingEnrichment:
    async def test_enrichment_contains_url_and_service_identity(self) -> None:
        pid = _pid()
        fakes = FakeFindingRepository()
        assets = FakeAssetRepository()
        obs = FakeAssetObservationRepository()
        svc = _build_svc(pid, fakes, assets, obs)

        httpx_payload = {
            "target": "http://172.18.0.10:3000",
            "results": [{"url": "http://172.18.0.10:3000", "status_code": 200}],
        }
        nuclei_payload = {
            "target": "http://172.18.0.10:3000",
            "vulnerabilities": [
                {
                    "template_id": "prometheus-metrics",
                    "title": "Prometheus Metrics Exposed",
                    "severity": "info",
                    "matched_url": "http://172.18.0.10:3000/metrics",
                    "description": "Found.",
                }
            ],
        }

        await svc.correlate(
            pid, [_tr(pid, "httpx", httpx_payload), _tr(pid, "nuclei", nuclei_payload)]
        )

        findings = await fakes.list_for_project(pid, limit=50)
        web = [f for f in findings if "Prometheus" in f.title]
        assert len(web) == 1
        e = web[0].enrichment
        assert e is not None
        assert e["url"] == "http://172.18.0.10:3000/metrics"
        assert e["matched_path"] == "/metrics"
        assert e["service_identity"] == "tcp/172.18.0.10:3000"
        assert e["confidence"] == "exact"


# ===========================================================================
# I. Web finding path-sensitive dedup
# ===========================================================================
class TestI_PathSensitiveDedup:
    async def test_different_paths_produce_different_findings(self) -> None:
        pid = _pid()
        fakes = FakeFindingRepository()
        svc = _build_svc(pid, fakes)

        nuclei_1 = {
            "target": "http://172.18.0.10:3000",
            "vulnerabilities": [
                {
                    "template_id": "prometheus-metrics",
                    "title": "Prometheus Metrics Exposed",
                    "severity": "info",
                    "matched_url": "http://172.18.0.10:3000/metrics",
                }
            ],
        }
        nuclei_2 = {
            "target": "http://172.18.0.10:3000",
            "vulnerabilities": [
                {
                    "template_id": "prometheus-metrics",
                    "title": "Prometheus Metrics Exposed",
                    "severity": "info",
                    "matched_url": "http://172.18.0.10:3000/login",
                }
            ],
        }

        await svc.correlate(pid, [_tr(pid, "nuclei", nuclei_1)])
        await svc.correlate(pid, [_tr(pid, "nuclei", nuclei_2)])

        findings = await fakes.list_for_project(pid, limit=50)
        assert len(findings) == 2

        paths = sorted([f.enrichment["matched_path"] for f in findings])  # type: ignore[index]
        assert paths == ["/login", "/metrics"]

    async def test_same_path_deduplicates(self) -> None:
        pid = _pid()
        fakes = FakeFindingRepository()
        svc = _build_svc(pid, fakes)

        nuclei = {
            "target": "http://172.18.0.10:3000",
            "vulnerabilities": [
                {
                    "template_id": "prometheus-metrics",
                    "title": "Prometheus Metrics Exposed",
                    "severity": "info",
                    "matched_url": "http://172.18.0.10:3000/metrics",
                }
            ],
        }

        await svc.correlate(pid, [_tr(pid, "nuclei", nuclei)])
        await svc.correlate(pid, [_tr(pid, "nuclei", nuclei)])

        findings = await fakes.list_for_project(pid, limit=50)
        assert len(findings) == 1


# ===========================================================================
# J. Repeated ToolResult idempotency
# ===========================================================================
class TestJ_RepeatedToolResultIdempotent:
    async def test_same_tool_result_twice_no_duplicates(self) -> None:
        pid = _pid()
        fakes = FakeFindingRepository()
        assets = FakeAssetRepository()
        obs = FakeAssetObservationRepository()
        svc = _build_svc(pid, fakes, assets, obs)

        nmap_payload = {
            "target": "172.18.0.10",
            "ports": [
                {"port": 3000, "protocol": "tcp", "state": "open", "service": "http"}
            ],
        }
        tr = _tr(pid, "nmap", nmap_payload)

        await svc.correlate(pid, [tr])
        await svc.correlate(pid, [tr])

        services = await assets.list_for_project(pid, AssetType.SERVICE, limit=10)
        assert len(services) == 1
        findings = await fakes.list_for_project(pid, limit=50)
        assert len(findings) == 1
        assert len(findings[0].tool_result_ids) == 1


# ===========================================================================
# K. Repeated correlation idempotency
# ===========================================================================
class TestK_RepeatedCorrelationIdempotent:
    async def test_correlate_same_batch_twice_converges(self) -> None:
        pid = _pid()
        fakes = FakeFindingRepository()
        assets = FakeAssetRepository()
        obs = FakeAssetObservationRepository()
        gr = FakeGraphRepository()
        gs = GraphService(gr)
        svc = _build_svc(pid, fakes, assets, obs, gs)

        httpx_payload = {
            "target": "http://172.18.0.10:3000",
            "results": [
                {"url": "http://172.18.0.10:3000", "status_code": 200, "technologies": ["Node.js"]}
            ],
        }
        nuclei_payload = {
            "target": "http://172.18.0.10:3000",
            "vulnerabilities": [
                {
                    "template_id": "prometheus-metrics",
                    "title": "Prometheus Metrics Exposed",
                    "severity": "info",
                    "matched_url": "http://172.18.0.10:3000/metrics",
                }
            ],
        }
        batch = [_tr(pid, "httpx", httpx_payload), _tr(pid, "nuclei", nuclei_payload)]

        await svc.correlate(pid, list(batch))
        counts_before = {
            "svcs": len(await assets.list_for_project(pid, AssetType.SERVICE, limit=50)),
            "techs": len(await assets.list_for_project(pid, AssetType.TECHNOLOGY, limit=50)),
            "findings": len(await fakes.list_for_project(pid, limit=50)),
            "uses_edges": len(await gs.list_edges(pid, GraphEdgeType.USES)),
        }

        await svc.correlate(pid, list(batch))
        counts_after = {
            "svcs": len(await assets.list_for_project(pid, AssetType.SERVICE, limit=50)),
            "techs": len(await assets.list_for_project(pid, AssetType.TECHNOLOGY, limit=50)),
            "findings": len(await fakes.list_for_project(pid, limit=50)),
            "uses_edges": len(await gs.list_edges(pid, GraphEdgeType.USES)),
        }

        assert counts_before == counts_after


# ===========================================================================
# L. Order-independent correlation
# ===========================================================================
class TestL_OrderIndependent:
    async def test_httpx_then_nmap_converges(self) -> None:
        pid = _pid()
        fakes = FakeFindingRepository()
        assets = FakeAssetRepository()
        svc = _build_svc(pid, fakes, assets)

        httpx_payload = {
            "target": "http://172.18.0.10:3000",
            "results": [{"url": "http://172.18.0.10:3000", "status_code": 200}],
        }
        nmap_payload = {
            "target": "172.18.0.10",
            "ports": [
                {"port": 3000, "protocol": "tcp", "state": "open", "service": "http"}
            ],
        }

        await svc.correlate(pid, [_tr(pid, "httpx", httpx_payload), _tr(pid, "nmap", nmap_payload)])

        services = await assets.list_for_project(pid, AssetType.SERVICE, limit=10)
        assert len(services) == 1
        assert services[0].identity_key == "tcp/172.18.0.10:3000"

    async def test_nmap_then_httpx_converges(self) -> None:
        pid = _pid()
        fakes = FakeFindingRepository()
        assets = FakeAssetRepository()
        svc = _build_svc(pid, fakes, assets)

        nmap_payload = {
            "target": "172.18.0.10",
            "ports": [
                {"port": 3000, "protocol": "tcp", "state": "open", "service": "http"}
            ],
        }
        httpx_payload = {
            "target": "http://172.18.0.10:3000",
            "results": [{"url": "http://172.18.0.10:3000", "status_code": 200}],
        }

        await svc.correlate(pid, [_tr(pid, "nmap", nmap_payload), _tr(pid, "httpx", httpx_payload)])

        services = await assets.list_for_project(pid, AssetType.SERVICE, limit=10)
        assert len(services) == 1
        assert services[0].identity_key == "tcp/172.18.0.10:3000"

    async def test_nuclei_then_httpx_then_nmap_converges(self) -> None:
        pid = _pid()
        fakes = FakeFindingRepository()
        assets = FakeAssetRepository()
        obs = FakeAssetObservationRepository()
        svc = _build_svc(pid, fakes, assets, obs)

        nuclei_payload = {
            "target": "http://172.18.0.10:3000",
            "vulnerabilities": [
                {
                    "template_id": "prometheus-metrics",
                    "title": "Prometheus",
                    "severity": "info",
                    "matched_url": "http://172.18.0.10:3000/metrics",
                }
            ],
        }
        httpx_payload = {
            "target": "http://172.18.0.10:3000",
            "results": [
                {"url": "http://172.18.0.10:3000", "status_code": 200, "technologies": ["Node.js"]}
            ],
        }
        nmap_payload = {
            "target": "172.18.0.10",
            "ports": [
                {"port": 3000, "protocol": "tcp", "state": "open", "service": "http"}
            ],
        }

        # nuclei first (no pre-existing service!)
        await svc.correlate(
            pid,
            [
                _tr(pid, "nuclei", nuclei_payload),
                _tr(pid, "httpx", httpx_payload),
                _tr(pid, "nmap", nmap_payload),
            ],
        )

        services = await assets.list_for_project(pid, AssetType.SERVICE, limit=10)
        assert len(services) == 1
        assert services[0].identity_key == "tcp/172.18.0.10:3000"
        techs = await assets.list_for_project(pid, AssetType.TECHNOLOGY, limit=10)
        assert len(techs) >= 1

        findings = await fakes.list_for_project(pid, limit=50)
        prom = [f for f in findings if "Prometheus" in f.title]
        assert len(prom) == 1
        assert prom[0].asset_id == services[0].id


# ===========================================================================
# M. Malformed observation handling
# ===========================================================================
class TestM_MalformedObservation:
    async def test_invalid_url_in_httpx_does_not_crash(self) -> None:
        pid = _pid()
        fakes = FakeFindingRepository()
        assets = FakeAssetRepository()
        obs = FakeAssetObservationRepository()
        svc = _build_svc(pid, fakes, assets, obs)

        bad_payload = {
            "target": "not-a-url",
            "results": [
                {"url": "", "status_code": 200},
                {"url": "ftp://invalid", "status_code": 200},
            ],
        }
        # Should not raise
        await svc.correlate(pid, [_tr(pid, "httpx", bad_payload)])

        # No services created from malformed data
        services = await assets.list_for_project(pid, AssetType.SERVICE, limit=10)
        assert len(services) == 0


# ===========================================================================
# N. Provenance preservation
# ===========================================================================
class TestN_ProvenancePreservation:
    async def test_observations_trace_back_to_tool_results(self) -> None:
        pid = _pid()
        fakes = FakeFindingRepository()
        assets = FakeAssetRepository()
        obs = FakeAssetObservationRepository()
        svc = _build_svc(pid, fakes, assets, obs)

        nmap_payload = {
            "target": "172.18.0.10",
            "ports": [
                {"port": 3000, "protocol": "tcp", "state": "open", "service": "http"}
            ],
        }
        httpx_payload = {
            "target": "http://172.18.0.10:3000",
            "results": [{"url": "http://172.18.0.10:3000", "status_code": 200}],
        }

        tr_nmap = _tr(pid, "nmap", nmap_payload)
        tr_httpx = _tr(pid, "httpx", httpx_payload)
        await svc.correlate(pid, [tr_nmap, tr_httpx])

        services = await assets.list_for_project(pid, AssetType.SERVICE, limit=10)
        assert len(services) == 1
        svc_obs = await obs.list_for_asset(services[0].id, limit=100)
        assert len(svc_obs) >= 2
        plugins = {o.plugin for o in svc_obs}
        assert "nmap" in plugins
        assert "httpx" in plugins


# ===========================================================================
# O. Project isolation
# ===========================================================================
class TestO_ProjectIsolation:
    async def test_correlation_is_scoped_to_project(self) -> None:
        pid1 = _pid()
        pid2 = _pid()
        fakes = FakeFindingRepository()
        assets = FakeAssetRepository()
        obs = FakeAssetObservationRepository()
        svc = _build_svc(pid1, fakes, assets, obs)

        nmap_payload = {
            "target": "172.18.0.10",
            "ports": [
                {"port": 3000, "protocol": "tcp", "state": "open", "service": "http"}
            ],
        }
        httpx_payload = {
            "target": "http://172.18.0.10:3000",
            "results": [{"url": "http://172.18.0.10:3000", "status_code": 200}],
        }

        await svc.correlate(pid1, [_tr(pid1, "nmap", nmap_payload)])
        await svc.correlate(pid2, [_tr(pid2, "httpx", httpx_payload)])

        svc1 = await assets.list_for_project(pid1, AssetType.SERVICE, limit=10)
        svc2 = await assets.list_for_project(pid2, AssetType.SERVICE, limit=10)
        assert len(svc1) == 1
        assert len(svc2) == 1
        assert svc1[0].project_id == pid1
        assert svc2[0].project_id == pid2


# ===========================================================================
# P. Correlation failure does not fail scan
# ===========================================================================
class TestP_CorrelationFailureIsolation:
    async def test_broken_asset_repo_does_not_fail_correlate(self) -> None:
        pid = _pid()

        class BrokenAssetRepo:
            async def get_by_identity(self, *a: object, **kw: object) -> None:
                raise RuntimeError("db is down")

            async def upsert(self, *a: object, **kw: object) -> None:
                raise RuntimeError("db is down")

            async def list_for_project(self, *a: object, **kw: object) -> list[object]:
                return []

        fakes = FakeFindingRepository()
        svc = CorrelationService(fakes, BrokenAssetRepo(), None, None)  # type: ignore[arg-type]

        nmap_payload = {
            "target": "172.18.0.10",
            "ports": [
                {"port": 3000, "protocol": "tcp", "state": "open", "service": "http"}
            ],
        }
        # Must not raise
        await svc.correlate(pid, [_tr(pid, "nmap", nmap_payload)])

        # Findings still processed (nmap findings don't depend on asset repo)
        findings = await fakes.list_for_project(pid, limit=10)
        assert len(findings) == 1


# ===========================================================================
# Q. Graph rebuild produces same graph
# ===========================================================================
class TestQ_GraphRebuild:
    async def test_rebuild_recreates_nodes_and_edges(self) -> None:
        pid = _pid()
        fakes = FakeFindingRepository()
        assets = FakeAssetRepository()
        gr = FakeGraphRepository()
        evidence = FakeEvidenceRepository()

        # Create a service asset with identity
        svc_asset = _svc(pid, "172.18.0.10", 3000, "tcp", "http")
        await assets.upsert(svc_asset)

        # Create a technology asset
        tech_now = datetime.now(UTC)
        tech_asset = Asset(
            id=uuid4(),
            project_id=pid,
            asset_type=AssetType.TECHNOLOGY,
            value="Node.js",
            first_seen=tech_now,
            last_seen=tech_now,
            source_scan_id=None,
            metadata={"service_identity": svc_asset.identity_key},
            created_at=tech_now,
            identity_key=f"{svc_asset.identity_key}#node-js",
        )
        await assets.upsert(tech_asset)

        # Create a finding linked to service
        finding = Finding(
            id=uuid4(),
            project_id=pid,
            title="Test Finding",
            severity=Severity.INFO,
            status=FindingStatus.OPEN,
            description="desc",
            dedup_key="test:key",
            tool_result_ids=[],
            created_at=tech_now,
            asset_id=svc_asset.id,
        )
        await fakes.add(finding)

        projector = GraphProjector(gr, assets, fakes, evidence)
        result = await projector.rebuild_graph_from_scratch(pid)

        assert result["nodes"] >= 3  # host + svc + tech + finding

        # USES edge exists
        uses_edges = await gr.list_edges_for_project(pid, GraphEdgeType.USES)
        assert len(uses_edges) >= 1

        # VULNERABLE_TO edge exists
        vuln_edges = await gr.list_edges_for_project(pid, GraphEdgeType.VULNERABLE_TO)
        assert len(vuln_edges) >= 1


# ===========================================================================
# R. Planner context contains correlated services
# ===========================================================================
class TestR_PlannerContext:
    async def test_summary_includes_services(self) -> None:
        pid = _pid()
        tech_now = datetime.now(UTC)
        svc_asset = _svc(pid, "172.18.0.10", 3000, "tcp", "http")
        tech_asset = Asset(
            id=uuid4(),
            project_id=pid,
            asset_type=AssetType.TECHNOLOGY,
            value="Node.js",
            first_seen=tech_now,
            last_seen=tech_now,
            source_scan_id=None,
            metadata={"service_identity": svc_asset.identity_key},
            created_at=tech_now,
            identity_key=f"{svc_asset.identity_key}#node-js",
        )
        finding = Finding(
            id=uuid4(),
            project_id=pid,
            title="Test",
            severity=Severity.HIGH,
            status=FindingStatus.OPEN,
            description="desc",
            dedup_key="k",
            tool_result_ids=[],
            created_at=tech_now,
            asset_id=svc_asset.id,
        )

        ctx = PlanningContext(
            project_id=pid,
            organization_id=None,
            targets=(),
            assets=(svc_asset, tech_asset),
            findings=(finding,),
            recent_actions=(),
        )
        s = ctx.summary()
        assert s["service_count"] == 1
        assert s["technology_count"] == 1
        assert len(s["services"]) == 1
        assert s["services"][0]["identity"] == svc_asset.identity_key
        assert "Node.js" in s["services"][0]["technologies"]


# ===========================================================================
# S. Planner does not receive cross-project data
# ===========================================================================
class TestS_PlannerIsolation:
    async def test_summary_uses_only_project_assets(self) -> None:
        pid1 = _pid()

        svc1 = _svc(pid1, "10.0.0.1", 80, "tcp", "http")

        ctx = PlanningContext(
            project_id=pid1,
            organization_id=None,
            targets=(),
            assets=(svc1,),  # only project1 assets
            findings=(),
            recent_actions=(),
        )
        s = ctx.summary()
        assert s["service_count"] == 1
        assert s["services"][0]["identity"] == svc1.identity_key
        assert all(
            a.project_id == pid1 for a in ctx.assets
        )
