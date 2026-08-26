"""
M7.3 Phase 2 — canonical identity, provenance, enrichment, and the
minimal executor-target compatibility fix (tests A–Q).

No Docker and no network: identity rules, service behavior over fakes,
and the executor wire contract via httpx.MockTransport.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx

import app.plugins.normalizers  # noqa: F401  - registers built-ins
from app.application.asset_service import AssetService
from app.domain.asset_identity import (
    endpoint_identity,
    identity_for_asset,
    normalize_host,
    service_identity,
)
from app.domain.entities import Asset, Finding, ToolResult
from app.domain.value_objects import (
    AssetType,
    FindingStatus,
    Severity,
)
from app.infrastructure.execution.authorized_target_runner import (
    AuthorizedTargetRunner,
)
from app.infrastructure.execution.executor_runner import ExecutorHttpRunner
from tests.fakes import (
    FakeAssetObservationRepository,
    FakeAssetRepository,
    FakeFindingRepository,
)


def _make_tool_result(plugin: str, payload: dict[str, Any]) -> ToolResult:
    return ToolResult(
        id=uuid4(),
        scan_id=uuid4(),
        plugin=plugin,
        target=str(payload.get("target", payload.get("hostname", ""))),
        normalized_payload=payload,
        raw_output_path=None,
        created_at=datetime.now(UTC),
    )


# --------------------------------------------------------------------------- #
# Identity generation (A–D)
# --------------------------------------------------------------------------- #


def test_a_identity_ip_host_normalized():
    assert normalize_host("172.18.0.10") == "172.18.0.10"
    assert identity_for_asset("host", "172.18.0.10") == "172.18.0.10"


def test_b_identity_hostname_lowercased_and_stripped():
    assert normalize_host("JuiceShop.LOCAL.") == "juiceshop.local"
    assert identity_for_asset("subdomain", "App.Example.COM") == "app.example.com"


def test_c_service_identity_independent_of_nmap_guess():
    # Legacy display values with DIFFERENT nmap service guesses collapse
    # to the SAME canonical identity.
    assert (
        identity_for_asset("service", "ppp?://172.18.0.10:3000/tcp")
        == "tcp/172.18.0.10:3000"
    )
    assert (
        identity_for_asset("service", "http://172.18.0.10:3000/tcp")
        == "tcp/172.18.0.10:3000"
    )
    assert service_identity("EXAMPLE.com", 3000, "tcp", "https") == (
        "tcp/example.com:3000/https"
    )


def test_d_url_endpoint_normalization():
    key = endpoint_identity("http://EXAMPLE.com:80/metrics?b=2&a=1#frag")
    assert key == "tcp/example.com:80/http/metrics?a=1&b=2"
    # Default port materialized; fragment dropped; query sorted.
    assert endpoint_identity("https://example.com") == "tcp/example.com:443/https/"
    assert endpoint_identity("not a url") is None


# --------------------------------------------------------------------------- #
# AssetService identity + dedup (E, F, G)
# --------------------------------------------------------------------------- #


def _nmap_payload(target: str, port: int, service: str) -> dict[str, Any]:
    return {
        "target": target,
        "host_up": True,
        "ports": [
            {"port": port, "protocol": "tcp", "state": "open",
             "service": service, "version": ""}
        ],
        "open_port_count": 1,
    }


async def test_e_same_identity_different_display_values_merge():
    repo = FakeAssetRepository()
    svc = AssetService(repo)
    project = uuid4()

    tr1 = _make_tool_result(
        "nmap", _nmap_payload("172.18.0.10", 3000, "ppp?")
    )
    first = await svc.upsert_from_tool_result(project, tr1)
    tr2 = _make_tool_result(
        "nmap", _nmap_payload("172.18.0.10", 3000, "http")
    )
    second = await svc.upsert_from_tool_result(project, tr2)

    host_asset = next(a for a in first if a.asset_type is AssetType.HOST)
    host_again = next(a for a in second if a.asset_type is AssetType.HOST)
    assert host_asset.id == host_again.id  # same value dedupe as before
    assert host_again.identity_key == "172.18.0.10"

    svc_first = next(a for a in first if a.asset_type is AssetType.SERVICE)
    svc_second = next(a for a in second if a.asset_type is AssetType.SERVICE)
    # Display values may differ (nmap's service guess changed), but the
    # canonical identity is identical — that is what Phase 3 merges on.
    assert "ppp?" in svc_first.value
    assert svc_second.identity_key == svc_first.identity_key == (
        "tcp/172.18.0.10:3000"
    )
    assert await repo.get_by_identity(
        first[0].project_id, AssetType.SERVICE, "tcp/172.18.0.10:3000"
    ) is not None


async def test_f_cross_project_same_identity_isolated():
    repo = FakeAssetRepository()
    svc = AssetService(repo)
    p1, p2 = uuid4(), uuid4()

    tr1 = _make_tool_result("nmap", _nmap_payload("10.0.0.5", 80, "http"))
    tr2 = _make_tool_result("nmap", _nmap_payload("10.0.0.5", 80, "http"))
    assets_p1 = await svc.upsert_from_tool_result(p1, tr1)
    assets_p2 = await svc.upsert_from_tool_result(p2, tr2)

    h1 = next(a for a in assets_p1 if a.asset_type is AssetType.HOST)
    h2 = next(a for a in assets_p2 if a.asset_type is AssetType.HOST)
    assert h1.identity_key == h2.identity_key == "10.0.0.5"
    assert h1.id != h2.id  # different projects → different assets


async def test_g_legacy_assets_without_identity_remain_valid():
    repo = FakeAssetRepository()
    legacy = Asset(
        id=uuid4(),
        project_id=uuid4(),
        asset_type=AssetType.HOST,
        value="legacy-host",
        first_seen=datetime.now(UTC),
        last_seen=datetime.now(UTC),
        created_at=datetime.now(UTC),
        identity_key=None,  # pre-migration row
    )
    await repo.add(legacy)

    fetched = await repo.get_by_dedup(
        legacy.project_id, AssetType.HOST, "legacy-host"
    )
    assert fetched is not None and fetched.identity_key is None

    # A new ping observation backfills identity without breaking anything.
    svc = AssetService(repo)
    tr = _make_tool_result(
        "ping", {"host": "legacy-host", "reachable": True}
    )
    updated_assets = await svc.upsert_from_tool_result(legacy.project_id, tr)
    updated = updated_assets[0]
    assert updated.id == legacy.id
    assert updated.identity_key == "legacy-host"

    # update() must never overwrite an existing identity with None.
    updated.identity_key = None
    await repo.update(updated)
    stored = await repo.get_by_id(legacy.id)
    assert stored is not None and stored.identity_key == "legacy-host"


def test_h_migration_backfill_matches_app_rules():
    # The live migration used the mirrored rules; pin the contract here.
    cases = [
        ("host", "172.18.0.4", "172.18.0.4"),
        ("service", "ppp?://172.18.0.9:3000/tcp", "tcp/172.18.0.9:3000"),
        ("technology", "OWASP Juice Shop", "owasp-juice-shop"),
    ]
    for asset_type, value, expected in cases:
        assert identity_for_asset(asset_type, value) == expected


# --------------------------------------------------------------------------- #
# Observations (I, J, K) + enrichment foundation (L)
# --------------------------------------------------------------------------- #


async def test_i_observation_created_per_asset():
    obs_repo = FakeAssetObservationRepository()
    svc = AssetService(FakeAssetRepository(), observation_repository=obs_repo)
    project = uuid4()
    tr = _make_tool_result("nmap", _nmap_payload("10.9.9.9", 443, "https"))

    assets = await svc.upsert_from_tool_result(project, tr)
    assert len(assets) == 2  # host + service
    assert obs_repo.count == 2
    for o in await obs_repo.list_for_asset(assets[0].id):
        assert o.project_id == project
        assert o.scan_id == tr.scan_id
        assert o.plugin == "nmap"


async def test_j_observation_idempotent_on_reprocessing():
    obs_repo = FakeAssetObservationRepository()
    svc = AssetService(FakeAssetRepository(), observation_repository=obs_repo)
    project = uuid4()
    tr = _make_tool_result("ping", {"host": "10.5.5.5", "reachable": True})

    await svc.upsert_from_tool_result(project, tr)
    await svc.upsert_from_tool_result(project, tr)  # same ToolResult again
    assert obs_repo.count == 1


async def test_k_observation_project_scoped():
    obs_repo = FakeAssetObservationRepository()
    svc = AssetService(FakeAssetRepository(), observation_repository=obs_repo)
    p1, p2 = uuid4(), uuid4()

    tr1 = _make_tool_result("ping", {"host": "10.7.7.7", "reachable": False})
    tr2 = _make_tool_result("ping", {"host": "10.8.8.8", "reachable": True})
    a1 = await svc.upsert_from_tool_result(p1, tr1)
    a2 = await svc.upsert_from_tool_result(p2, tr2)

    rows = list(obs_repo._observations.values())  # noqa: SLF001
    assert obs_repo.count == 2
    # Every observation's project matches its asset's project — no leakage.
    by_id = {a.id: a for pair in (a1, a2) for a in pair}
    for o in rows:
        assert o.project_id == by_id[o.asset_id].project_id


async def test_l_finding_enrichment_roundtrip():
    repo = FakeFindingRepository()
    finding = Finding(
        id=uuid4(),
        project_id=uuid4(),
        title="Prometheus Metrics - Detect",
        severity=Severity.MEDIUM,
        status=FindingStatus.OPEN,
        dedup_key="correlated:nuclei:x",
        enrichment={
            "url": "http://172.18.0.10:3000/metrics",
            "matched_path": "/metrics",
            "technologies": ["prometheus"],
            "service_identity": "tcp/172.18.0.10:3000/http",
            "confidence": "exact",
        },
    )
    await repo.add(finding)
    loaded = await repo.get(finding.id)
    assert loaded is not None
    assert loaded.enrichment is not None
    assert loaded.enrichment["confidence"] == "exact"
    assert loaded.enrichment["service_identity"] == "tcp/172.18.0.10:3000/http"


# --------------------------------------------------------------------------- #
# Executor-target compatibility fix (M, N, O, P, Q)
# --------------------------------------------------------------------------- #


class RecordingInnerRunner:
    """Captures what reaches ExecutorHttpRunner.run unchanged."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def run(self, command, *, timeout_seconds, target="", metadata=None):
        self.calls.append(
            {
                "command": list(command),
                "timeout_seconds": timeout_seconds,
                "target": target,
                "metadata": dict(metadata or {}),
            }
        )
        from app.plugins.base import PluginResult

        return PluginResult(success=True, stdout="", stderr="", exit_code=0)


