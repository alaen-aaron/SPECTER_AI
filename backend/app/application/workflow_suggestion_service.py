"""
Workflow suggestion service (Milestone 5).

AI-integrated workflow generation that recommends multi-step scanning
workflows based on target type, available plugins, and current project
state. Builds on the existing PlannerService pattern of never
auto-executing — workflows are suggested as PlannedAction objects
requiring human approval.

Uses:
- Plugin registry for capability-aware workflow composition
- Workflow templates for reusable patterns
- Graph intelligence for context-aware recommendations
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from app.domain.builtin_templates import (
    list_builtin_templates,
)
from app.domain.entities import Asset, Finding, PlannedAction
from app.domain.llm_provider import LLMMessage, LLMProvider
from app.domain.repositories import (
    AssetRepository,
    FindingRepository,
    PlannedActionRepository,
)
from app.domain.value_objects import PlannedActionStatus, Severity
from app.domain.workflow_templates import WorkflowTemplate
from app.plugins.base import PluginCategory
from app.plugins.registry import PluginRegistry


@dataclass(slots=True)
class WorkflowRecommendation:
    """A recommended workflow with metadata."""

    template_id: str
    name: str
    description: str
    reason: str
    confidence: float  # 0.0-1.0
    steps: list[dict[str, Any]] = field(default_factory=list)
    estimated_duration_seconds: int = 0
    required_plugins: list[str] = field(default_factory=list)
    missing_plugins: list[str] = field(default_factory=list)


class WorkflowSuggestionService:
    """
    Recommends multi-step scanning workflows based on project context.

    Combines:
    1. Template-based recommendations (pre-built patterns)
    2. Plugin-capability-aware composition (what can chain together)
    3. Project-state-aware prioritization (what's missing vs. done)
    4. AI-enhanced suggestions (when LLM provider available)
    """

    def __init__(
        self,
        plugin_registry: PluginRegistry,
        finding_repo: FindingRepository,
        asset_repo: AssetRepository,
        planned_action_repo: PlannedActionRepository,
        llm_provider: LLMProvider | None = None,
    ) -> None:
        self._registry = plugin_registry
        self._finding_repo = finding_repo
        self._asset_repo = asset_repo
        self._action_repo = planned_action_repo
        self._llm = llm_provider

    async def recommend_workflows(
        self,
        project_id: UUID,
        target_type: str = "",
        created_by: UUID | None = None,
    ) -> list[WorkflowRecommendation]:
        """
        Recommend workflows for a project based on target type and state.

        Returns recommendations sorted by confidence (highest first).
        """
        findings = await self._finding_repo.list_for_project(project_id, limit=50)
        assets = await self._asset_repo.list_for_project(project_id, limit=50)

        recommendations: list[WorkflowRecommendation] = []

        # 1. Template-based recommendations
        for template in list_builtin_templates():
            rec = self._evaluate_template(template, target_type, findings, assets)
            if rec is not None:
                recommendations.append(rec)

        # 2. Custom composition based on plugin capabilities
        custom = self._suggest_custom_workflow(target_type, findings, assets)
        recommendations.extend(custom)

        # 3. AI-enhanced recommendations
        if self._llm is not None:
            ai_recs = await self._suggest_with_llm(
                project_id, target_type, findings, assets
            )
            recommendations.extend(ai_recs)

        # Sort by confidence
        recommendations.sort(key=lambda r: r.confidence, reverse=True)

        # Create PlannedActions for top recommendations
        for rec in recommendations[:5]:
            action = PlannedAction(
                id=uuid4(),
                project_id=project_id,
                action_type="workflow",
                title=f"Execute workflow: {rec.name}",
                description=(
                    f"{rec.description}\n\n"
                    f"Reason: {rec.reason}\n"
                    f"Estimated duration: {rec.estimated_duration_seconds}s\n"
                    f"Required plugins: {', '.join(rec.required_plugins)}"
                ),
                justification=rec.reason,
                plugin=rec.required_plugins[0] if rec.required_plugins else None,
                target_ids=[],
                plugin_config={
                    "workflow_template_id": rec.template_id,
                    "workflow_steps": rec.steps,
                },
                status=PlannedActionStatus.PENDING_REVIEW,
                created_by=created_by,
            )
            await self._action_repo.create(action)

        return recommendations

    def _evaluate_template(
        self,
        template: WorkflowTemplate,
        target_type: str,
        findings: list[Finding],
        assets: list[Asset],
    ) -> WorkflowRecommendation | None:
        """Evaluate if a template is relevant for the current project state."""
        if not template.target_types:
            return None

        if target_type and target_type not in template.target_types:
            return None

        # Check plugin availability
        required_plugins = {s.plugin for s in template.get_enabled_steps()}
        available_plugins = {p.name() for p in self._registry.list()}
        missing = required_plugins - available_plugins

        confidence = self._compute_template_confidence(
            template, target_type, findings, assets, missing
        )

        if confidence < 0.2:
            return None

        estimated_duration = sum(
            s.timeout_seconds for s in template.get_enabled_steps()
        )

        return WorkflowRecommendation(
            template_id=template.id,
            name=template.name,
            description=template.description,
            reason=self._generate_reason(template, findings, assets, missing),
            confidence=confidence,
            steps=[
                {
                    "id": s.id,
                    "plugin": s.plugin,
                    "name": s.name,
                    "depends_on": s.depends_on,
                }
                for s in template.get_enabled_steps()
            ],
            estimated_duration_seconds=estimated_duration,
            required_plugins=sorted(required_plugins),
            missing_plugins=sorted(missing),
        )

    def _compute_template_confidence(
        self,
        template: WorkflowTemplate,
        target_type: str,
        findings: list[Finding],
        assets: list[Asset],
        missing_plugins: set[str],
    ) -> float:
        """Compute confidence score for a template recommendation."""
        score = 0.5  # base

        # Higher if target type matches
        if target_type and target_type in template.target_types:
            score += 0.2

        # Lower if plugins are missing
        if missing_plugins:
            score -= 0.1 * len(missing_plugins)

        # Higher if project is empty (need initial scanning)
        if not assets and ("reconnaissance" in template.tags or "comprehensive" in template.tags):
            score += 0.2

        # Higher if high-severity findings exist and template is vulnerability-focused
        high_findings = [
            f for f in findings
            if f.severity in (Severity.HIGH, Severity.CRITICAL)
        ]
        if high_findings and "vulnerability" in template.tags:
            score += 0.15

        return max(0.0, min(1.0, score))

    def _generate_reason(
        self,
        template: WorkflowTemplate,
        findings: list[Finding],
        assets: list[Asset],
        missing_plugins: set[str],
    ) -> str:
        """Generate a human-readable reason for the recommendation."""
        parts: list[str] = []

        if not assets:
            parts.append("No assets discovered yet; initial reconnaissance needed")
        elif not findings:
            parts.append(
                f"{len(assets)} assets discovered but no findings; "
                "vulnerability scanning recommended"
            )
        else:
            high = [
                f for f in findings
                if f.severity in (Severity.HIGH, Severity.CRITICAL)
            ]
            if high:
                parts.append(
                    f"{len(high)} high/critical findings warrant "
                    "further investigation"
                )

        if missing_plugins:
            parts.append(
                f"Missing plugins: {', '.join(sorted(missing_plugins))}"
            )
        else:
            parts.append("All required plugins available")

        return "; ".join(parts) if parts else f"Recommended for {template.category}"

    def _suggest_custom_workflow(
        self,
        target_type: str,
        findings: list[Finding],
        assets: list[Asset],
    ) -> list[WorkflowRecommendation]:
        """Suggest custom workflows based on plugin capability chains."""
        recommendations: list[WorkflowRecommendation] = []

        # Build capability chains from registry
        recon_plugins = self._registry.list_by_category(PluginCategory.RECONNAISSANCE)
        scan_plugins = self._registry.list_by_category(PluginCategory.SCANNING)
        vuln_plugins = self._registry.list_by_category(PluginCategory.VULNERABILITY)

        # Suggest a custom chain: recon → scan → vuln
        if recon_plugins and scan_plugins and vuln_plugins:
            chain_plugins = [recon_plugins[0], scan_plugins[0], vuln_plugins[0]]
            chain_names = [p.name() for p in chain_plugins]

            # Check all are available
            missing = [
                name for name in chain_names
                if not self._registry.get(name).health_check()
            ]

            if not missing:
                recommendations.append(
                    WorkflowRecommendation(
                        template_id="custom_chain",
                        name=f"Custom Chain: {' → '.join(chain_names)}",
                        description=(
                            f"Custom workflow: {chain_names[0]} (reconnaissance) → "
                            f"{chain_names[1]} (scanning) → "
                            f"{chain_names[2]} (vulnerability)"
                        ),
                        reason=(
                            "Automated capability chain based on "
                            "available plugins"
                        ),
                        confidence=0.6,
                        steps=[
                            {"id": f"step_{i}", "plugin": name, "name": name}
                            for i, name in enumerate(chain_names)
                        ],
                        estimated_duration_seconds=300,
                        required_plugins=chain_names,
                        missing_plugins=missing,
                    )
                )

        return recommendations

    async def _suggest_with_llm(
        self,
        project_id: UUID,
        target_type: str,
        findings: list[Finding],
        assets: list[Asset],
    ) -> list[WorkflowRecommendation]:
        """Use LLM to generate workflow suggestions."""
        if self._llm is None:
            return []

        available_plugins = [
            f"{p.name()} ({p.metadata().category.value})"
            for p in self._registry.get_healthy_plugins()
        ]

        findings_summary = "\n".join(
            f"- {f.title} (severity: {f.severity.value})"
            for f in findings[:10]
        ) or "No findings yet."

        assets_summary = "\n".join(
            f"- {a.value} (type: {a.asset_type.value})"
            for a in assets[:10]
        ) or "No assets discovered."

        prompt = (
            "You are a security testing workflow planner.\n\n"
            f"Target type: {target_type or 'unknown'}\n"
            f"Available plugins: {', '.join(available_plugins)}\n\n"
            f"Current findings:\n{findings_summary}\n\n"
            f"Current assets:\n{assets_summary}\n\n"
            "Suggest up to 2 scanning workflows as JSON array. Each object should have:\n"
            "- name: workflow name\n"
            "- description: what it does\n"
            "- steps: list of {plugin, description} objects\n"
            "- reason: why this workflow is recommended\n"
            "- estimated_duration_seconds: total estimated time\n"
        )

        messages = [LLMMessage(role="user", content=prompt)]

        try:
            import json

            assert self._llm is not None
            response = await self._llm.complete(messages)
            raw: list[object] = json.loads(response.content)
            if not isinstance(raw, list):
                return []

            recommendations: list[WorkflowRecommendation] = []
            for item in raw[:2]:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name", "AI Workflow"))
                description = str(item.get("description", ""))
                reason = str(item.get("reason", "AI-generated recommendation"))
                steps_raw = item.get("steps", [])
                steps = [
                    {
                        "id": f"ai_step_{i}",
                        "plugin": str(s.get("plugin", "")),
                        "name": str(s.get("description", f"Step {i}")),
                    }
                    for i, s in enumerate(steps_raw)
                    if isinstance(s, dict)
                ]
                required = [s["plugin"] for s in steps if s["plugin"]]
                missing = [
                    p for p in required if not self._registry.get(p).health_check()
                ]
                recommendations.append(
                    WorkflowRecommendation(
                        template_id=f"ai_{name.lower().replace(' ', '_')}",
                        name=name,
                        description=description,
                        reason=reason,
                        confidence=0.7,
                        steps=steps,
                        estimated_duration_seconds=int(
                            item.get("estimated_duration_seconds", 300)
                        ),
                        required_plugins=required,
                        missing_plugins=missing,
                    )
                )
            return recommendations

        except Exception:
            return []
