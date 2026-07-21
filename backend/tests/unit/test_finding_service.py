from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.application.finding_service import FindingService
from app.domain.entities import Finding, ToolResult
from app.domain.exceptions import FindingNotFoundError
from app.domain.value_objects import FindingStatus, Severity
from tests.fakes import FakeAssetRepository, FakeFindingRepository


def _make_finding(
    project_id,
    title: str = "Test finding",
    severity: Severity = Severity.MEDIUM,
    dedup_key: str = "",
) -> Finding:
    now = datetime.now(UTC)
    return Finding(
        id=uuid4(),
        project_id=project_id,
        title=title,
        severity=severity,
        status=FindingStatus.OPEN,
        description="test",
        dedup_key=dedup_key,
        tool_result_ids=[uuid4()],
        created_at=now,
    )


def _make_tool_result(plugin, payload, scan_id=None):
    return ToolResult(
        id=uuid4(),
        scan_id=scan_id or uuid4(),
        plugin=plugin,
        target="test-target",
        normalized_payload=payload,
        created_at=datetime.now(UTC),
    )


@pytest.fixture
def repos():
    return {
        "findings": FakeFindingRepository(),
        "assets": FakeAssetRepository(),
    }


def _make_service(repos) -> FindingService:
    return FindingService(
        finding_repository=repos["findings"],
        asset_repository=repos["assets"],
    )


@pytest.mark.asyncio
async def test_list_for_project_returns_findings(repos):
    project_id = uuid4()
    f1 = _make_finding(project_id, "Finding A", Severity.HIGH)
    f2 = _make_finding(project_id, "Finding B", Severity.LOW)
    await repos["findings"].add(f1)
    await repos["findings"].add(f2)

    service = _make_service(repos)
    result = await service.list_for_project(project_id)

    assert len(result) == 2
    titles = {f.title for f in result}
    assert titles == {"Finding A", "Finding B"}


@pytest.mark.asyncio
async def test_list_for_project_filters_by_severity(repos):
    project_id = uuid4()
    await repos["findings"].add(_make_finding(project_id, "High", Severity.HIGH))
    await repos["findings"].add(_make_finding(project_id, "Low", Severity.LOW))
    await repos["findings"].add(_make_finding(project_id, "High 2", Severity.HIGH))

    service = _make_service(repos)
    result = await service.list_for_project(project_id, severity=Severity.HIGH)

    assert len(result) == 2
    assert all(f.severity is Severity.HIGH for f in result)


@pytest.mark.asyncio
async def test_get_raises_for_unknown_id(repos):
    service = _make_service(repos)
    with pytest.raises(FindingNotFoundError):
        await service.get(uuid4())


@pytest.mark.asyncio
async def test_get_returns_existing_finding(repos):
    project_id = uuid4()
    finding = _make_finding(project_id, "Known issue")
    await repos["findings"].add(finding)

    service = _make_service(repos)
    result = await service.get(finding.id)

    assert result.id == finding.id
    assert result.title == "Known issue"


@pytest.mark.asyncio
async def test_create_from_nmap_creates_findings_for_vulnerable_services(repos):
    project_id = uuid4()
    tr = _make_tool_result(
        "nmap",
        {
            "target": "10.0.0.1",
            "ports": [
                {
                    "port": 23, "state": "open", "service": "telnet",
                    "protocol": "tcp", "version": "",
                },
                {
                    "port": 21, "state": "open", "service": "ftp",
                    "protocol": "tcp", "version": "vsftpd 3.0.3",
                },
            ],
        },
    )

    service = _make_service(repos)
    findings = await service.create_from_tool_result(project_id, tr)

    assert len(findings) == 2
    by_service = {f.title.split(":")[1].strip().split(" ")[0]: f for f in findings}

    telnet = by_service["telnet"]
    assert telnet.severity is Severity.MEDIUM
    assert telnet.project_id == project_id
    assert "telnet" in telnet.description.lower()

    ftp = by_service["ftp"]
    assert ftp.severity is Severity.LOW
    assert "vsftpd" in ftp.description


@pytest.mark.asyncio
async def test_create_from_nmap_skips_known_services(repos):
    project_id = uuid4()
    tr = _make_tool_result(
        "nmap",
        {
            "target": "10.0.0.1",
            "ports": [
                {
                    "port": 22, "state": "open", "service": "ssh",
                    "protocol": "tcp", "version": "OpenSSH 8.9",
                },
                {
                    "port": 80, "state": "open", "service": "http",
                    "protocol": "tcp", "version": "nginx",
                },
            ],
        },
    )

    service = _make_service(repos)
    findings = await service.create_from_tool_result(project_id, tr)

    assert findings == []
    all_findings = await service.list_for_project(project_id)
    assert all_findings == []


@pytest.mark.asyncio
async def test_create_from_nmap_deduplicates(repos):
    project_id = uuid4()
    tr = _make_tool_result(
        "nmap",
        {
            "target": "10.0.0.1",
            "ports": [
                {
                    "port": 23, "state": "open", "service": "telnet",
                    "protocol": "tcp", "version": "",
                },
            ],
        },
    )

    service = _make_service(repos)
    first = await service.create_from_tool_result(project_id, tr)
    assert len(first) == 1
    first_id = first[0].id

    second = await service.create_from_tool_result(project_id, tr)
    assert second == []

    all_findings = await service.list_for_project(project_id)
    assert len(all_findings) == 1
    assert all_findings[0].id == first_id


@pytest.mark.asyncio
async def test_create_from_non_nmap_returns_empty(repos):
    project_id = uuid4()
    tr = _make_tool_result(
        "ping",
        {"host": "10.0.0.1", "reachable": True},
    )

    service = _make_service(repos)
    findings = await service.create_from_tool_result(project_id, tr)

    assert findings == []


@pytest.mark.asyncio
async def test_update_status_changes_finding_status(repos):
    project_id = uuid4()
    finding = _make_finding(project_id)
    await repos["findings"].add(finding)

    service = _make_service(repos)
    updated = await service.update_status(finding.id, FindingStatus.CONFIRMED)

    assert updated.status is FindingStatus.CONFIRMED
    assert updated.id == finding.id

    persisted = await service.get(finding.id)
    assert persisted.status is FindingStatus.CONFIRMED