def test_m_authorized_runner_injects_policy_targets_metadata():
    inner = RecordingInnerRunner()
    runner = AuthorizedTargetRunner(inner, ["172.18.0.10"])
    runner.run(["httpx", "-u", "http://172.18.0.10:3000"], timeout_seconds=30,
               target="http://172.18.0.10:3000")
    meta = inner.calls[0]["metadata"]
    assert meta["authorized_policy_targets"] == ["172.18.0.10"]
    # The plugin's URL target passes through untouched.
    assert inner.calls[0]["target"] == "http://172.18.0.10:3000"


def _capturing_executor_runner(seen: dict[str, Any]) -> ExecutorHttpRunner:
    def handler(request: httpx.Request) -> httpx.Response:
        seen["json"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"execution_id": "x", "status": "completed", "exit_code": 0,
                  "stdout": "", "stderr": ""},
            request=request,
        )

    return ExecutorHttpRunner(
        base_url="http://executor:8000",
        image="specter-plugins:local",
        cpu_limit=1.0,
        memory_limit="512m",
        transport=httpx.MockTransport(handler),
    )


def test_n_url_stays_in_command_policy_receives_registered_identity():
    seen: dict[str, Any] = {}
    runner = _capturing_executor_runner(seen)
    wrapped = AuthorizedTargetRunner(runner, ["172.18.0.10"])

    wrapped.run(
        ["httpx", "-silent", "-json", "-u", "http://172.18.0.10:3000"],
        timeout_seconds=30,
        target="http://172.18.0.10:3000",
    )
    payload = seen["json"]
    # Policy targets: ONLY the registered IP identity.
    assert payload["targets"] == ["172.18.0.10"]
    # The URL survives solely inside the tool command.
    assert any("http://172.18.0.10:3000" in str(part) for part in payload["command"])
    assert "http://" not in " ".join(payload["targets"])


