"""Unit tests for RiskEngineService (Phase 4, SRS §8.1, FR-7.3)."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.application.risk_engine_service import RiskEngineService
from app.domain.entities import Finding, RiskScore
from app.domain.exceptions import (
    FindingNotFoundError,
    RiskScoreAlreadyExistsError,
    RiskScoreNotFoundError,
)
from app.domain.llm_provider import LLMMessage, LLMResponse
from app.domain.value_objects import (
    AIOutputReviewStatus,
    FindingStatus,
    RiskScoreSource,
    Severity,
)
from tests.fakes import (
    FakeFindingRepository,
    FakeRiskScoreRepository,
)


def _finding(
    project_id: UUID,
    severity: Severity = Severity.HIGH,
    cvss_score: float | None = None,
) -> Finding:
    return Finding(
        id=uuid4(),
        project_id=project_id,
        title="Test Finding",
        severity=severity,
        status=FindingStatus.OPEN,
        cvss_score=cvss_score,
    )


class FakeLLMProvider:
    async def complete(self, messages: list[LLMMessage]) -> LLMResponse:
        return LLMResponse(
            content="This finding has a high risk score because it exposes critical data.",
            model="fake",
        )


class FailingLLMProvider:
    async def complete(self, messages: list[LLMMessage]) -> LLMResponse:
        raise RuntimeError("LLM unavailable")


@pytest.mark.asyncio
async def test_compute_basic_score_no_cvss():
    project_id = uuid4()
    finding = _finding(project_id, Severity.HIGH)
    finding_repo = FakeFindingRepository()
    await finding_repo.add(finding)

    svc = RiskEngineService(
        risk_score_repo=FakeRiskScoreRepository(),
        finding_repo=finding_repo,
    )
    score = await svc.compute_risk_score(finding.id)
    assert score.base_score == 7.5
    assert score.source is RiskScoreSource.COMPUTED
    assert score.ai_rationale is None
    assert score.review_status is AIOutputReviewStatus.AI_DRAFTED


@pytest.mark.asyncio
async def test_compute_score_with_cvss():
    project_id = uuid4()
    finding = _finding(project_id, Severity.MEDIUM, cvss_score=8.2)
    finding_repo = FakeFindingRepository()
    await finding_repo.add(finding)

    svc = RiskEngineService(
        risk_score_repo=FakeRiskScoreRepository(),
        finding_repo=finding_repo,
    )
    score = await svc.compute_risk_score(finding.id)
    assert score.base_score == 8.2


@pytest.mark.asyncio
async def test_compute_score_with_exposure_modifier():
    project_id = uuid4()
    finding = _finding(project_id, Severity.MEDIUM)
    finding_repo = FakeFindingRepository()
    await finding_repo.add(finding)

    svc = RiskEngineService(
        risk_score_repo=FakeRiskScoreRepository(),
        finding_repo=finding_repo,
    )
    score = await svc.compute_risk_score(finding.id, exposure_modifier=1.5)
    assert score.exposure_modifier == 1.5
    assert score.base_score == 5.0


@pytest.mark.asyncio
async def test_compute_score_with_llm_rationale():
    project_id = uuid4()
    finding = _finding(project_id, Severity.CRITICAL)
    finding_repo = FakeFindingRepository()
    await finding_repo.add(finding)

    svc = RiskEngineService(
        risk_score_repo=FakeRiskScoreRepository(),
        finding_repo=finding_repo,
        llm_provider=FakeLLMProvider(),
    )
    score = await svc.compute_risk_score(finding.id)
    assert score.base_score == 9.5
    assert score.ai_rationale is not None
    assert score.source is RiskScoreSource.AI_RATIONALE


@pytest.mark.asyncio
async def test_compute_score_llm_failure_fallback():
    project_id = uuid4()
    finding = _finding(project_id, Severity.LOW)
    finding_repo = FakeFindingRepository()
    await finding_repo.add(finding)

    svc = RiskEngineService(
        risk_score_repo=FakeRiskScoreRepository(),
        finding_repo=finding_repo,
        llm_provider=FailingLLMProvider(),
    )
    score = await svc.compute_risk_score(finding.id)
    assert score.ai_rationale is None
    assert score.source is RiskScoreSource.COMPUTED


@pytest.mark.asyncio
async def test_compute_duplicate_raises():
    project_id = uuid4()
    finding = _finding(project_id)
    finding_repo = FakeFindingRepository()
    await finding_repo.add(finding)

    score_repo = FakeRiskScoreRepository()
    score = RiskScore(
        id=uuid4(),
        finding_id=finding.id,
        base_score=5.0,
        exposure_modifier=0.0,
        review_status=AIOutputReviewStatus.AI_DRAFTED,
        source=RiskScoreSource.COMPUTED,
    )
    await score_repo.create(score)

    svc = RiskEngineService(
        risk_score_repo=score_repo, finding_repo=finding_repo
    )
    with pytest.raises(RiskScoreAlreadyExistsError):
        await svc.compute_risk_score(finding.id)


@pytest.mark.asyncio
async def test_compute_finding_not_found():
    svc = RiskEngineService(
        risk_score_repo=FakeRiskScoreRepository(),
        finding_repo=FakeFindingRepository(),
    )
    with pytest.raises(FindingNotFoundError):
        await svc.compute_risk_score(uuid4())


@pytest.mark.asyncio
async def test_get_risk_score():
    score_repo = FakeRiskScoreRepository()
    score = RiskScore(
        id=uuid4(),
        finding_id=uuid4(),
        base_score=5.0,
        exposure_modifier=0.0,
        review_status=AIOutputReviewStatus.AI_DRAFTED,
        source=RiskScoreSource.COMPUTED,
    )
    await score_repo.create(score)

    svc = RiskEngineService(
        risk_score_repo=score_repo, finding_repo=FakeFindingRepository()
    )
    result = await svc.get_risk_score(score.id)
    assert result.base_score == 5.0


@pytest.mark.asyncio
async def test_get_risk_score_not_found():
    svc = RiskEngineService(
        risk_score_repo=FakeRiskScoreRepository(),
        finding_repo=FakeFindingRepository(),
    )
    with pytest.raises(RiskScoreNotFoundError):
        await svc.get_risk_score(uuid4())


@pytest.mark.asyncio
async def test_get_risk_score_for_finding():
    score_repo = FakeRiskScoreRepository()
    finding_id = uuid4()
    score = RiskScore(
        id=uuid4(),
        finding_id=finding_id,
        base_score=2.5,
        exposure_modifier=0.0,
        review_status=AIOutputReviewStatus.AI_DRAFTED,
        source=RiskScoreSource.COMPUTED,
    )
    await score_repo.create(score)

    svc = RiskEngineService(
        risk_score_repo=score_repo, finding_repo=FakeFindingRepository()
    )
    result = await svc.get_risk_score_for_finding(finding_id)
    assert result is not None
    assert result.base_score == 2.5


@pytest.mark.asyncio
async def test_severity_base_scores():
    project_id = uuid4()
    finding_repo = FakeFindingRepository()
    svc = RiskEngineService(
        risk_score_repo=FakeRiskScoreRepository(),
        finding_repo=finding_repo,
    )
    expected = {
        Severity.INFO: 0.0,
        Severity.LOW: 2.5,
        Severity.MEDIUM: 5.0,
        Severity.HIGH: 7.5,
        Severity.CRITICAL: 9.5,
    }
    for sev, expected_score in expected.items():
        finding = _finding(project_id, sev)
        await finding_repo.add(finding)
        score = await svc.compute_risk_score(finding.id)
        assert score.base_score == expected_score
        assert score.source is RiskScoreSource.COMPUTED
