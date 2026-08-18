"""
Planner service (SRS §8.1, FR-7.1).

Given current project asset/finding state, proposes a ranked list of
next recon/scan actions with justification. Output: PlannedAction[]
with status=pending_review. Never auto-executed — a human must approve
via the API (SRS §8.4).

Milestone 4.5: Graph-aware suggestions — uses Knowledge Graph traversal
to identify attack paths, blast radius, and connected findings for
richer, context-aware recommendations.
"""

from __future__ import annotations

import json
from uuid import UUID, uuid4

from app.domain.entities import Asset, Finding, PlannedAction
from app.domain.exceptions import (
    PlannedActionExpiredError,
    PlannedActionNotApprovableError,
    PlannedActionNotFoundError,
)
from app.domain.llm_provider import LLMMessage, LLMProvider
from app.domain.repositories import (
    AIContextMemoryRepository,
    AssetRepository,
    FindingRepository,
    GraphRepository,
    PlannedActionRepository,
)
from app.domain.value_objects import GraphNodeType, PlannedActionStatus


class PlannerService:
    """
    AI Planner that suggests next actions based on project state.

    Per SRS §8.4: the AI never touches the plugin execution path directly.
    It emits PlannedAction objects with status=pending_review; a human must
    approve; only then does the Workflow Engine enqueue it.

    Milestone 4.5: When a GraphRepository is available, the planner
    enriches suggestions with graph-derived intelligence (blast radius,
    attack paths, connected findings).
    """

    def __init__(
        self,
        planned_action_repo: PlannedActionRepository,
        finding_repo: FindingRepository,
        asset_repo: AssetRepository,
        context_memory_repo: AIContextMemoryRepository,
        llm_provider: LLMProvider | None = None,
        graph_repo: GraphRepository | None = None,
    ) -> None:
        self._action_repo = planned_action_repo
        self._finding_repo = finding_repo
        self._asset_repo = asset_repo
        self._context_memory_repo = context_memory_repo
        self._llm = llm_provider
        self._graph_repo = graph_repo

    async def suggest(
        self,
        project_id: UUID,
        created_by: UUID | None = None,
    ) -> list[PlannedAction]:
        """
        Generate action suggestions for a project.

        When an LLM provider is available, uses AI to generate contextual
        suggestions. Otherwise falls back to heuristic-based suggestions
        derived from current project state.
        """
        findings = await self._finding_repo.list_for_project(project_id, limit=50)
        assets = await self._asset_repo.list_for_project(project_id, limit=50)

        if self._llm is not None:
            suggestions = await self._suggest_with_llm(project_id, findings, assets)
        elif self._graph_repo is not None:
            suggestions = await self._suggest_graph_enriched(
                project_id, findings, assets
            )
        else:
            suggestions = self._suggest_heuristic(project_id, findings, assets)

        actions: list[PlannedAction] = []
        for suggestion in suggestions:
            raw_plugin = suggestion.get("plugin")
            plugin_val = str(raw_plugin) if isinstance(raw_plugin, str) else None
            action = PlannedAction(
                id=uuid4(),
                project_id=project_id,
                action_type=str(suggestion["action_type"]),
                title=str(suggestion["title"]),
                description=str(suggestion["description"]),
                justification=str(suggestion["justification"]),
                plugin=plugin_val,
                target_ids=[],
                plugin_config={},
                status=PlannedActionStatus.PENDING_REVIEW,
                created_by=created_by,
            )
            await self._action_repo.create(action)
            actions.append(action)

        return actions

    async def approve(
        self,
        action_id: UUID,
        approved_by: UUID,
    ) -> PlannedAction:
        """Human approves a planned action (SRS §8.4)."""
        action = await self._action_repo.get(action_id)
        if action is None:
            raise PlannedActionNotFoundError(action_id)

        if not action.is_approvable:
            raise PlannedActionNotApprovableError(action_id, action.status.value)

        if action.expires_at is not None:
            from datetime import UTC, datetime

            if datetime.now(UTC) > action.expires_at:
                action.status = PlannedActionStatus.EXPIRED
                await self._action_repo.update(action)
                raise PlannedActionExpiredError(action_id)

        from datetime import UTC, datetime

        action.status = PlannedActionStatus.APPROVED
        action.approved_by = approved_by
        action.approved_at = datetime.now(UTC)
        await self._action_repo.update(action)
        return action

    async def reject(
        self,
        action_id: UUID,
        rejected_by: UUID,
        reason: str = "",
    ) -> PlannedAction:
        """Human rejects a planned action."""
        action = await self._action_repo.get(action_id)
        if action is None:
            raise PlannedActionNotFoundError(action_id)

        if not action.is_approvable:
            raise PlannedActionNotApprovableError(action_id, action.status.value)

        action.status = PlannedActionStatus.REJECTED
        action.rejection_reason = reason
        await self._action_repo.update(action)
        return action

    async def get(self, action_id: UUID) -> PlannedAction:
        action = await self._action_repo.get(action_id)
        if action is None:
            raise PlannedActionNotFoundError(action_id)
        return action

    async def list_for_project(
        self,
        project_id: UUID,
        status: PlannedActionStatus | None = None,
        limit: int = 20,
    ) -> list[PlannedAction]:
        return await self._action_repo.list_for_project(
            project_id, status=status, limit=limit
        )

    async def _suggest_with_llm(
        self,
        project_id: UUID,
        findings: list[Finding],
        assets: list[Asset],
    ) -> list[dict[str, object]]:
        """Use LLM to generate contextual suggestions."""
        findings_summary = "\n".join(
            f"- {f.title} (severity: {f.severity.value}, status: {f.status.value})"
            for f in findings[:20]
        ) or "No findings yet."

        assets_summary = "\n".join(
            f"- {a.value} (type: {a.asset_type.value})"
            for a in assets[:20]
        ) or "No assets discovered yet."

        prompt = (
            "You are a security testing planner. Based on the current project state, "
            "suggest up to 3 next actions for the tester.\n\n"
            f"Current findings:\n{findings_summary}\n\n"
            f"Current assets:\n{assets_summary}\n\n"
            "Respond with a JSON array of objects, each with keys: "
            "action_type, title, description, justification, plugin (optional), "
            "target_ids (optional list of strings).\n"
            "action_type should be one of: scan, recon, correlate, investigate."
        )

        messages = [LLMMessage(role="user", content=prompt)]

        try:
            assert self._llm is not None
            response = await self._llm.complete(messages)
            raw: list[object] = json.loads(response.content)
            if isinstance(raw, list):
                result: list[dict[str, object]] = [
                    s for s in raw[:3] if isinstance(s, dict)
                ]
                return result
        except Exception:
            pass

        return self._suggest_heuristic(project_id, findings, assets)

    def _suggest_heuristic(
        self,
        project_id: UUID,
        findings: list[Finding],
        assets: list[Asset],
    ) -> list[dict[str, object]]:
        suggestions: list[dict[str, object]] = []

        if not assets:
            suggestions.append({
                "action_type": "recon",
                "title": "Run initial reconnaissance",
                "description": "No assets discovered yet. Run nmap.",
                "justification": (
                    "Asset inventory is empty; initial recon "
                    "is needed before deeper scanning."
                ),
                "plugin": "nmap",
            })
        elif not findings:
            suggestions.append({
                "action_type": "scan",
                "title": "Run vulnerability scan",
                "description": (
                    f"Found {len(assets)} assets but no findings. "
                    "Run a vulnerability scan."
                ),
                "justification": (
                    "Assets exist but no vulnerabilities "
                    "have been identified yet."
                ),
                "plugin": "nmap",
            })
        else:
            high_findings = [f for f in findings if f.severity.value in ("high", "critical")]
            if high_findings:
                suggestions.append({
                    "action_type": "investigate",
                    "title": (
                        f"Investigate {len(high_findings)} "
                        "high/critical findings"
                    ),
                    "description": (
                        f"There are {len(high_findings)} high or "
                        "critical severity findings that need investigation."
                    ),
                    "justification": (
                        "High-severity findings should be "
                        "prioritized for investigation."
                    ),
                })

        return suggestions[:3]

    async def _suggest_graph_enriched(
        self,
        project_id: UUID,
        findings: list[Finding],
        assets: list[Asset],
    ) -> list[dict[str, object]]:
        """Graph-enriched suggestion generation.

        Uses Knowledge Graph to identify blast radius, attack paths,
        and connected finding clusters for richer recommendations.
        """
        suggestions: list[dict[str, object]] = []

        if self._graph_repo is None:
            return self._suggest_heuristic(project_id, findings, assets)

        all_nodes = await self._graph_repo.list_nodes_for_project(project_id)
        asset_nodes = [
            n for n in all_nodes if n.node_type == GraphNodeType.ASSET
        ]
        finding_nodes = [
            n for n in all_nodes if n.node_type == GraphNodeType.FINDING
        ]

        for fn in finding_nodes:
            blast = await self._graph_repo.blast_radius(
                project_id, fn.id, max_depth=3
            )
            affected = [
                n
                for n in blast
                if n.node_type == GraphNodeType.ASSET
            ]
            if len(affected) >= 3:
                suggestions.append({
                    "action_type": "investigate",
                    "title": (
                        f"Investigate finding with blast radius "
                        f"of {len(affected)} assets"
                    ),
                    "description": (
                        f"Finding '{fn.label}' has {len(affected)} assets "
                        "in its blast radius. Prioritize investigation."
                    ),
                    "justification": (
                        "Graph analysis shows this finding impacts "
                        "multiple downstream assets."
                    ),
                })

        if not suggestions and asset_nodes and finding_nodes:
            for an in asset_nodes[:5]:
                for fn in finding_nodes[:5]:
                    path = await self._graph_repo.shortest_path(
                        fn.id, an.id, 4
                    )
                    if path and len(path) >= 3:
                        suggestions.append({
                            "action_type": "scan",
                            "title": (
                                f"Scan target reachable from "
                                f"finding: {an.label}"
                            ),
                            "description": (
                                f"Graph analysis reveals an attack path "
                                f"from a finding to '{an.label}' "
                                f"({len(path)} hops)."
                            ),
                            "justification": (
                                "Attack path detected via graph traversal."
                            ),
                            "plugin": "nmap",
                        })
                        break
                if suggestions:
                    break

        base_suggestions = self._suggest_heuristic(
            project_id, findings, assets
        )
        for s in base_suggestions:
            if not any(
                existing["title"] == s["title"] for existing in suggestions
            ):
                suggestions.append(s)

        return suggestions[:3]
