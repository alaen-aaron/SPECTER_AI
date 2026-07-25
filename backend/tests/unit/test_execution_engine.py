"""
Unit tests for `ExecutionEngine` (Milestone 3).

Uses in-memory fakes for repositories and a real `LocalArtifactStore`
pointed at a pytest `tmp_path` (cheap, deterministic, no mocking of
filesystem calls needed). Exercises: successful completion, plugin
failure, defense-in-depth Scope Guard re-validation at execution time,
and the "already cancelled before the worker picked it up" skip path.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from app.application.scope_guard_service import ScopeGuardService
from app.domain.entities import AuthorizationRecord, Project, Scan, Target
from app.domain.value_objects import (
    AuthorizationStatus,
    ProjectState,
    ScanStatus,
    Severity,
    TargetType,
)
from app.infrastructure.execution.engine import ExecutionEngine
from app.infrastructure.storage.local_artifact_store import LocalArtifactStore
from app.plugins.base import Plugin, PluginResult
from app.plugins.echo_plugin import EchoPlugin
from app.plugins.manager import PluginManager
from app.plugins.registry import PluginRegistry
from tests.fakes import (
    FakeAssetRepository,
    FakeAuditLogRepository,
    FakeAuthorizationRecordRepository,
    FakeFindingRepository,
    FakeProjectRepository,
    FakeScanRepository,
    FakeTargetRepository,
    FakeToolResultRepository,
)


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


def _make_scan(project_id, target_ids, plugin="echo", plugin_config=None) -> Scan:
    return Scan(
        id=uuid4(),
        project_id=project_id,
        initiated_by=uuid4(),
        plugin=plugin,
        status=ScanStatus.QUEUED,
        target_ids=target_ids,
        plugin_config=plugin_config or {},
        created_at=datetime.now(UTC),
    )


@pytest.fixture
def registry() -> PluginRegistry:
    reg = PluginRegistry()
    reg.register(EchoPlugin())
    return reg


@pytest.fixture
def repos():
    return {
        "scans": FakeScanRepository(),
        "projects": FakeProjectRepository(),
        "targets": FakeTargetRepository(),
        "authorizations": FakeAuthorizationRecordRepository(),
        "audit": FakeAuditLogRepository(),
        "tool_results": FakeToolResultRepository(),
    }


def _make_engine(repos, registry: PluginRegistry, tmp_path: Path) -> ExecutionEngine:
    from app.plugins.normalizer_registry import NormalizerRegistry

    scope_guard = ScopeGuardService(
        project_repository=repos["projects"],
        target_repository=repos["targets"],
        authorization_repository=repos["authorizations"],
    )
    return ExecutionEngine(
        scan_repository=repos["scans"],
        scope_guard=scope_guard,
        plugin_manager=PluginManager(registry),
        artifact_store=LocalArtifactStore(str(tmp_path)),
        audit_log_repository=repos["audit"],
        tool_result_repository=repos["tool_results"],
        normalizer_registry=NormalizerRegistry(),
        default_timeout_seconds=10,
    )


def _make_engine_with_correlation(
    repos, registry: PluginRegistry, tmp_path: Path, correlation_service
) -> ExecutionEngine:
    from app.plugins.normalizer_registry import NormalizerRegistry

    scope_guard = ScopeGuardService(
        project_repository=repos["projects"],
        target_repository=repos["targets"],
        authorization_repository=repos["authorizations"],
    )
    return ExecutionEngine(
        scan_repository=repos["scans"],
        scope_guard=scope_guard,
        plugin_manager=PluginManager(registry),
        artifact_store=LocalArtifactStore(str(tmp_path)),
        audit_log_repository=repos["audit"],
        tool_result_repository=repos["tool_results"],
        normalizer_registry=NormalizerRegistry(),
        default_timeout_seconds=10,
        correlation_service=correlation_service,
    )


@pytest.mark.asyncio
async def test_successful_scan_completes_and_writes_logs(repos, registry, tmp_path):
    project = _make_project()
    target = _make_target(project.id)
    record = _make_record(project.id, allowed_targets=[target.value])
    scan = _make_scan(project.id, [target.id])

    await repos["projects"].add(project)
    await repos["targets"].add(target)
    await repos["authorizations"].add(record)
    await repos["scans"].create(scan)

    engine = _make_engine(repos, registry, tmp_path)
    await engine.run(scan.id)

    final = await repos["scans"].get(scan.id)
    assert final.status is ScanStatus.COMPLETED
    assert final.exit_code == 0
    assert final.logs_path is not None
    assert (Path(final.logs_path) / "stdout.log").read_text() == "Hello from SPECTER"


@pytest.mark.asyncio
async def test_execution_writes_audit_entries_for_start_and_completion(repos, registry, tmp_path):
    project = _make_project()
    target = _make_target(project.id)
    record = _make_record(project.id, allowed_targets=[])
    scan = _make_scan(project.id, [target.id])

    await repos["projects"].add(project)
    await repos["targets"].add(target)
    await repos["authorizations"].add(record)
    await repos["scans"].create(scan)

    engine = _make_engine(repos, registry, tmp_path)
    await engine.run(scan.id)

    actions = [e.action for e in repos["audit"]._entries]
    assert "scan.started" in actions
    assert "scan.completed" in actions


@pytest.mark.asyncio
async def test_scope_guard_rejection_at_execution_time_fails_the_scan(repos, registry, tmp_path):
    """
    Defense-in-depth: even though ScanService already validated scope at
    enqueue time, ExecutionEngine re-validates immediately before
    execution. Simulates the authorization record having been revoked
    in between by NOT adding it at all before running the engine.
    """
    project = _make_project()
    target = _make_target(project.id)
    scan = _make_scan(project.id, [target.id])

    await repos["projects"].add(project)
    await repos["targets"].add(target)
    await repos["scans"].create(scan)
    # Deliberately no authorization record added.

    engine = _make_engine(repos, registry, tmp_path)
    await engine.run(scan.id)

    final = await repos["scans"].get(scan.id)
    assert final.status is ScanStatus.FAILED
    assert "Scope Guard rejected" in (final.error_message or "")


@pytest.mark.asyncio
async def test_plugin_failure_marks_scan_failed(repos, tmp_path):
    project = _make_project()
    target = _make_target(project.id)
    record = _make_record(project.id, allowed_targets=[])
    scan = _make_scan(
        project.id, [target.id], plugin="ping", plugin_config={"hostname": "127.0.0.1"}
    )

    await repos["projects"].add(project)
    await repos["targets"].add(target)
    await repos["authorizations"].add(record)
    await repos["scans"].create(scan)

    # A registry where "ping" is registered but the binary can't be found —
    # simulated by registering a plugin whose execute() reports failure.
    from app.plugins.base import Plugin, PluginResult

    class _AlwaysFailsPlugin(Plugin):
        def name(self) -> str:
            return "ping"

        def description(self) -> str:
            return "fails on purpose"

        def validate_config(self, config: dict) -> None:  # noqa: ANN001
            return None

        def execute(self, config: dict, timeout_seconds: int) -> PluginResult:  # noqa: ANN001
            return PluginResult(success=False, stdout="", stderr="simulated failure", exit_code=1)

    registry = PluginRegistry()
    registry.register(_AlwaysFailsPlugin())

    engine = _make_engine(repos, registry, tmp_path)
    await engine.run(scan.id)

    final = await repos["scans"].get(scan.id)
    assert final.status is ScanStatus.FAILED
    assert final.exit_code == 1
    assert final.error_message == "simulated failure"


@pytest.mark.asyncio
async def test_already_cancelled_scan_is_never_executed(repos, registry, tmp_path):
    project = _make_project()
    target = _make_target(project.id)
    record = _make_record(project.id, allowed_targets=[])
    scan = _make_scan(project.id, [target.id])
    scan.status = ScanStatus.CANCELLED

    await repos["projects"].add(project)
    await repos["targets"].add(target)
    await repos["authorizations"].add(record)
    await repos["scans"].create(scan)

    engine = _make_engine(repos, registry, tmp_path)
    await engine.run(scan.id)

    final = await repos["scans"].get(scan.id)
    assert final.status is ScanStatus.CANCELLED  # untouched, never ran
    assert final.logs_path is None


@pytest.mark.asyncio
async def test_missing_scan_is_handled_gracefully(repos, registry, tmp_path):
    """`run()` on a scan_id that doesn't exist must not raise."""
    engine = _make_engine(repos, registry, tmp_path)
    await engine.run(uuid4())  # must not raise


