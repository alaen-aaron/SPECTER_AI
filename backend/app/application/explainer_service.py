"""
Explainer service (SRS §8.1, FR-7.4).

Turns a Finding into a plain-English description, with "why this matters"
and "how to fix" sections. AI-drafted content is visually watermarked
as "AI-drafted — pending human review" until approved (FR-7.6).

Milestone 4.5: Graph-aware explanations — uses Knowledge Graph context
to explain blast radius, connected assets, and attack paths.
"""

from __future__ import annotations

from uuid import UUID

from app.domain.entities import Finding
from app.domain.llm_provider import LLMMessage, LLMProvider
from app.domain.repositories import FindingRepository, GraphRepository
from app.domain.value_objects import AIOutputReviewStatus, GraphNodeType


class ExplainerService:
    """
    Generates human-readable explanations of findings.

    Per SRS FR-7.4: generates plain English descriptions with "why this matters"
    and "how to fix" sections.

    Milestone 4.5: When a GraphRepository is available, enriches explanations
    with graph-derived blast radius and attack path context.
    """

    def __init__(
        self,
        finding_repo: FindingRepository,
        llm_provider: LLMProvider | None = None,
        graph_repo: GraphRepository | None = None,
    ) -> None:
        self._finding_repo = finding_repo
        self._llm = llm_provider
        self._graph_repo = graph_repo

    async def explain_finding(
        self,
        finding_id: UUID,
    ) -> dict[str, object]:
        """
        Generate a plain-English explanation for a finding.

        Returns a dict with keys: explanation, why_it_matters, how_to_fix,
        review_status.

        Milestone 4.5: Includes graph context when available.
        """
        finding = await self._finding_repo.get(finding_id)
        if finding is None:
            from app.domain.exceptions import FindingNotFoundError
            raise FindingNotFoundError(finding_id)

        graph_context = await self._get_graph_context(finding)

        if self._llm is not None:
            return await self._explain_with_llm(finding, graph_context)

        return self._explain_heuristic(finding, graph_context)

    async def _explain_with_llm(
        self, finding: Finding, graph_context: str = ""
    ) -> dict[str, object]:
        """Use LLM to generate contextual explanation."""
        prompt = (
            f"Explain this security finding in plain English for a security tester.\n\n"
            f"Title: {finding.title}\n"
            f"Severity: {finding.severity.value}\n"
            f"Description: {finding.description or 'No description provided.'}\n"
            f"CVSS Score: {finding.cvss_score or 'N/A'}\n"
        )
        if graph_context:
            prompt += f"\nGraph context: {graph_context}\n"
        prompt += (
            "\nRespond with a JSON object with keys: explanation, why_it_matters, how_to_fix.\n"
            "Each should be a string of 2-4 sentences."
        )

        messages = [LLMMessage(role="user", content=prompt)]

        try:
            import json
            assert self._llm is not None
            response = await self._llm.complete(messages)
            data: dict[str, object] = json.loads(response.content)
            data["review_status"] = AIOutputReviewStatus.AI_DRAFTED.value
            if graph_context:
                data["graph_context"] = graph_context
            return data
        except Exception:
            return self._explain_heuristic(finding, graph_context)

    def _explain_heuristic(
        self, finding: Finding, graph_context: str = ""
    ) -> dict[str, object]:
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

        explanation = explanations.get(severity, f"Finding: {title}")
        why = why_it_matters.get(
            severity, "This finding is part of the assessment."
        )

        if graph_context:
            explanation += f" {graph_context}"

        return {
            "explanation": explanation,
            "why_it_matters": why,
            "how_to_fix": (
                f"Review and apply remediation for: {title}"
            ),
            "review_status": AIOutputReviewStatus.AI_DRAFTED.value,
            **({"graph_context": graph_context} if graph_context else {}),
        }

    async def _get_graph_context(self, finding: Finding) -> str:
        """Build a graph-context string for the explanation."""
        if self._graph_repo is None:
            return ""

        finding_node = await self._graph_repo.find_node(
            finding.project_id,
            GraphNodeType.FINDING,
            "findings",
            finding.id,
        )
        if finding_node is None:
            return ""

        blast = await self._graph_repo.blast_radius(
            finding.project_id, finding_node.id, max_depth=3
        )
        affected_assets = [
            n for n in blast if n.node_type == GraphNodeType.ASSET
        ]

        if not affected_assets:
            return ""

        asset_names = [a.label for a in affected_assets[:5]]
        remaining = len(affected_assets) - len(asset_names)
        msg = (
            f"Graph analysis shows {len(affected_assets)} assets in "
            f"blast radius ({', '.join(asset_names)}"
        )
        if remaining > 0:
            msg += f" and {remaining} more"
        msg += ")."
        return msg
