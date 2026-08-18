"""
Reporter service (SRS §8.1, FR-7.5).

Assembles Explainer + Analyzer output into report-section drafts
(Executive Summary, per-finding narrative). All AI output is
visually watermarked as "AI-drafted — pending human review" (FR-7.6).

Milestone 4.5: Graph-aware reporting — uses Knowledge Graph paths
and relationships to generate richer attack narratives.
"""

from __future__ import annotations

from uuid import UUID

from app.domain.entities import Finding
from app.domain.llm_provider import LLMMessage, LLMProvider
from app.domain.repositories import (
    FindingRepository,
    GraphRepository,
    ReportRepository,
)
from app.domain.value_objects import AIOutputReviewStatus, GraphNodeType


class AIReporterService:
    """
    Assembles AI-drafted report sections for human review.

    Per SRS FR-7.5: drafts Executive Summary and per-finding narrative
    text for human review/edit before report finalization.

    Milestone 4.5: When a GraphRepository is available, enriches
    narratives with graph-derived attack paths and blast radius.
    """

    def __init__(
        self,
        finding_repo: FindingRepository,
        report_repo: ReportRepository,
        llm_provider: LLMProvider | None = None,
        graph_repo: GraphRepository | None = None,
    ) -> None:
        self._finding_repo = finding_repo
        self._report_repo = report_repo
        self._llm = llm_provider
        self._graph_repo = graph_repo

    async def draft_executive_summary(
        self,
        project_id: UUID,
    ) -> dict[str, object]:
        """
        Draft an executive summary for a project's findings.

        Returns a dict with keys: summary, review_status, finding_count,
        severity_breakdown.
        """
        findings = await self._finding_repo.list_for_project(project_id, limit=200)

        severity_counts: dict[str, int] = {}
        for f in findings:
            severity_counts[f.severity.value] = severity_counts.get(f.severity.value, 0) + 1

        if self._llm is not None:
            return await self._draft_with_llm(findings, severity_counts)

        return self._draft_heuristic(findings, severity_counts)

    async def draft_finding_narrative(
        self,
        finding_id: UUID,
    ) -> dict[str, object]:
        """Draft a narrative section for a specific finding.

        Milestone 4.5: Includes graph-derived context such as blast
        radius and connected assets when available.
        """
        finding = await self._finding_repo.get(finding_id)
        if finding is None:
            from app.domain.exceptions import FindingNotFoundError
            raise FindingNotFoundError(finding_id)

        graph_context = await self._get_graph_context(finding)

        if self._llm is not None:
            prompt = (
                f"Write a professional penetration test finding narrative for:\n\n"
                f"Title: {finding.title}\n"
                f"Severity: {finding.severity.value}\n"
                f"Description: {finding.description or 'No description.'}\n"
                f"CVSS Score: {finding.cvss_score or 'N/A'}\n"
            )
            if graph_context:
                prompt += (
                    f"\nGraph context:\n{graph_context}\n"
                )
            prompt += (
                "\nInclude sections: Overview, Impact, Technical Details, Remediation.\n"
                "Respond with JSON: {overview, impact, technical_details, remediation}"
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
                pass

        result: dict[str, object] = {
            "overview": f"Finding: {finding.title} (Severity: {finding.severity.value})",
            "impact": (
                f"This {finding.severity.value} severity finding "
                "may impact the security posture."
            ),
            "technical_details": finding.description or "Technical details not available.",
            "remediation": f"Address the following finding: {finding.title}",
            "review_status": AIOutputReviewStatus.AI_DRAFTED.value,
        }
        if graph_context:
            result["graph_context"] = graph_context
        return result

    async def _get_graph_context(self, finding: Finding) -> str:
        """Build a graph-context string for the finding narrative."""
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

        incoming = await self._graph_repo.get_neighbors(
            finding_node.id, direction="incoming"
        )

        parts: list[str] = []
        if affected_assets:
            asset_labels = [a.label for a in affected_assets[:5]]
            parts.append(
                f"Blast radius: {len(affected_assets)} assets affected "
                f"({', '.join(asset_labels)})"
            )
        if incoming:
            evidence_count = sum(
                1 for n in incoming
                if n.node_type == GraphNodeType.EVIDENCE
            )
            if evidence_count:
                parts.append(f"Supporting evidence: {evidence_count} artifacts")

        return "; ".join(parts)

    async def _draft_with_llm(
        self,
        findings: list[Finding],
        severity_counts: dict[str, int],
    ) -> dict[str, object]:
        findings_summary = "\n".join(
            f"- {f.title} ({f.severity.value})"
            for f in findings[:30]
        ) or "No findings."

        prompt = (
            "Write a professional executive summary for a penetration test report.\n\n"
            f"Total findings: {len(findings)}\n"
            f"Severity breakdown: {severity_counts}\n\n"
            f"Key findings:\n{findings_summary}\n\n"
            "Respond with JSON: {summary, key_findings_count, recommendation}"
        )

        messages = [LLMMessage(role="user", content=prompt)]

        try:
            import json
            assert self._llm is not None
            response = await self._llm.complete(messages)
            data: dict[str, object] = json.loads(response.content)
            data["review_status"] = AIOutputReviewStatus.AI_DRAFTED.value
            data["finding_count"] = len(findings)
            data["severity_breakdown"] = severity_counts
            return data
        except Exception:
            return self._draft_heuristic(findings, severity_counts)

    def _draft_heuristic(
        self,
        findings: list[Finding],
        severity_counts: dict[str, int],
    ) -> dict[str, object]:
        critical = severity_counts.get("critical", 0)
        high = severity_counts.get("high", 0)
        total = len(findings)

        if critical > 0:
            risk_level = "critical"
            recommendation = f"Address {critical} critical findings immediately."
        elif high > 0:
            risk_level = "high"
            recommendation = f"Address {high} high-severity findings promptly."
        elif total > 0:
            risk_level = "moderate"
            recommendation = "Review and address findings as appropriate."
        else:
            risk_level = "low"
            recommendation = "No significant findings identified."

        summary = (
            f"The penetration test identified {total} finding(s) across the target scope. "
            f"Overall risk level: {risk_level}. {recommendation}"
        )

        return {
            "summary": summary,
            "review_status": AIOutputReviewStatus.AI_DRAFTED.value,
            "finding_count": total,
            "severity_breakdown": severity_counts,
            "recommendation": recommendation,
        }
