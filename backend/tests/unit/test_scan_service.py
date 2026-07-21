"""
Unit tests for `ScanService` (Milestone 3).

Uses `NullScanTaskDispatcher` (no real Celery/broker involved) and the
same in-memory fakes Milestone 2's tests use for Project/Target/
Authorization repositories — this proves `ScanService.create` really
does route every request through `ScopeGuardService` and plugin config
validation, without needing Postgres or Redis.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest

from app.application.scan_service import NullScanTaskDispatcher, ScanService
from app.application.scope_guard_service import ScopeGuardService
from app.domain.entities import AuthorizationRecord, Project, Target
from app.domain.exceptions import (
    InvalidPluginConfigError,
    OutOfScopeTargetError,
    PluginNotFoundError,
    ProjectNotActiveError,
    ScanNotCancellableError,
    ScanNotFoundError,
)
from app.domain.value_objects import AuthorizationStatus, ProjectState, ScanStatus, TargetType
from app.plugins.echo_plugin import EchoPlugin
from app.plugins.manager import PluginManager
from app.plugins.nmap_plugin import NmapPlugin
from app.plugins.registry import PluginRegistry
from tests.fakes import (
    FakeAuthorizationRecordRepository,
    FakeProjectRepository,
    FakeScanRepository,
    FakeTargetRepository,
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


def _make_authorization_record(project_id, allowed_targets: list[str]) -> AuthorizationRecord:
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


@pytest.fixture
def registry() -> PluginRegistry:
    reg = PluginRegistry()
    reg.register(EchoPlugin())
    reg.register(NmapPlugin())
    return reg


@pytest.fixture
def repos():
    return {
        "scans": FakeScanRepository(),
        "projects": FakeProjectRepository(),
        "targets": FakeTargetRepository(),
        "authorizations": FakeAuthorizationRecordRepository(),
    }


def _make_service(repos, registry: PluginRegistry) -> ScanService:
    scope_guard = ScopeGuardService(
        project_repository=repos["projects"],
        target_repository=repos["targets"],
        authorization_repository=repos["authorizations"],
    )
    return ScanService(
        scan_repository=repos["scans"],
        scope_guard=scope_guard,
        plugin_manager=PluginManager(registry),
        task_dispatcher=NullScanTaskDispatcher(),
    )


@pytest.mark.asyncio
async def test_create_scan_succeeds_for_authorized_target(repos, registry):
    project = _make_project()
    target = _make_target(project.id)
    record = _make_authorization_record(project.id, allowed_targets=[target.value])
    await repos["projects"].add(project)
    await repos["targets"].add(target)
    await repos["authorizations"].add(record)

    service = _make_service(repos, registry)
    scan = await service.create(
        project_id=project.id,
        plugin_name="echo",
        plugin_config={},
        target_ids=[target.id],
        initiated_by=uuid4(),
    )

    assert scan.status is ScanStatus.QUEUED
    assert scan.plugin == "echo"
    persisted = await repos["scans"].get(scan.id)
    assert persisted is not None


@pytest.mark.asyncio
async def test_create_scan_rejects_inactive_project(repos, registry):
    project = _make_project(state=ProjectState.DRAFT)
    target = _make_target(project.id)
    await repos["projects"].add(project)
    await repos["targets"].add(target)

    service = _make_service(repos, registry)
    with pytest.raises(ProjectNotActiveError):
        await service.create(
            project_id=project.id,
            plugin_name="echo",
            plugin_config={},
            target_ids=[target.id],
            initiated_by=uuid4(),
        )
    assert await repos["scans"].list(project.id) == []


@pytest.mark.asyncio
async def test_create_scan_rejects_out_of_scope_target(repos, registry):
    project = _make_project()
    target = _make_target(project.id, "192.168.1.1")
    record = _make_authorization_record(project.id, allowed_targets=["10.0.0.0/8"])
    await repos["projects"].add(project)
    await repos["targets"].add(target)
    await repos["authorizations"].add(record)

    service = _make_service(repos, registry)
    with pytest.raises(OutOfScopeTargetError):
        await service.create(
            project_id=project.id,
            plugin_name="echo",
            plugin_config={},
            target_ids=[target.id],
            initiated_by=uuid4(),
        )
    # No scan row should exist — scope guard rejects before persistence.
    assert await repos["scans"].list(project.id) == []


@pytest.mark.asyncio
async def test_create_scan_rejects_unknown_plugin(repos, registry):
    project = _make_project()
    target = _make_target(project.id)
    record = _make_authorization_record(project.id, allowed_targets=[])
    await repos["projects"].add(project)
    await repos["targets"].add(target)
    await repos["authorizations"].add(record)

    service = _make_service(repos, registry)
    with pytest.raises(PluginNotFoundError):
        await service.create(
            project_id=project.id,
            plugin_name="sqlmap",
            plugin_config={},
            target_ids=[target.id],
            initiated_by=uuid4(),
        )
    assert await repos["scans"].list(project.id) == []


@pytest.mark.asyncio
async def test_create_scan_rejects_invalid_plugin_config_before_persisting(repos, registry):
    project = _make_project()
    target = _make_target(project.id)
    record = _make_authorization_record(project.id, allowed_targets=[])
    await repos["projects"].add(project)
    await repos["targets"].add(target)
    await repos["authorizations"].add(record)

    service = _make_service(repos, registry)
    with pytest.raises(InvalidPluginConfigError):
        await service.create(
            project_id=project.id,
            plugin_name="nmap",
            plugin_config={"target": "10.0.0.5", "ports": "80", "arguments": ["--script=evil"]},
            target_ids=[target.id],
            initiated_by=uuid4(),
        )
    assert await repos["scans"].list(project.id) == []


@pytest.mark.asyncio
async def test_get_scan_raises_for_unknown_id(repos, registry):
    service = _make_service(repos, registry)
    with pytest.raises(ScanNotFoundError):
        await service.get(uuid4())


@pytest.mark.asyncio
async def test_cancel_queued_scan_succeeds(repos, registry):
    project = _make_project()
    target = _make_target(project.id)
    record = _make_authorization_record(project.id, allowed_targets=[])
    await repos["projects"].add(project)
    await repos["targets"].add(target)
    await repos["authorizations"].add(record)

    service = _make_service(repos, registry)
    scan = await service.create(
        project_id=project.id,
        plugin_name="echo",
        plugin_config={},
        target_ids=[target.id],
        initiated_by=uuid4(),
    )
    cancelled = await service.cancel(scan.id)
    assert cancelled.status is ScanStatus.CANCELLED


@pytest.mark.asyncio
async def test_cancel_completed_scan_raises(repos, registry):
    project = _make_project()
    target = _make_target(project.id)
    record = _make_authorization_record(project.id, allowed_targets=[])
    await repos["projects"].add(project)
    await repos["targets"].add(target)
    await repos["authorizations"].add(record)

    service = _make_service(repos, registry)
    scan = await service.create(
        project_id=project.id,
        plugin_name="echo",
        plugin_config={},
        target_ids=[target.id],
        initiated_by=uuid4(),
    )
    await repos["scans"].complete(scan.id, 0, None)

    with pytest.raises(ScanNotCancellableError):
        await service.cancel(scan.id)


@pytest.mark.asyncio
async def test_list_for_project_only_returns_that_projects_scans(repos, registry):
    project_a = _make_project()
    project_b = _make_project()
    target_a = _make_target(project_a.id)
    target_b = _make_target(project_b.id)
    await repos["projects"].add(project_a)
    await repos["projects"].add(project_b)
    await repos["targets"].add(target_a)
    await repos["targets"].add(target_b)
    await repos["authorizations"].add(_make_authorization_record(project_a.id, []))
    await repos["authorizations"].add(_make_authorization_record(project_b.id, []))

    service = _make_service(repos, registry)
    await service.create(
        project_id=project_a.id,
        plugin_name="echo",
        plugin_config={},
        target_ids=[target_a.id],
        initiated_by=uuid4(),
    )
    await service.create(
        project_id=project_b.id,
        plugin_name="echo",
        plugin_config={},
        target_ids=[target_b.id],
        initiated_by=uuid4(),
    )

    scans_a = await service.list_for_project(project_a.id)
    assert len(scans_a) == 1
    assert scans_a[0].project_id == project_a.id