# ---------------------------------------------------------------------------
# E2E regression: Scan → Plugin → ToolResult → Correlation → Finding
# ---------------------------------------------------------------------------


class _NmapEchoPlugin(Plugin):
    """Test plugin that outputs JSON matching the nmap normalizer format."""

    def name(self) -> str:
        return "nmap"

    def description(self) -> str:
        return "nmap echo for pipeline tests"

    def validate_config(self, config: dict) -> None:  # noqa: ANN001
        return None

    def execute(self, config: dict, timeout_seconds: int) -> PluginResult:  # noqa: ANN001
        import json

        output = json.dumps({
            "target": config.get("target", "10.0.0.1"),
            "ports": [
                {
                    "port": 23,
                    "state": "open",
                    "service": "telnet",
                    "protocol": "tcp",
                    "version": "",
                },
            ],
        })
        return PluginResult(success=True, stdout=output, stderr="", exit_code=0)


class _PassthroughNormalizer:
    """Normalizer that parses JSON from stdout as-is."""

    @property
    def plugin_name(self) -> str:
        return "nmap"

    def normalize(self, stdout: str, stderr: str, config: dict) -> dict:  # noqa: ANN001
        import json

        return json.loads(stdout)


@pytest.mark.asyncio
async def test_pipeline_scan_toolresult_correlation_finding(
    repos, tmp_path
):
    """
    E2E regression: a successful scan must automatically produce a
    Finding via the CorrelationService → FindingService pipeline.

    Verifies: Scan completes → ToolResult persisted → Correlation runs
    → Finding created → finding_id is available.
    """
    from app.application.correlation_service import CorrelationService
    from app.application.finding_service import FindingService
    from app.plugins.normalizer_registry import NormalizerRegistry

    # Register the nmap-echo plugin + passthrough normalizer
    reg = PluginRegistry()
    reg.register(_NmapEchoPlugin())

    norm_registry = NormalizerRegistry()
    norm_registry.register(_PassthroughNormalizer())

    finding_repo = FakeFindingRepository()
    correlation = CorrelationService(finding_repo)

    project = _make_project()
    target = _make_target(project.id, value="10.0.0.1")
    record = _make_record(project.id, allowed_targets=[target.value])
    scan = _make_scan(
        project.id,
        [target.id],
        plugin="nmap",
        plugin_config={"target": "10.0.0.1"},
    )

    await repos["projects"].add(project)
    await repos["targets"].add(target)
    await repos["authorizations"].add(record)
    await repos["scans"].create(scan)

    # Build engine with correlation wired in
    scope_guard = ScopeGuardService(
        project_repository=repos["projects"],
        target_repository=repos["targets"],
        authorization_repository=repos["authorizations"],
    )
    engine = ExecutionEngine(
        scan_repository=repos["scans"],
        scope_guard=scope_guard,
        plugin_manager=PluginManager(reg),
        artifact_store=LocalArtifactStore(str(tmp_path)),
        audit_log_repository=repos["audit"],
        tool_result_repository=repos["tool_results"],
        normalizer_registry=norm_registry,
        default_timeout_seconds=10,
        correlation_service=correlation,
    )

    await engine.run(scan.id)

    # 1. Scan completed
    final_scan = await repos["scans"].get(scan.id)
    assert final_scan.status is ScanStatus.COMPLETED

    # 2. ToolResult persisted
    tool_results = await repos["tool_results"].list_for_scan(scan.id)
    assert len(tool_results) == 1
    assert tool_results[0].plugin == "nmap"

    # 3. Finding created automatically by CorrelationService
    findings = await finding_repo.list_for_project(project.id)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.severity is Severity.MEDIUM  # telnet → MEDIUM
    assert "telnet" in finding.title.lower()
    assert finding.project_id == project.id

    # 4. finding_id is valid and retrievable
    fetched = await FindingService(finding_repo, FakeAssetRepository()).get(finding.id)
    assert fetched.id == finding.id