def test_o_no_broadening_when_multiple_registered():
    seen: dict[str, Any] = {}
    runner = _capturing_executor_runner(seen)
    AuthorizedTargetRunner(runner, ["172.18.0.10", "10.0.0.0/24"]).run(
        ["whatweb", "http://172.18.0.10:3000"], timeout_seconds=30,
        target="http://172.18.0.10:3000",
    )
    # Exactly the registered set — plugin string never added on top.
    assert set(seen["json"]["targets"]) == {"172.18.0.10", "10.0.0.0/24"}


def test_p_plugin_target_mismatch_never_reaches_policy():
    """A malicious/broken plugin config pointing at 8.8.8.8 cannot widen
    the executor policy beyond registered identities."""
    seen: dict[str, Any] = {}
    runner = _capturing_executor_runner(seen)
    wrapped = AuthorizedTargetRunner(runner, ["172.18.0.10"])

    wrapped.run(["nmap", "8.8.8.8"], timeout_seconds=30, target="8.8.8.8")
    assert seen["json"]["targets"] == ["172.18.0.10"]
    assert "8.8.8.8" not in seen["json"]["targets"]


def test_q_legacy_behavior_without_authorized_metadata():
    seen: dict[str, Any] = {}
    runner = _capturing_executor_runner(seen)
    runner.run(["nmap", "-sV", "10.0.0.5"], timeout_seconds=30, target="10.0.0.5")
    # No metadata key -> byte-for-byte M7.1 behavior.
    assert seen["json"]["targets"] == ["10.0.0.5"]
