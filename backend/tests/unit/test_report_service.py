from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.application.report_service import ReportService
from app.domain.entities import Finding
from app.domain.exceptions import ReportAlreadyFinalizedError, ReportNotFoundError
from app.domain.value_objects import FindingStatus, ReportStatus, Severity
from tests.fakes import (
    FakeFindingRepository,
    FakeReportRepository,
    FakeReportVersionRepository,
)


def _make_finding(
    project_id: UUID,
    title: str = "Test finding",
    severity: Severity = Severity.MEDIUM,
) -> Finding:
    return Finding(
        id=uuid4(),
        project_id=project_id,
        title=title,
        severity=severity,
        status=FindingStatus.OPEN,
        dedup_key="",
        created_at=datetime.now(UTC),
    )


def _make_service(
    tmp_path: str | None = None,
) -> tuple[
    ReportService,
    FakeReportRepository,
    FakeReportVersionRepository,
    FakeFindingRepository,
]:
    report_repo = FakeReportRepository()
    version_repo = FakeReportVersionRepository()
    finding_repo = FakeFindingRepository()
    artifacts_dir = tmp_path or tempfile.mkdtemp()
    service = ReportService(
        report_repository=report_repo,
        report_version_repository=version_repo,
        finding_repository=finding_repo,
        artifacts_dir=artifacts_dir,
    )
    return service, report_repo, version_repo, finding_repo


@pytest.mark.asyncio
async def test_create_report_starts_as_draft():
    service, report_repo, _, _ = _make_service()
    project_id = uuid4()
    report = await service.create(project_id, title="Q3 Pentest Report")

    assert report.status is ReportStatus.DRAFT
    assert report.title == "Q3 Pentest Report"
    assert report.project_id == project_id
    assert await report_repo.get(report.id) is not None


@pytest.mark.asyncio
async def test_get_raises_for_unknown_id():
    service, _, _, _ = _make_service()
    with pytest.raises(ReportNotFoundError):
        await service.get(uuid4())


@pytest.mark.asyncio
async def test_generate_version_creates_markdown_file():
    with tempfile.TemporaryDirectory() as tmp:
        service, _, _, finding_repo = _make_service(tmp)
        project_id = uuid4()
        report = await service.create(project_id, title="Test Report")
        await finding_repo.add(
            _make_finding(project_id, "Open Telnet", Severity.HIGH)
        )
        await finding_repo.add(
            _make_finding(project_id, "Weak SSH Key", Severity.LOW)
        )

        version = await service.generate_version(
            report.id, project_id, generated_by=uuid4()
        )

        assert os.path.isfile(version.file_pointer)
        with open(version.file_pointer) as fh:
            content = fh.read()
        assert "# Test Report" in content
        assert "Open Telnet" in content
        assert "Weak SSH Key" in content
        assert "HIGH" in content
        assert "LOW" in content


@pytest.mark.asyncio
async def test_generate_version_increments_version_number():
    with tempfile.TemporaryDirectory() as tmp:
        service, _, _, _ = _make_service(tmp)
        project_id = uuid4()
        report = await service.create(project_id, title="Inc Report")

        v1 = await service.generate_version(
            report.id, project_id, generated_by=uuid4()
        )
        v2 = await service.generate_version(
            report.id, project_id, generated_by=uuid4()
        )

        assert v1.version_number == 1
        assert v2.version_number == 2


@pytest.mark.asyncio
async def test_finalize_sets_status():
    service, _, _, _ = _make_service()
    project_id = uuid4()
    report = await service.create(project_id, title="Finalizable")

    finalized = await service.finalize(report.id)

    assert finalized.status is ReportStatus.FINAL
    persisted = await service.get(report.id)
    assert persisted.status is ReportStatus.FINAL


@pytest.mark.asyncio
async def test_finalize_raises_if_already_final():
    service, _, _, _ = _make_service()
    project_id = uuid4()
    report = await service.create(project_id, title="Done Report")
    await service.finalize(report.id)

    with pytest.raises(ReportAlreadyFinalizedError):
        await service.finalize(report.id)


@pytest.mark.asyncio
async def test_list_for_project():
    service, _, _, _ = _make_service()
    pid1 = uuid4()
    pid2 = uuid4()
    await service.create(pid1, title="Report A")
    await service.create(pid1, title="Report B")
    await service.create(pid2, title="Report C")

    results = await service.list_for_project(pid1)
    assert len(results) == 2
    titles = {r.title for r in results}
    assert titles == {"Report A", "Report B"}
