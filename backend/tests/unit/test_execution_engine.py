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
from app.domain.value_objects import AuthorizationStatus, ProjectState, ScanStatus, TargetType
from app.infrastructure.execution.engine import ExecutionEngine
from app.infrastructure.storage.local_artifact_store import LocalArtifactStore
from app.plugins.echo_plugin import EchoPlugin
from app.plugins.manager import PluginManager
from app.plugins.registry import PluginRegistry
from tests.fakes import (
    FakeAuditLogRepository,
    FakeAuthorizationRecordRepository,
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
