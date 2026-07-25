"""Unit tests for AnalyzerService (Phase 4, SRS §8.1, FR-7.2)."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.application.analyzer_service import AnalyzerService
from app.domain.entities import Finding
from app.domain.value_objects import FindingStatus, Severity
from tests.fakes import FakeFindingRepository


def _finding(
    project_id: UUID,
    title: str = "Test Finding",
    severity: Severity = Severity.MEDIUM,
    dedup_key: str = "",
    tool_result_ids: list[UUID] | None = None,
) -> Finding:
    return Finding(
        id=uuid4(),
        project_id=project_id,
        title=title,
        severity=severity,
        status=FindingStatus.OPEN,
        dedup_key=dedup_key,
        tool_result_ids=tool_result_ids or [],
    )


@pytest.mark.asyncio
async def test_correlate_empty_project():
    project_id = uuid4()
    repo = FakeFindingRepository()
    svc = AnalyzerService(finding_repo=repo)
    result = await svc.correlate_findings(project_id)
    assert result["total_findings"] == 0
    assert result["correlations_found"] == 0


@pytest.mark.asyncio
async def test_correlate_unique_findings():
    project_id = uuid4()
    repo = FakeFindingRepository()
    await repo.add(_finding(project_id, "A", dedup_key="a"))
    await repo.add(_finding(project_id, "B", dedup_key="b"))
    svc = AnalyzerService(finding_repo=repo)
    result = await svc.correlate_findings(project_id)
    assert result["total_findings"] == 2
    assert result["unique_findings"] == 2
    assert result["correlations_found"] == 0
    assert result["duplicates_merged"] == 0


@pytest.mark.asyncio
async def test_correlate_merges_duplicates():
    project_id = uuid4()
    repo = FakeFindingRepository()
    f1 = _finding(
        project_id,
        "SQL Injection",
        Severity.CRITICAL,
        dedup_key="sql-inj",
        tool_result_ids=[uuid4()],
    )
    f2 = _finding(
        project_id,
        "SQL Injection",
        Severity.CRITICAL,
        dedup_key="sql-inj",
        tool_result_ids=[uuid4()],
    )
    await repo.add(f1)
    await repo.add(f2)
    svc = AnalyzerService(finding_repo=repo)
    result = await svc.correlate_findings(project_id)
    assert result["correlations_found"] == 1
    assert result["duplicates_merged"] == 1
    assert len(f1.tool_result_ids) == 2


@pytest.mark.asyncio
async def test_correlate_falls_back_to_title_severity_key():
    project_id = uuid4()
    repo = FakeFindingRepository()
    f1 = _finding(project_id, "Open Port", Severity.LOW, dedup_key="")
    f2 = _finding(project_id, "Open Port", Severity.LOW, dedup_key="")
    await repo.add(f1)
    await repo.add(f2)
    svc = AnalyzerService(finding_repo=repo)
    result = await svc.correlate_findings(project_id)
    assert result["correlations_found"] == 1
    assert result["duplicates_merged"] == 1


@pytest.mark.asyncio
async def test_correlate_no_merge_different_severity():
    project_id = uuid4()
    repo = FakeFindingRepository()
    f1 = _finding(project_id, "X", Severity.LOW, dedup_key="x")
    f2 = _finding(project_id, "X", Severity.HIGH, dedup_key="x")
    await repo.add(f1)
    await repo.add(f2)
    svc = AnalyzerService(finding_repo=repo)
    result = await svc.correlate_findings(project_id)
    assert result["correlations_found"] == 1
    assert result["duplicates_merged"] == 1


@pytest.mark.asyncio
async def test_get_finding_correlations_none_when_not_found():
    repo = FakeFindingRepository()
    svc = AnalyzerService(finding_repo=repo)
    result = await svc.get_finding_correlations(uuid4())
    assert result == []


@pytest.mark.asyncio
async def test_get_finding_correlations_returns_shared_tools():
    project_id = uuid4()
    repo = FakeFindingRepository()
    shared_tool = uuid4()
    f1 = _finding(
        project_id, "A", Severity.MEDIUM,
        tool_result_ids=[shared_tool],
    )
    f2 = _finding(
        project_id, "B", Severity.HIGH,
        tool_result_ids=[shared_tool, uuid4()],
    )
    await repo.add(f1)
    await repo.add(f2)
    svc = AnalyzerService(finding_repo=repo)
    correlations = await svc.get_finding_correlations(f1.id)
    assert len(correlations) == 1
    assert correlations[0]["finding_id"] == str(f2.id)
    assert correlations[0]["shared_tool_results"] == 1


@pytest.mark.asyncio
async def test_get_finding_correlations_excludes_self():
    project_id = uuid4()
    repo = FakeFindingRepository()
    f1 = _finding(
        project_id, "A", Severity.MEDIUM,
        tool_result_ids=[uuid4()],
    )
    await repo.add(f1)
    svc = AnalyzerService(finding_repo=repo)
    correlations = await svc.get_finding_correlations(f1.id)
    assert correlations == []
