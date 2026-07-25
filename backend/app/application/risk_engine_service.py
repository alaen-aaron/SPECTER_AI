"""
Risk Engine service (SRS §8.1, FR-7.3).

Computes deterministic base_score from CVSS/exposure heuristics;
AI only supplies the ai_rationale narrative, never the number.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from app.domain.entities import RiskScore
from app.domain.exceptions import RiskScoreAlreadyExistsError, RiskScoreNotFoundError
from app.domain.llm_provider import LLMMessage, LLMProvider
from app.domain.repositories import FindingRepository, RiskScoreRepository
from app.domain.value_objects import AIOutputReviewStatus, RiskScoreSource, Severity

# Base scores by severity (CVSS-derived approximation)
_SEVERITY_BASE_SCORES: dict[Severity, float] = {
    Severity.INFO: 0.0,
    Severity.LOW: 2.5,
    Severity.MEDIUM: 5.0,
    Severity.HIGH: 7.5,
    Severity.CRITICAL: 9.5,
}


class RiskEngineService:
    """
    Deterministic risk scoring + optional AI rationale layer.

    Per SRS FR-7.3: the score itself is never solely an LLM hallucination,
    it's computed, then explained.
    """

    def __init__(
        self,
        risk_score_repo: RiskScoreRepository,
        finding_repo: FindingRepository,
        llm_provider: LLMProvider | None = None,
    ) -> None:
        self._score_repo = risk_score_repo
        self._finding_repo = finding_repo
        self._llm = llm_provider

    async def compute_risk_score(
        self,
        finding_id: UUID,
        exposure_modifier: float = 0.0,
    ) -> RiskScore:
        """
        Compute a deterministic risk score for a finding.

        base_score is computed from severity (CVSS-derived approximation).
        exposure_modifier adjusts based on asset exposure context.
        Optionally generates AI rationale when LLM provider is available.
        """
        existing = await self._score_repo.get_by_finding(finding_id)
        if existing is not None:
            raise RiskScoreAlreadyExistsError(finding_id)

        finding = await self._finding_repo.get(finding_id)
        if finding is None:
            from app.domain.exceptions import FindingNotFoundError
            raise FindingNotFoundError(finding_id)

        base_score = _SEVERITY_BASE_SCORES.get(finding.severity, 5.0)
        if finding.cvss_score is not None:
            base_score = float(finding.cvss_score)

        ai_rationale: str | None = None
        source = RiskScoreSource.COMPUTED

        if self._llm is not None:
            ai_rationale = await self._generate_rationale(
                finding.title, finding.severity.value, base_score, exposure_modifier
            )
            if ai_rationale:
                source = RiskScoreSource.AI_RATIONALE

        score = RiskScore(
            id=uuid4(),
            finding_id=finding_id,
            base_score=base_score,
            exposure_modifier=exposure_modifier,
            ai_rationale=ai_rationale,
            review_status=AIOutputReviewStatus.AI_DRAFTED,
            source=source,
        )
        await self._score_repo.create(score)
        return score

    async def get_risk_score(self, score_id: UUID) -> RiskScore:
        score = await self._score_repo.get(score_id)
        if score is None:
            raise RiskScoreNotFoundError(score_id)
        return score

    async def get_risk_score_for_finding(self, finding_id: UUID) -> RiskScore | None:
        return await self._score_repo.get_by_finding(finding_id)

    async def list_for_project(self, project_id: UUID) -> list[RiskScore]:
        return await self._score_repo.list_for_project(project_id)

    async def _generate_rationale(
        self,
        title: str,
        severity: str,
        base_score: float,
        exposure_modifier: float,
    ) -> str | None:
        """Use LLM to generate a human-readable rationale for the risk score."""
        prompt = (
            f"Explain why this security finding has a risk score of "
            f"{base_score + exposure_modifier:.1f} (base: {base_score}, "
            f"exposure modifier: {exposure_modifier}).\n\n"
            f"Finding: {title}\nSeverity: {severity}\n\n"
            "Provide a 2-3 sentence explanation in plain English."
        )

        messages = [LLMMessage(role="user", content=prompt)]

        try:
            assert self._llm is not None
            response = await self._llm.complete(messages)
            return response.content.strip()
        except Exception:
            return None
