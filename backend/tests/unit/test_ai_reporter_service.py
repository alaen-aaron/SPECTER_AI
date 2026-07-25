"""Unit tests for AIReporterService (Phase 4, SRS §8.1, FR-7.5)."""

from __future__ import annotations

import json
from uuid import UUID, uuid4

import pytest

from app.application.ai_reporter_service import AIReporterService
from app.domain.entities import Finding
from app.domain.exceptions import FindingNotFoundError
from app.domain.llm_provider import LLMMessage, LLMResponse
from app.domain.value_objects import (
    AIOutputReviewStatus,
    FindingStatus,
    Severity,
)
from tests.fakes import FakeFindingRepository, FakeReportRepository


def _finding(
    project_id: UUID,
    title: str = "Test Vuln",
    severity: Severity = Severity.MEDIUM,
    description: str | None = None,
) -> Finding:
    return Finding(
        id=uuid4(),
        project_id=project_id,
        title=title,
        severity=severity,
        status=FindingStatus.OPEN,
        description=description,
    )


class FakeLLM:
    async def complete(self, messages: list[LLMMessage]) -> LLMResponse:
        data = {
            "summary": "Critical risk. Immediate action required.",
            "key_findings_count": 5,
            "recommendation": "Patch all systems.",
        }
        return LLMResponse(content=json.dumps(data), model="fake")


class FailingLLM:
    async def complete(self, messages: list[LLMMessage]) -> LLMResponse:
        raise RuntimeError("LLM down")


@pytest.mark.asyncio
async def test_draft_executive_summary_empty():
    project_id = uuid4()
    svc = AIReporterService(
        finding_repo=FakeFindingRepository(),
        report_repo=FakeReportRepository(),
    )
    result = await svc.draft_executive_summary(project_id)
    assert result["finding_count"] == 0
    assert result["severity_breakdown"] == {}
    assert result["review_status"] == AIOutputReviewStatus.AI_DRAFTED.value
    assert "no significant" in result["summary"].lower()


@pytest.mark.asyncio
async def test_draft_executive_summary_with_findings():
    project_id = uuid4()
    repo = FakeFindingRepository()
    await repo.add(_finding(project_id, "A", Severity.CRITICAL))
    await repo.add(_finding(project_id, "B", Severity.HIGH))
    await repo.add(_finding(project_id, "C", Severity.HIGH))

    svc = AIReporterService(finding_repo=repo, report_repo=FakeReportRepository())
    result = await svc.draft_executive_summary(project_id)
    assert result["finding_count"] == 3
    assert result["severity_breakdown"]["critical"] == 1
    assert result["severity_breakdown"]["high"] == 2
    assert "critical" in result["recommendation"].lower()


@pytest.mark.asyncio
async def test_draft_executive_summary_with_llm():
    project_id = uuid4()
    repo = FakeFindingRepository()
    await repo.add(_finding(project_id, "A", Severity.CRITICAL))

    svc = AIReporterService(
        finding_repo=repo,
        report_repo=FakeReportRepository(),
        llm_provider=FakeLLM(),
    )
    result = await svc.draft_executive_summary(project_id)
    assert result["review_status"] == AIOutputReviewStatus.AI_DRAFTED.value
    assert "finding_count" in result


@pytest.mark.asyncio
async def test_draft_executive_summary_llm_failure_fallback():
    project_id = uuid4()
    repo = FakeFindingRepository()
    await repo.add(_finding(project_id, "A", Severity.LOW))

    svc = AIReporterService(
        finding_repo=repo,
        report_repo=FakeReportRepository(),
        llm_provider=FailingLLM(),
    )
    result = await svc.draft_executive_summary(project_id)
    assert result["finding_count"] == 1
    assert "low" in result["recommendation"].lower() or "review" in result["recommendation"].lower()


@pytest.mark.asyncio
async def test_draft_finding_narrative_heuristic():
    project_id = uuid4()
    repo = FakeFindingRepository()
    finding = _finding(
        project_id, "XSS in search", Severity.HIGH,
        description="Reflected XSS via query param",
    )
    await repo.add(finding)

    svc = AIReporterService(finding_repo=repo, report_repo=FakeReportRepository())
    result = await svc.draft_finding_narrative(finding.id)
    assert result["review_status"] == AIOutputReviewStatus.AI_DRAFTED.value
    assert "XSS" in result["overview"]
    assert result["technical_details"] == "Reflected XSS via query param"
    assert result["remediation"]


@pytest.mark.asyncio
async def test_draft_finding_narrative_with_llm():
    project_id = uuid4()
    repo = FakeFindingRepository()
    finding = _finding(project_id, "RCE", Severity.CRITICAL)
    await repo.add(finding)

    class LocalLLM:
        async def complete(self, messages: list[LLMMessage]) -> LLMResponse:
            data = {
                "overview": "Remote code execution found.",
                "impact": "Full server compromise.",
                "technical_details": "Via deserialization flaw.",
                "remediation": "Sanitize inputs.",
            }
            return LLMResponse(content=json.dumps(data), model="fake")

    svc = AIReporterService(
        finding_repo=repo, report_repo=FakeReportRepository(), llm_provider=LocalLLM()
    )
    result = await svc.draft_finding_narrative(finding.id)
    assert "Remote code" in result["overview"]


@pytest.mark.asyncio
async def test_draft_finding_narrative_not_found():
    svc = AIReporterService(
        finding_repo=FakeFindingRepository(),
        report_repo=FakeReportRepository(),
    )
    with pytest.raises(FindingNotFoundError):
        await svc.draft_finding_narrative(uuid4())


@pytest.mark.asyncio
async def test_heuristic_risk_levels():
    project_id = uuid4()
    repo = FakeFindingRepository()

    svc = AIReporterService(finding_repo=repo, report_repo=FakeReportRepository())

    await repo.add(_finding(project_id, "A", Severity.LOW))
    result = await svc.draft_executive_summary(project_id)
    assert result["severity_breakdown"]["low"] == 1
    rec = result["recommendation"].lower()
    assert "moderate" in rec or "review" in rec
