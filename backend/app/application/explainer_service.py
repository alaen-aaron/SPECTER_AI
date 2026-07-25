"""
Explainer service (SRS §8.1, FR-7.4).

Turns a Finding into a plain-English description, with "why this matters"
and "how to fix" sections. AI-drafted content is visually watermarked
as "AI-drafted — pending human review" until approved (FR-7.6).
"""

from __future__ import annotations

from uuid import UUID

from app.domain.entities import Finding
from app.domain.llm_provider import LLMMessage, LLMProvider
from app.domain.repositories import FindingRepository
from app.domain.value_objects import AIOutputReviewStatus


class ExplainerService:
    """
    Generates human-readable explanations of findings.

    Per SRS FR-7.4: generates plain English descriptions with "why this matters"
    and "how to fix" sections.
    """

    def __init__(
        self,
        finding_repo: FindingRepository,
        llm_provider: LLMProvider | None = None,
    ) -> None:
        self._finding_repo = finding_repo
        self._llm = llm_provider

    async def explain_finding(
        self,
        finding_id: UUID,
    ) -> dict[str, object]:
        """
        Generate a plain-English explanation for a finding.

        Returns a dict with keys: explanation, why_it_matters, how_to_fix,
        review_status.
        """
        finding = await self._finding_repo.get(finding_id)
        if finding is None:
            from app.domain.exceptions import FindingNotFoundError
            raise FindingNotFoundError(finding_id)

        if self._llm is not None:
            return await self._explain_with_llm(finding)

        return self._explain_heuristic(finding)

    async def _explain_with_llm(self, finding: Finding) -> dict[str, object]:
        """Use LLM to generate contextual explanation."""
        prompt = (
            f"Explain this security finding in plain English for a security tester.\n\n"
            f"Title: {finding.title}\n"
            f"Severity: {finding.severity.value}\n"
            f"Description: {finding.description or 'No description provided.'}\n"
            f"CVSS Score: {finding.cvss_score or 'N/A'}\n\n"
            "Respond with a JSON object with keys: explanation, why_it_matters, how_to_fix.\n"
            "Each should be a string of 2-4 sentences."
        )

        messages = [LLMMessage(role="user", content=prompt)]

        try:
            import json
            assert self._llm is not None
            response = await self._llm.complete(messages)
            data: dict[str, object] = json.loads(response.content)
            data["review_status"] = AIOutputReviewStatus.AI_DRAFTED.value
            return data
        except Exception:
            return self._explain_heuristic(finding)

    def _explain_heuristic(self, finding: Finding) -> dict[str, object]:
        severity = finding.severity.value
        title = finding.title

        explanations = {
            "critical": (
                f"This is a critical severity finding: {title}. "
                "Immediate attention is required."
            ),
            "high": (
                f"This is a high severity finding: {title}. "
                "It should be investigated promptly."
            ),
            "medium": f"This is a medium severity finding: {title}.",
            "low": f"This is a low severity finding: {title}.",
            "info": f"This is an informational finding: {title}.",
        }

        why_it_matters = {
            "critical": (
                "Critical findings often indicate exploitable "
                "vulnerabilities that could lead to full system compromise."
            ),
            "high": (
                "High severity findings can significantly impact "
                "the security posture of the target."
            ),
            "medium": (
                "Medium severity findings may be exploitable "
                "in certain conditions."
            ),
            "low": (
                "Low severity findings have limited impact "
                "but should still be addressed."
            ),
            "info": (
                "Informational findings provide context "
                "for the security assessment."
            ),
        }

        return {
            "explanation": explanations.get(severity, f"Finding: {title}"),
            "why_it_matters": why_it_matters.get(
                severity, "This finding is part of the assessment."
            ),
            "how_to_fix": (
                f"Review and apply remediation for: {title}"
            ),
            "review_status": AIOutputReviewStatus.AI_DRAFTED.value,
        }
