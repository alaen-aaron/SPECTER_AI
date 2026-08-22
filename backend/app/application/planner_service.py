"""
Planner service (SRS §8.1, FR-7.1).

Given current project asset/finding state, proposes a ranked list of
next recon/scan actions with justification. Output: PlannedAction[]
with status=pending_review. Never auto-executed — a human must approve
via the API (SRS §8.4).

Milestone 4.5: Graph-aware suggestions — uses Knowledge Graph traversal
to identify attack paths, blast radius, and connected findings for
richer, context-aware recommendations.

Milestone 7.2: AI-driven planning & CONTROLLED execution.
`plan()` runs the full pipeline:

    security context -> risk-aware candidate ordering -> structured
    action proposals -> deterministic ActionProposalValidator ->
    (persist accepted proposals) -> decision audit

`execute_approved()` is the ONLY bridge from an approved PlannedAction
to the existing execution path — it delegates to the caller-supplied
launcher (`ScanService.create`), which re-runs Scope Guard and plugin
policy before dispatching through the M7.1 isolated executor. The AI
never reaches Celery, Docker, or the filesystem directly.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from app.application.action_validator import (
    ACTIONABLE_ACTION_TYPES,
    FORBIDDEN_CONFIG_KEYS,
    ProposalValidation,
)
from app.domain.entities import Asset, AuditLogEntry, Finding, PlannedAction, Target
from app.domain.exceptions import (
    ActionNotExecutableError,
    ActionRejectedByValidatorError,
    PlannedActionExpiredError,
    PlannedActionNotApprovableError,
    PlannedActionNotFoundError,
)
from app.domain.llm_provider import LLMMessage, LLMProvider
from app.domain.repositories import (
    AIContextMemoryRepository,
    AssetRepository,
    AuditLogRepository,
    FindingRepository,
    GraphRepository,
    PlannedActionRepository,
    ProjectRepository,
    TargetRepository,
)
from app.domain.value_objects import GraphNodeType, PlannedActionStatus, Severity


class ScanLauncher(Protocol):
    """
    Boundary to the EXISTING execution path. Satisfied by
    `ScanService.create` — the same entry point human scans use, which
    chains Scope Guard -> plugin policy -> persist -> Celery dispatch
    -> M7.1 isolated executor. The planner never dispatches directly.
    """

    async def __call__(
        self,
        project_id: UUID,
        plugin_name: str,
        plugin_config: dict[str, Any],
        target_ids: list[UUID],
        initiated_by: UUID,
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class PlanningContext:
    """Project-scoped security context handed to the planner.

    Built exclusively from repositories keyed by `project_id`, so the
    planner can never see another project's/org's data by construction.
    """

    project_id: UUID
    organization_id: UUID | None
    targets: tuple[Target, ...]
    assets: tuple[Asset, ...]
    findings: tuple[Finding, ...]
    recent_actions: tuple[PlannedAction, ...]

    def summary(self) -> dict[str, object]:
        return {
            "target_count": len(self.targets),
            "asset_count": len(self.assets),
            "finding_count": len(self.findings),
            "high_severity_findings": sum(
                1
                for f in self.findings
                if f.severity in (Severity.HIGH, Severity.CRITICAL)
            ),
            "recent_action_count": len(self.recent_actions),
        }


@dataclass(frozen=True, slots=True)
class ProposedAction:
    """A validated (or rejected) planner proposal returned by plan()."""

    action: PlannedAction
    validation: ProposalValidation
    persisted: bool


@dataclass(frozen=True, slots=True)
class GroundedProposal:
    """A raw suggestion normalized into concrete, executable-shaped
    fields — every target resolved to a project-owned Target entity and
    the config restricted to allow-listed plugin fields."""

    action_type: str
    plugin: str
    targets: tuple[Target, ...]
    plugin_config: dict[str, object]
    title: str
    description: str
    justification: str
    risk_level: str
    expected_value: str


@dataclass(frozen=True, slots=True)
class PlanOutcome:
    proposals: tuple[ProposedAction, ...]
    skipped_duplicates: int
    ungrounded: int
    stopped_because: str
    context_summary: dict[str, object]
    runner_mode: str


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
        target_repo: TargetRepository | None = None,
        project_repo: ProjectRepository | None = None,
        audit_repo: AuditLogRepository | None = None,
    ) -> None:
        self._action_repo = planned_action_repo
        self._finding_repo = finding_repo
        self._asset_repo = asset_repo
        self._context_memory_repo = context_memory_repo
        self._llm = llm_provider
        self._graph_repo = graph_repo
        self._target_repo = target_repo
        self._project_repo = project_repo
        self._audit_repo = audit_repo
        self._validator: Any = None

    def set_validator(self, validator: Any) -> None:
        """Wire the deterministic M7.2 validator (done by the DI layer)."""
        self._validator = validator

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
        for raw_suggestion in suggestions:
            # M7.2 AI-output security: treat every suggestion as untrusted
            # — whitelist keys, bound lengths, drop malformed entries.
            suggestion = self._sanitize_legacy_suggestion(raw_suggestion)
            if suggestion is None:
                continue
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

    # --- M7.2: AI-driven planning & controlled execution --------------------

    async def plan(
        self,
        project_id: UUID,
        created_by: UUID,
        objective: str = "",
        max_actions: int = 3,
        session_timeout_seconds: float = 15.0,
        cancelled_check: Callable[[], bool] | None = None,
    ) -> PlanOutcome:
        """
        M7.2 planning session: context -> proposals -> deterministic
        validation -> persist accepted -> audit every decision.

        Safety limits (no autonomous loops here — that is M7.4):
        - at most `max_actions` validated proposals per call
        - whole session bounded by `session_timeout_seconds`
        - cooperative cancellation via `cancelled_check`
        - in-session + repository-level duplicate prevention
        """
        if self._validator is None:
            raise RuntimeError(
                "PlannerService.plan() requires a wired ActionProposalValidator"
            )

        started = time.monotonic()
        context = await self._build_security_context(project_id)
        raw_suggestions = await self._raw_suggestions(context)

        proposals: list[ProposedAction] = []
        skipped_duplicates = 0
        ungrounded = 0
        stopped_because = "no_more_candidates"
        seen: set[tuple[str, frozenset[UUID], str]] = set()

        for suggestion in raw_suggestions:
            if len(proposals) >= max_actions:
                stopped_because = "max_actions"
                break
            if cancelled_check is not None and cancelled_check():
                stopped_because = "cancelled"
                break
            if (time.monotonic() - started) > session_timeout_seconds:
                stopped_because = "timeout"
                break

            grounded = self._ground_proposal(suggestion, context)
            if grounded is None:
                ungrounded += 1
                continue

            key = (
                grounded.plugin,
                frozenset(t.id for t in grounded.targets),
                json.dumps(grounded.plugin_config, sort_keys=True, default=str),
            )
            if key in seen:
                skipped_duplicates += 1
                continue
            seen.add(key)

            target_ids = [t.id for t in grounded.targets]
            validation = await self._validator.validate_proposal(
                project_id=project_id,
                action_type=grounded.action_type,
                plugin=grounded.plugin,
                target_ids=list(target_ids),
                plugin_config=dict(grounded.plugin_config),
                title=grounded.title,
            )

            action = PlannedAction(
                id=uuid4(),
                project_id=project_id,
                action_type=grounded.action_type,
                title=grounded.title,
                description=grounded.description,
                justification=grounded.justification,
                plugin=grounded.plugin,
                target_ids=list(target_ids),
                plugin_config=dict(grounded.plugin_config),
                status=PlannedActionStatus.PENDING_REVIEW,
                created_by=created_by,
                objective=objective or None,
                expected_value=grounded.expected_value or None,
                risk_level=grounded.risk_level,
            )

            persisted = False
            if validation.accepted:
                # Only SAFE proposals enter the human approval queue.
                await self._action_repo.create(action)
                persisted = True
            else:
                # Rejected proposals are never persisted as approvable;
                # they exist only in this response and the audit trail.
                action.rejection_reason = "; ".join(
                    validation.failed_reasons
                )[:2000]

            await self._audit_decision(
                organization_id=context.organization_id,
                actor_id=created_by,
                action_name="ai.planner.proposal",
                planned_action_id=action.id,
                details={
                    "accepted": validation.accepted,
                    "persisted": persisted,
                    "runner_mode": validation.runner_mode,
                    "failed_checks": validation.failed_reasons[:10],
                    "plugin": grounded.plugin,
                    "risk_level": grounded.risk_level,
                    "objective": objective or None,
                    "target_count": len(target_ids),
                },
            )

            proposals.append(
                ProposedAction(
                    action=action, validation=validation, persisted=persisted
                )
            )

        return PlanOutcome(
            proposals=tuple(proposals),
            skipped_duplicates=skipped_duplicates,
            ungrounded=ungrounded,
            stopped_because=stopped_because,
            context_summary=context.summary(),
            runner_mode=self._validator.runner_mode,
        )

    async def execute_approved(
        self,
        action_id: UUID,
        initiated_by: UUID,
        launch_scan: ScanLauncher,
        expected_project_id: UUID | None = None,
    ) -> tuple[PlannedAction, Any]:
        """
        The ONLY bridge from an approved AI proposal to execution.

        Requires status == APPROVED (human approval, SRS §8.4), then
        re-runs the full deterministic validator, then delegates to the
        caller-supplied launcher (`ScanService.create`) which enforces
        Scope Guard + plugin policy again before dispatching through the
        M7.1 isolated executor. No second execution engine exists.

        Tenant isolation: when `expected_project_id` is supplied (the
        API always supplies it from the authorized route context), an
        action belonging to any OTHER project is treated as not found —
        a member of project A can never execute (or even probe) project
        B's planned actions.

        Raises `ActionNotExecutableError` when the action was never
        approved, and `ActionRejectedByValidatorError` (after marking
        the action REJECTED) when re-validation fails at execute time.
        """
        if self._validator is None:
            raise RuntimeError(
                "PlannerService.execute_approved() requires a wired "
                "ActionProposalValidator"
            )

        action = await self.get(action_id)
        if (
            expected_project_id is not None
            and action.project_id != expected_project_id
        ):
            # Deliberately 404-shaped: never leak cross-project existence.
            raise PlannedActionNotFoundError(action_id)
        if action.status is not PlannedActionStatus.APPROVED:
            raise ActionNotExecutableError(action_id, action.status.value)

        validation = await self._validator.validate_action_entity(action)
        organization_id = await self._organization_for(action.project_id)

        if not validation.accepted:
            reasons = validation.failed_reasons
            action.status = PlannedActionStatus.REJECTED
            action.rejection_reason = "; ".join(reasons)[:2000]
            await self._action_repo.update(action)
            await self._audit_decision(
                organization_id=organization_id,
                actor_id=initiated_by,
                action_name="ai.action.execute_rejected",
                planned_action_id=action.id,
                details={"reasons": reasons[:10], "runner_mode": validation.runner_mode},
            )
            raise ActionRejectedByValidatorError(action_id, reasons)

        scan = await launch_scan(
            project_id=action.project_id,
            plugin_name=str(action.plugin),
            plugin_config=dict(action.plugin_config),
            target_ids=list(action.target_ids),
            initiated_by=initiated_by,
        )

        action.status = PlannedActionStatus.EXECUTED
        scan_id = getattr(scan, "id", None)
        if isinstance(scan_id, UUID):
            action.scan_id = scan_id
        await self._action_repo.update(action)

        await self._audit_decision(
            organization_id=organization_id,
            actor_id=initiated_by,
            action_name="ai.action.execute_started",
            planned_action_id=action.id,
            details={
                "scan_id": str(scan_id) if scan_id is not None else None,
                "plugin": action.plugin,
                "runner_mode": validation.runner_mode,
            },
        )
        return action, scan

    async def _build_security_context(self, project_id: UUID) -> PlanningContext:
        """Project-scoped context; other tenants are invisible here."""
        targets: tuple[Target, ...] = ()
        if self._target_repo is not None:
            targets = tuple(await self._target_repo.list_for_project(project_id))
        assets = tuple(await self._asset_repo.list_for_project(project_id, limit=100))
        findings = tuple(
            await self._finding_repo.list_for_project(project_id, limit=100)
        )
        recent_actions = tuple(await self._action_repo.list_for_project(project_id, limit=50))
        return PlanningContext(
            project_id=project_id,
            organization_id=await self._organization_for(project_id),
            targets=targets,
            assets=assets,
            findings=findings,
            recent_actions=recent_actions,
        )

    async def _raw_suggestions(
        self, context: PlanningContext
    ) -> list[dict[str, object]]:
        """
        Proposal sources, in order: LLM (when a provider is wired) then
        deterministic synthesis to fill remaining slots. Both paths are
        grounded against `context` before validation.
        """
        suggestions: list[dict[str, object]] = []

        if self._llm is not None:
            findings = list(context.findings)
            assets = list(context.assets)
            llm_raw = await self._suggest_with_llm(
                context.project_id, findings, assets
            )
            for raw in llm_raw:
                coerced = self._coerce_llm_proposal(raw, context)
                if coerced is not None:
                    suggestions.append(coerced)

        suggestions.extend(self._synthesize_deterministic(context))
        return suggestions

    def _synthesize_deterministic(
        self, context: PlanningContext
    ) -> list[dict[str, object]]:
        """
        Risk-prioritized, fully deterministic proposals:

        1. Targets referenced by high/critical findings -> nmap service
           enumeration (highest information value).
        2. Unscanned targets (no assets yet) -> nmap initial recon.
        3. Everything else -> ping liveness probe.
        """
        out: list[dict[str, object]] = []
        if not context.targets:
            return out

        high_asset_ids = {
            f.asset_id
            for f in context.findings
            if f.severity in (Severity.HIGH, Severity.CRITICAL) and f.asset_id
        }
        # A high finding "references" a target when it is linked to one of
        # the target's assets OR explicitly names the target value.
        # (Manual findings may lack asset links — FindingCreate has no
        # asset_id field — so the title/description is matched too.)
        high_values = {a.value for a in context.assets if a.id in high_asset_ids}
        high_texts: set[str] = set(high_values)
        for f in context.findings:
            if f.severity in (Severity.HIGH, Severity.CRITICAL):
                high_texts.add(f.title or "")
                if f.description:
                    high_texts.add(f.description)

        def has_high(t: Target) -> bool:
            return any(t.value in v for v in high_texts)

        def asset_count(t: Target) -> int:
            return sum(1 for a in context.assets if t.value in a.value)

        ordered = sorted(
            context.targets,
            key=lambda t: (not has_high(t), asset_count(t) > 0, str(t.value)),
        )

        for t in ordered:
            if has_high(t):
                out.append(
                    {
                        "action_type": "recon",
                        "plugin": "nmap",
                        "target_ids": [t.id],
                        "plugin_config": {
                            "target": t.value,
                            "ports": "1-1000",
                            "arguments": ["-Pn", "-sV"],
                        },
                        "title": f"Enumerate services on {t.value}",
                        "description": (
                            f"High-severity findings reference {t.value}; "
                            "enumerate its services to validate exposure."
                        ),
                        "justification": (
                            "Risk-prioritized: known high-severity "
                            "findings make service enumeration the "
                            "highest-value next step."
                        ),
                        "risk_level": "high",
                        "expected_value": (
                            f"Service/port inventory for {t.value} to "
                            "confirm or refute critical exposure."
                        ),
                    }
                )
            elif asset_count(t) == 0:
                out.append(
                    {
                        "action_type": "recon",
                        "plugin": "nmap",
                        "target_ids": [t.id],
                        "plugin_config": {
                            "target": t.value,
                            "ports": "1-1000",
                            "arguments": ["-Pn"],
                        },
                        "title": f"Initial reconnaissance of {t.value}",
                        "description": (
                            f"No assets discovered on {t.value} yet; "
                            "start with port discovery."
                        ),
                        "justification": (
                            "Asset inventory is empty for this target; "
                            "recon must precede deeper testing."
                        ),
                        "risk_level": "medium",
                        "expected_value": (
                            f"First port/service inventory for {t.value}."
                        ),
                    }
                )
            else:
                out.append(
                    {
                        "action_type": "recon",
                        "plugin": "ping",
                        "target_ids": [t.id],
                        "plugin_config": {"hostname": t.value},
                        "title": f"Liveness check for {t.value}",
                        "description": (
                            f"Verify {t.value} is reachable before "
                            "scheduling deeper scans."
                        ),
                        "justification": (
                            "Cheap liveness signal keeps later actions "
                            "from wasting executor capacity on dead hosts."
                        ),
                        "risk_level": "low",
                        "expected_value": f"Reachability status for {t.value}.",
                    }
                )
        return out

    def _coerce_llm_proposal(
        self, raw: object, context: PlanningContext
    ) -> dict[str, object] | None:
        """
        Treat LLM output as UNTRUSTED: whitelist keys only, bound string
        lengths, resolve targets strictly against project-owned targets
        (hallucinated ids/values are dropped), and refuse anything that
        smells like an execution mechanism. Returns None on any doubt —
        the deterministic synthesizer fills the slot instead.
        """
        if not isinstance(raw, dict):
            return None

        allowed_keys = {
            "action_type",
            "title",
            "description",
            "justification",
            "plugin",
            "target_ids",
            "plugin_config",
            "risk_level",
            "expected_value",
        }
        data = {k: raw[k] for k in allowed_keys if k in raw}

        config = data.get("plugin_config")
        if config is not None and not isinstance(config, dict):
            return None
        lowered = {str(k).lower() for k in (config or {})}
        if lowered & {k.lower() for k in FORBIDDEN_CONFIG_KEYS}:
            return None

        plugin = data.get("plugin")
        if not isinstance(plugin, str) or not plugin.strip():
            return None

        resolved: list[UUID] = []
        by_id = {str(t.id): t for t in context.targets}
        by_value = {t.value: t for t in context.targets}
        raw_targets = data.get("target_ids")
        if isinstance(raw_targets, list):
            for rt in raw_targets:
                match = by_id.get(str(rt)) or by_value.get(str(rt))
                if match is None:
                    return None  # hallucinated target -> drop entirely
                resolved.append(match.id)
        if not resolved:
            return None

        def _cap(key: str, fallback: str, limit: int = 500) -> str:
            value = data.get(key)
            if not isinstance(value, str) or not value.strip():
                return fallback
            return value.strip()[:limit]

        risk = data.get("risk_level")
        risk_level = str(risk) if risk in ("low", "medium", "high") else "medium"

        first_target = next(
            t for t in context.targets if t.id == resolved[0]
        )
        return {
            "action_type": "recon",
            "plugin": plugin.strip().lower(),
            "target_ids": resolved,
            "plugin_config": dict(config or {}),
            "title": _cap("title", f"{plugin.strip().lower()} on {first_target.value}"),
            "description": _cap("description", "LLM-proposed reconnaissance step.", 2000),
            "justification": _cap("justification", "Proposed by AI planner.", 2000),
            "risk_level": risk_level,
            "expected_value": _cap("expected_value", "", 2000),
        }

    def _ground_proposal(
        self,
        suggestion: dict[str, object],
        context: PlanningContext,
    ) -> GroundedProposal | None:
        """
        Normalize one raw suggestion into a fully-grounded proposal with
        concrete target entities and a safe default config when needed.
        Deterministic suggestions arrive pre-grounded; LLM suggestions go
        through `_coerce_llm_proposal` before reaching here.
        """
        plugin_raw = suggestion.get("plugin")
        plugin = (
            plugin_raw.strip().lower()
            if isinstance(plugin_raw, str)
            else ""
        )
        if not plugin:
            return None

        raw_targets = suggestion.get("target_ids") or []
        target_entities: list[Target] = []
        if isinstance(raw_targets, list):
            by_id = {str(t.id): t for t in context.targets}
            by_value = {t.value: t for t in context.targets}
            for rt in raw_targets:
                match = by_id.get(str(rt)) or by_value.get(str(rt))
                if match is not None:
                    target_entities.append(match)
        if not target_entities:
            return None

        config_raw = suggestion.get("plugin_config")
        config: dict[str, object]
        if isinstance(config_raw, dict) and config_raw:
            config = dict(config_raw)
        else:
            default = self._default_config_for(plugin, target_entities[0])
            if default is None:
                return None
            config = default

        action_type = str(suggestion.get("action_type") or "recon")
        if action_type not in ACTIONABLE_ACTION_TYPES:
            return None

        def _text(key: str, fallback: str, limit: int) -> str:
            value = suggestion.get(key)
            if not isinstance(value, str) or not value.strip():
                return fallback
            return value.strip()[:limit]

        risk_raw = suggestion.get("risk_level")
        risk_level = (
            str(risk_raw) if risk_raw in ("low", "medium", "high") else "medium"
        )

        return GroundedProposal(
            action_type=action_type,
            plugin=plugin,
            targets=tuple(target_entities),
            plugin_config=config,
            title=_text("title", f"{plugin} recon", 500),
            description=_text("description", "", 4000),
            justification=_text("justification", "", 4000),
            risk_level=risk_level,
            expected_value=_text("expected_value", "", 2000),
        )

    @staticmethod
    def _default_config_for(plugin: str, target: Target) -> dict[str, object] | None:
        """
        Safe per-plugin default configs built ONLY from registered
        allow-listed fields — never commands or container settings.
        """
        if plugin == "nmap":
            return {"target": target.value, "ports": "1-1000"}
        if plugin == "ping":
            return {"hostname": target.value}
        return None

    async def _organization_for(self, project_id: UUID) -> UUID | None:
        if self._project_repo is None:
            return None
        project = await self._project_repo.get_by_id(project_id)
        return project.organization_id if project else None

    async def _audit_decision(
        self,
        organization_id: UUID | None,
        actor_id: UUID | None,
        action_name: str,
        planned_action_id: UUID,
        details: dict[str, object],
    ) -> None:
        """Append-only decision audit via the EXISTING audit infra."""
        if self._audit_repo is None:
            return
        await self._audit_repo.add(
            AuditLogEntry(
                id=uuid4(),
                organization_id=organization_id,
                actor_id=actor_id,
                action=action_name,
                target_type="planned_action",
                target_id=planned_action_id,
                ip_address=None,
                created_at=datetime.now(UTC),
                before_state={},
                after_state=details,
            )
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

    @staticmethod
    def _sanitize_legacy_suggestion(raw: object) -> dict[str, object] | None:
        """
        Whitelist-and-bound a legacy suggestion dict (M7.2 AI-output
        security): only the four display fields plus an optional plugin
        survive; unknown keys are dropped, non-string values reject the
        whole entry, and lengths are capped. Nothing here is ever
        executed — suggest() output stays advisory-only.
        """
        if not isinstance(raw, dict):
            return None
        out: dict[str, object] = {}
        for key in ("action_type", "title", "description", "justification"):
            value = raw.get(key)
            if not isinstance(value, str) or not value.strip():
                return None
            out[key] = value.strip()[: (500 if key == "title" else 4000)]
        plugin = raw.get("plugin")
        out["plugin"] = (
            plugin.strip().lower()[:100] if isinstance(plugin, str) else None
        )
        return out

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
