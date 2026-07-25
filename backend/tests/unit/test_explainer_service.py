"""Unit tests for ExplainerService (Phase 4, SRS §8.1, FR-7.4)."""

from __future__ import annotations

import json
from uuid import UUID, uuid4

import pytest

from app.application.explainer_service import ExplainerService
from app.domain.entities import Finding
from app.domain.exceptions import FindingNotFoundError
from app.domain.llm_provider import LLMMessage, LLMResponse
from app.domain.value_objects import (
    AIOutputReviewStatus,
    FindingStatus,
    Severity,
)
from tests.fakes import FakeFindingRepository


def _finding(
    project_id: UUID,
    title: str = "SQL Injection in login",
    severity: Severity = Severity.CRITICAL,
    description: str | None = "User input not sanitized",
    cvss_score: float | None = 9.8,
) -> Finding:
    return Finding(
        id=uuid4(),
        project_id=project_id,
        title=title,
        severity=severity,
        status=FindingStatus.OPEN,
        description=description,
        cvss_score=cvss_score,
    )


class FakeLLM:
    async def complete(self, messages: list[LLMMessage]) -> LLMResponse:
        data = {
            "explanation": "The login form is vulnerable to SQL injection.",
            "why_it_matters": "Attackers can bypass authentication.",
            "how_to_fix": "Use parameterized queries.",
        }
        return LLMResponse(content=json.dumps(data), model="fake")


class FailingLLM:
    async def complete(self, messages: list[LLMMessage]) -> LLMResponse:
        raise RuntimeError("LLM down")


@pytest.mark.asyncio
async def test_explain_heuristic_critical():
    project_id = uuid4()
    repo = FakeFindingRepository()
    finding = _finding(project_id, severity=Severity.CRITICAL)
    await repo.add(finding)

    svc = ExplainerService(finding_repo=repo)
    result = await svc.explain_finding(finding.id)
    assert "critical" in result["explanation"].lower()
    assert result["review_status"] == AIOutputReviewStatus.AI_DRAFTED.value
    assert result["how_to_fix"]


@pytest.mark.asyncio
async def test_explain_heuristic_high():
    project_id = uuid4()
    repo = FakeFindingRepository()
    finding = _finding(project_id, "Weak TLS", Severity.HIGH)
    await repo.add(finding)

    svc = ExplainerService(finding_repo=repo)
    result = await svc.explain_finding(finding.id)
    assert "high" in result["explanation"].lower()


@pytest.mark.asyncio
async def test_explain_heuristic_medium():
    project_id = uuid4()
    repo = FakeFindingRepository()
    finding = _finding(project_id, "Missing headers", Severity.MEDIUM)
    await repo.add(finding)

    svc = ExplainerService(finding_repo=repo)
    result = await svc.explain_finding(finding.id)
    assert "medium" in result["explanation"].lower()


@pytest.mark.asyncio
async def test_explain_heuristic_low():
    project_id = uuid4()
    repo = FakeFindingRepository()
    finding = _finding(project_id, "Info leak", Severity.LOW)
    await repo.add(finding)

    svc = ExplainerService(finding_repo=repo)
    result = await svc.explain_finding(finding.id)
    assert "low" in result["explanation"].lower()


@pytest.mark.asyncio
async def test_explain_heuristic_info():
    project_id = uuid4()
    repo = FakeFindingRepository()
    finding = _finding(project_id, "Server banner", Severity.INFO)
    await repo.add(finding)

    svc = ExplainerService(finding_repo=repo)
    result = await svc.explain_finding(finding.id)
    assert "informational" in result["explanation"].lower()


@pytest.mark.asyncio
async def test_explain_with_llm():
    project_id = uuid4()
    repo = FakeFindingRepository()
    finding = _finding(project_id)
    await repo.add(finding)

    svc = ExplainerService(finding_repo=repo, llm_provider=FakeLLM())
    result = await svc.explain_finding(finding.id)
    assert "SQL injection" in result["explanation"]
    assert result["review_status"] == AIOutputReviewStatus.AI_DRAFTED.value


@pytest.mark.asyncio
async def test_explain_llm_failure_fallback():
    project_id = uuid4()
    repo = FakeFindingRepository()
    finding = _finding(project_id, severity=Severity.HIGH)
    await repo.add(finding)

    svc = ExplainerService(finding_repo=repo, llm_provider=FailingLLM())
    result = await svc.explain_finding(finding.id)
    assert "high" in result["explanation"].lower()


@pytest.mark.asyncio
async def test_explain_not_found():
    svc = ExplainerService(finding_repo=FakeFindingRepository())
    with pytest.raises(FindingNotFoundError):
        await svc.explain_finding(uuid4())
