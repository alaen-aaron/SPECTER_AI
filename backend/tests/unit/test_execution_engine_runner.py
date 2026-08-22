"""
ExecutionEngine + M7.1 runner integration tests.

Proves the full M6 pipeline (scope guard → plugin dispatch → normalize →
ToolResult → asset → finding → graph) runs through an active `CommandRunner`
instead of the in-process subprocess path, and that Scope Guard rejection
prevents ANY runner invocation. Uses a recording fake runner — no Docker.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from app.application.asset_service import AssetService
from app.application.correlation_service import CorrelationService
from app.application.graph_service import GraphService
from app.application.scope_guard_service import ScopeGuardService
from app.domain.entities import (
    AuthorizationRecord,
    Project,
    Scan,
    Target,
)
from app.domain.value_objects import (
    AuthorizationStatus,
    ProjectState,
    ScanStatus,
    TargetType,
)
from app.infrastructure.execution.engine import ExecutionEngine
from app.infrastructure.storage.local_artifact_store import LocalArtifactStore
from app.plugins.base import PluginResult
from app.plugins.manager import PluginManager
from app.plugins.nmap_plugin import NmapPlugin
from app.plugins.normalizer_registry import NormalizerRegistry
from app.plugins.normalizers.nmap_normalizer import NmapNormalizer
from app.plugins.registry import PluginRegistry
from tests.fakes import (
    FakeAssetRepository,
    FakeAuditLogRepository,
    FakeAuthorizationRecordRepository,
    FakeFindingRepository,
    FakeGraphRepository,
    FakeProjectRepository,
    FakeScanRepository,
    FakeTargetRepository,
    FakeToolResultRepository,
)

NMAP_STDOUT = """Starting Nmap 7.94 ( https://nmap.org ) at test
Nmap scan report for 10.0.0.5
Host is up (0.0012s latency).
PORT      STATE    SERVICE    VERSION
22/tcp    open     ssh        OpenSSH 8.9p1 Ubuntu
80/tcp    open     http       nginx 1.18.0
443/tcp   filtered https
Nmap done: 1 IP address (1 host up) scanned in 2.00 seconds
"""


class RecordingRunner:
    def __init__(self, result: PluginResult | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._result = result or PluginResult(
            success=True,
            stdout=NMAP_STDOUT,
            stderr="",
            exit_code=0,
            metadata={"via": "runner"},
        )

    def run(
        self,
        command: list[str],
        *,
        timeout_seconds: int,
        target: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> PluginResult:
        self.calls.append(
            {
                "command": command,
                "timeout_seconds": timeout_seconds,
                "target": target,
                "metadata": metadata,
            }
        )
        return self._result


def _make_project(state: ProjectState = ProjectState.ACTIVE) -> Project:
    now = datetime.now(UTC)
    return Project(
        id=uuid4(),
        organization_id=uuid4(),
        name="Test Project",
        description=None,
        state=state,
        tags=[],
        client_metadata={},
        created_at=now,
        updated_at=now,
    )


def _make_target(project_id, value: str = "10.0.0.5") -> Target:
    now = datetime.now(UTC)
    return Target(
        id=uuid4(),
        project_id=project_id,
        value=value,
        target_type=TargetType.IP,
        in_scope=True,
        created_at=now,
        updated_at=now,
    )


def _make_record(project_id, allowed_targets: list[str]) -> AuthorizationRecord:
    today = date.today()
    return AuthorizationRecord(
        id=uuid4(),
        project_id=project_id,
        client_name="Acme",
        document_reference="doc.pdf",
        authorized_from=today - timedelta(days=1),
        authorized_to=today + timedelta(days=30),
        allowed_targets=allowed_targets,
        approved_by=uuid4(),
        status=AuthorizationStatus.ACTIVE,
        scope_notes=None,
        evidence_pointer=None,
        created_at=datetime.now(UTC),
    )


def _make_scan(project_id, target_ids, plugin="nmap", plugin_config=None) -> Scan:
    return Scan(
        id=uuid4(),
        project_id=project_id,
        initiated_by=uuid4(),
        plugin=plugin,
        status=ScanStatus.QUEUED,
        target_ids=target_ids,
        plugin_config=plugin_config
        or {"target": "10.0.0.5", "ports": "1-1000", "arguments": ["-sV"]},
        created_at=datetime.now(UTC),
    )


def _build_engine(repos, tmp_path: Path, runner):
    normalizers = NormalizerRegistry()
    normalizers.register(NmapNormalizer())

    registry = PluginRegistry()
    registry.register(NmapPlugin())

    scope_guard = ScopeGuardService(
        project_repository=repos["projects"],
        target_repository=repos["targets"],
        authorization_repository=repos["authorizations"],
    )
    graph_service = GraphService(repos["graph"])
    asset_service = AssetService(repos["assets"], graph_service)
    correlation = CorrelationService(repos["findings"])

    return ExecutionEngine(
        scan_repository=repos["scans"],
        scope_guard=scope_guard,
        plugin_manager=PluginManager(registry),
        artifact_store=LocalArtifactStore(str(tmp_path)),
        audit_log_repository=repos["audit"],
        tool_result_repository=repos["tool_results"],
        normalizer_registry=normalizers,
        default_timeout_seconds=30,
        correlation_service=correlation,
        asset_service=asset_service,
        graph_service=graph_service,
        runner=runner,
    )


@pytest.fixture
def repos():
    return {
        "scans": FakeScanRepository(),
        "projects": FakeProjectRepository(),
        "targets": FakeTargetRepository(),
        "authorizations": FakeAuthorizationRecordRepository(),
        "audit": FakeAuditLogRepository(),
        "tool_results": FakeToolResultRepository(),
        "assets": FakeAssetRepository(),
        "findings": FakeFindingRepository(),
        "graph": FakeGraphRepository(),
    }


@pytest.mark.asyncio
async def test_engine_dispatches_nmap_to_runner_and_runs_full_pipeline(repos, tmp_path):
    project = _make_project()
    target = _make_target(project.id)
    record = _make_record(project.id, allowed_targets=[target.value])
    scan = _make_scan(project.id, [target.id])

    await repos["projects"].add(project)
    await repos["targets"].add(target)
    await repos["authorizations"].add(record)
    await repos["scans"].create(scan)

    runner = RecordingRunner()
    engine = _build_engine(repos, tmp_path, runner)
    await engine.run(scan.id)

    # Scope Guard passed → the runner was invoked with the plugin's command.
    assert len(runner.calls) == 1
    command = runner.calls[0]["command"]
    assert command[0] == "nmap"
    assert "10.0.0.5" in command
    assert runner.calls[0]["target"] == "10.0.0.5"

    final = await repos["scans"].get(scan.id)
    assert final.status is ScanStatus.COMPLETED
    assert final.exit_code == 0

    # ToolResult persisted with the normalizer's structured payload.
    tool_results = await repos["tool_results"].list_for_scan(scan.id)
    assert len(tool_results) == 1
    payload = tool_results[0].normalized_payload
    assert payload["host_up"] is True
    assert payload["open_port_count"] == 2
    assert {p["port"] for p in payload["ports"]} == {22, 80, 443}

    # Pipeline ran on the runner's output: assets + findings were created.
    assert len(repos["assets"]._assets) > 0
    assert len(repos["findings"]._findings) > 0
    assert len(repos["graph"]._nodes) > 0

    # Raw output was written to the shared artifact store as usual.
    assert final.logs_path is not None
    assert NMAP_STDOUT in (Path(final.logs_path) / "stdout.log").read_text()


@pytest.mark.asyncio
async def test_scope_guard_rejection_prevents_any_runner_invocation(repos, tmp_path):
    project = _make_project()
    target = _make_target(project.id)
    scan = _make_scan(project.id, [target.id])

    await repos["projects"].add(project)
    await repos["targets"].add(target)
    await repos["scans"].create(scan)
    # Deliberately no authorization record → Scope Guard must reject at runtime.

    runner = RecordingRunner()
    engine = _build_engine(repos, tmp_path, runner)
    await engine.run(scan.id)

    assert runner.calls == [], "plugin must never be dispatched when Scope Guard rejects"
    final = await repos["scans"].get(scan.id)
    assert final.status is ScanStatus.FAILED
    assert "Scope Guard rejected" in (final.error_message or "")


@pytest.mark.asyncio
async def test_runner_failure_result_fails_the_scan(repos, tmp_path):
    project = _make_project()
    target = _make_target(project.id)
    record = _make_record(project.id, allowed_targets=[target.value])
    scan = _make_scan(project.id, [target.id])

    await repos["projects"].add(project)
    await repos["targets"].add(target)
    await repos["authorizations"].add(record)
    await repos["scans"].create(scan)

    runner = RecordingRunner(
        PluginResult(
            success=False,
            stdout="partial",
            stderr="nmap: failed to connect",
            exit_code=1,
        )
    )
    engine = _build_engine(repos, tmp_path, runner)
    await engine.run(scan.id)

    final = await repos["scans"].get(scan.id)
    assert final.status is ScanStatus.FAILED
    assert "nmap: failed to connect" in (final.error_message or "")


@pytest.mark.asyncio
async def test_runner_timeout_result_fails_scan_with_exit_code_none(repos, tmp_path):
    project = _make_project()
    target = _make_target(project.id)
    record = _make_record(project.id, allowed_targets=[target.value])
    scan = _make_scan(project.id, [target.id])

    await repos["projects"].add(project)
    await repos["targets"].add(target)
    await repos["authorizations"].add(record)
    await repos["scans"].create(scan)

    runner = RecordingRunner(
        PluginResult(
            success=False,
            stdout="",
            stderr="Plugin execution timed out after 30s",
            exit_code=None,
        )
    )
    engine = _build_engine(repos, tmp_path, runner)
    await engine.run(scan.id)

    final = await repos["scans"].get(scan.id)
    assert final.status is ScanStatus.FAILED
    assert final.exit_code is None
    assert "timed out" in (final.error_message or "")