@pytest.mark.asyncio
async def test_pipeline_no_correlation_still_completes(repos, registry, tmp_path):
    """Engine without CorrelationService still works (backward compat)."""
    project = _make_project()
    target = _make_target(project.id)
    record = _make_record(project.id, allowed_targets=[target.value])
    scan = _make_scan(project.id, [target.id])

    await repos["projects"].add(project)
    await repos["targets"].add(target)
    await repos["authorizations"].add(record)
    await repos["scans"].create(scan)

    engine = _make_engine(repos, registry, tmp_path)
    await engine.run(scan.id)

    final = await repos["scans"].get(scan.id)
    assert final.status is ScanStatus.COMPLETED
    # No correlation_service → no findings created
    assert engine._correlation is None


@pytest.mark.asyncio
async def test_pipeline_correlation_failure_does_not_fail_scan(repos, tmp_path):
    """If correlation raises, the scan still completes successfully."""
    from app.application.correlation_service import CorrelationService
    from app.plugins.normalizer_registry import NormalizerRegistry

    reg = PluginRegistry()
    reg.register(_NmapEchoPlugin())

    norm_registry = NormalizerRegistry()
    norm_registry.register(_PassthroughNormalizer())

    class _BrokenFindingRepo:
        """Repository that blows up on add — simulates DB failure in correlation."""

        async def add(self, finding):  # noqa: ANN001
            raise RuntimeError("simulated DB failure")

        async def get(self, finding_id):  # noqa: ANN001
            return None

        async def list_for_project(self, project_id, **kwargs):  # noqa: ANN001
            return []

        async def get_by_dedup_key(self, project_id, dedup_key):  # noqa: ANN001
            return None

        async def update_status(self, finding_id, status):  # noqa: ANN001
            pass

    correlation = CorrelationService(_BrokenFindingRepo())

    project = _make_project()
    target = _make_target(project.id, value="10.0.0.1")
    record = _make_record(project.id, allowed_targets=[target.value])
    scan = _make_scan(
        project.id, [target.id], plugin="nmap",
        plugin_config={"target": "10.0.0.1"},
    )

    await repos["projects"].add(project)
    await repos["targets"].add(target)
    await repos["authorizations"].add(record)
    await repos["scans"].create(scan)

    engine = _make_engine_with_correlation(repos, reg, tmp_path, correlation)
    await engine.run(scan.id)

    # Scan still completes — correlation failure is non-fatal
    final = await repos["scans"].get(scan.id)
    assert final.status is ScanStatus.COMPLETED
