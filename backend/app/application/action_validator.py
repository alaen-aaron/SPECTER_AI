"""
Deterministic AI-action validation (M7.2).

Sits between the AI Planner and the existing execution path. Every
AI-proposed action passes through `ActionProposalValidator.validate`
BEFORE it can be persisted as approvable or executed:

    AI proposal -> schema shape -> forbidden keys -> plugin registered
      -> plugin policy (allow-lists) -> targets exist & in-project
      -> Scope Guard -> duplicate detection -> executor constraints

Every check is recorded; a proposal is accepted only when all checks
pass. The validator is purely deterministic — same input, same verdict
— and never trusts anything produced by an LLM.

Security invariants enforced here:
- The AI cannot invent execution mechanisms: any config key that could
  carry a command/shell/container directive is rejected outright.
- The AI cannot select targets outside the project: target rows must
  exist and belong to `project_id`, then Scope Guard re-validates them.
- The AI cannot bypass plugin allow-lists: config validation is
  delegated to the same `PluginManager.validate` used for human scans.
- The AI cannot weaken M7.1 isolation: runner mode is reported, never
  negotiated; executor configuration is read-only from here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from app.domain.entities import PlannedAction
from app.domain.exceptions import (
    DomainError,
    InvalidPluginConfigError,
    PluginNotFoundError,
)
from app.domain.repositories import (
    PlannedActionRepository,
    TargetRepository,
)
from app.domain.value_objects import PlannedActionStatus

# Only these action types may ever reach the execution path. Advisory
# suggestions ("investigate", "correlate") stay human-only.
ACTIONABLE_ACTION_TYPES = frozenset({"scan", "recon"})

# Any proposal carrying one of these keys — at the top level of its
# plugin_config — is rejected without further inspection. This is a
# deny-by-default tripwire: legitimate plugins never need any of these
# because commands are built by plugins themselves from allow-listed
# config fields.
FORBIDDEN_CONFIG_KEYS = frozenset(
    {
        "command",
        "commands",
        "shell",
        "cmd",
        "argv",
        "args_file",
        "script",
        "script_path",
        "exec",
        "executable",
        "binary",
        "bin_path",
        "interpreter",
        "env",
        "env_file",
        "environment",
        "mounts",
        "volumes",
        "network_mode",
        "networks",
        "cap_add",
        "cap_drop",
        "privileged",
        "image",
        "entrypoint",
        "user",
        "pid",
        "workdir",
    }
)

MAX_TITLE_LENGTH = 500
MAX_TEXT_LENGTH = 4000
MAX_CONFIG_JSON_LENGTH = 8192
MAX_TARGETS_PER_ACTION = 32

_DUPLICATE_STATUSES = frozenset(
    {
        PlannedActionStatus.PENDING_REVIEW,
        PlannedActionStatus.APPROVED,
        PlannedActionStatus.EXECUTED,
    }
)

RUNNER_EXECUTOR = "executor"
RUNNER_SUBPROCESS_FALLBACK = "subprocess-fallback"


@dataclass(frozen=True, slots=True)
class ValidationCheck:
    """One named, deterministic check and its outcome."""

    name: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ProposalValidation:
    """Full result of validating one AI-proposed action."""

    accepted: bool
    checks: tuple[ValidationCheck, ...]
    runner_mode: str

    @property
    def failed_reasons(self) -> list[str]:
        return [f"{c.name}: {c.detail}" for c in self.checks if not c.passed]


class PluginPolicyValidator(Protocol):
    """Satisfied by `PluginManager` (kept as a Protocol so the
    application layer never imports plugin classes directly)."""

    def validate(self, plugin_name: str, config: dict[str, Any]) -> None: ...


class PluginAvailabilityLookup(Protocol):
    """Satisfied by `PluginRegistry`."""

    def get(self, plugin_name: str) -> Any: ...


def _canonical_config(config: dict[str, Any]) -> str:
    return json.dumps(config, sort_keys=True, default=str)


class ActionProposalValidator:
    def __init__(
        self,
        policy_validator: PluginPolicyValidator,
        plugin_lookup: PluginAvailabilityLookup,
        target_repository: TargetRepository,
        action_repository: PlannedActionRepository,
        scope_guard: Any,
        executor_enabled: bool,
        executor_image: str,
    ) -> None:
        self._policy = policy_validator
        self._registry = plugin_lookup
        self._targets = target_repository
        self._actions = action_repository
        self._scope_guard = scope_guard
        self._executor_enabled = executor_enabled
        self._executor_image = executor_image

    async def validate_proposal(
        self,
        project_id: UUID,
        action_type: str,
        plugin: str | None,
        target_ids: list[UUID],
        plugin_config: dict[str, Any],
        title: str = "",
        exclude_action_id: UUID | None = None,
    ) -> ProposalValidation:
        checks: list[ValidationCheck] = []

        # 1. Shape — only actionable types, bounded sizes.
        checks.append(self._check_shape(action_type, plugin, target_ids, plugin_config, title))

        # 2. Forbidden keys — reject command/container escape attempts.
        if plugin_config:
            found = sorted(
                k for k in plugin_config if str(k).lower() in FORBIDDEN_CONFIG_KEYS
            )
            checks.append(
                ValidationCheck(
                    "forbidden_keys",
                    not found,
                    f"config carries forbidden key(s): {found}"
                    if found
                    else "no forbidden keys",
                )
            )
        else:
            checks.append(ValidationCheck("forbidden_keys", False, "missing plugin_config"))

        # Short-circuit remaining structural checks on failure of the
        # first two — nothing downstream is meaningful for garbage.
        if not all(c.passed for c in checks):
            return self._result(checks)

        assert plugin is not None  # narrowing: _check_shape passed

        # 3. Registered plugin.
        try:
            self._registry.get(plugin)
            checks.append(ValidationCheck("plugin_registered", True, plugin))
        except PluginNotFoundError:
            checks.append(
                ValidationCheck("plugin_registered", False, f"unknown plugin '{plugin}'")
            )
            return self._result(checks)

        # 4. Plugin policy — same allow-list validation as human scans.
        try:
            self._policy.validate(plugin, dict(plugin_config))
            checks.append(ValidationCheck("plugin_policy", True, "config accepted"))
        except InvalidPluginConfigError as exc:
            checks.append(ValidationCheck("plugin_policy", False, str(exc)))
            return self._result(checks)

        # 5. Targets exist and belong to this project (tenant isolation).
        bad_targets: list[str] = []
        for tid in target_ids:
            target = await self._targets.get_by_id(tid)
            if target is None:
                bad_targets.append(f"{tid}:not-found")
            elif target.project_id != project_id:
                bad_targets.append(f"{tid}:foreign-project")
        checks.append(
            ValidationCheck(
                "targets_in_project",
                not bad_targets,
                "; ".join(bad_targets) if bad_targets else "all targets belong to project",
            )
        )

        # 6. Scope Guard — temporal authorization + in-scope enforcement.
        try:
            await self._scope_guard.validate_targets(project_id, list(target_ids))
            checks.append(ValidationCheck("scope_guard", True, "authorized"))
        except DomainError as exc:
            checks.append(ValidationCheck("scope_guard", False, str(exc)))

        # 7. Duplicate-action prevention.
        duplicate = await self._find_duplicate(
            project_id, plugin, target_ids, plugin_config, exclude_action_id
        )
        checks.append(
            ValidationCheck(
                "duplicate_action",
                duplicate is None,
                f"duplicate of action {duplicate}"
                if duplicate
                else "no pending/approved/executed equivalent",
            )
        )

        # 8. Executor constraints (M7.1 boundary intact; mode reported).
        if self._executor_enabled:
            mode_ok = bool(self._executor_image)
            detail = f"mode={RUNNER_EXECUTOR} image={'configured' if mode_ok else 'MISSING'}"
        else:
            mode_ok = True
            detail = f"mode={RUNNER_SUBPROCESS_FALLBACK} (EXECUTOR_ENABLED=false)"
        checks.append(ValidationCheck("executor_constraints", mode_ok, detail))

        return self._result(checks)

    async def validate_action_entity(
        self, action: PlannedAction
    ) -> ProposalValidation:
        return await self.validate_proposal(
            project_id=action.project_id,
            action_type=action.action_type,
            plugin=action.plugin,
            target_ids=list(action.target_ids),
            plugin_config=dict(action.plugin_config),
            title=action.title,
            exclude_action_id=action.id,
        )

    @property
    def runner_mode(self) -> str:
        return RUNNER_EXECUTOR if self._executor_enabled else RUNNER_SUBPROCESS_FALLBACK

    async def _find_duplicate(
        self,
        project_id: UUID,
        plugin: str,
        target_ids: list[UUID],
        plugin_config: dict[str, Any],
        exclude_action_id: UUID | None = None,
    ) -> UUID | None:
        recent = await self._actions.list_for_project(project_id, limit=50)
        wanted = (frozenset(target_ids), _canonical_config(dict(plugin_config)))
        for existing in recent:
            if exclude_action_id is not None and existing.id == exclude_action_id:
                continue  # never flag an action as a duplicate of itself
            if existing.plugin != plugin:
                continue
            if existing.status not in _DUPLICATE_STATUSES:
                continue
            have = (
                frozenset(existing.target_ids),
                _canonical_config(dict(existing.plugin_config)),
            )
            if have == wanted:
                return existing.id
        return None

    @staticmethod
    def _check_shape(
        action_type: str,
        plugin: str | None,
        target_ids: list[UUID],
        plugin_config: dict[str, Any],
        title: str,
    ) -> ValidationCheck:
        problems: list[str] = []
        if action_type not in ACTIONABLE_ACTION_TYPES:
            problems.append(f"non-actionable action_type '{action_type}'")
        if not plugin or not isinstance(plugin, str) or len(plugin) > 100:
            problems.append("plugin must be a non-empty string <=100 chars")
        if not target_ids or len(target_ids) > MAX_TARGETS_PER_ACTION:
            problems.append(f"target_ids must contain 1..{MAX_TARGETS_PER_ACTION} ids")
        if title and len(title) > MAX_TITLE_LENGTH:
            problems.append(f"title exceeds {MAX_TITLE_LENGTH} chars")
        if not isinstance(plugin_config, dict):
            problems.append("plugin_config must be an object")
        else:
            try:
                size = len(json.dumps(plugin_config, default=str))
            except (TypeError, ValueError):
                size = -1
            if size < 0:
                problems.append("plugin_config is not JSON-serializable")
            elif size > MAX_CONFIG_JSON_LENGTH:
                problems.append(
                    f"plugin_config exceeds {MAX_CONFIG_JSON_LENGTH} chars"
                )
        return ValidationCheck(
            "proposal_shape", not problems, "; ".join(problems) or "well-formed"
        )

    @staticmethod
    def _result(checks: list[ValidationCheck]) -> ProposalValidation:
        # Derive runner mode from the last check if present, else default.
        runner = RUNNER_SUBPROCESS_FALLBACK
        for c in checks:
            if c.name == "executor_constraints":
                runner = (
                    RUNNER_EXECUTOR
                    if c.detail.startswith("mode=executor")
                    else RUNNER_SUBPROCESS_FALLBACK
                )
        return ProposalValidation(
            accepted=all(c.passed for c in checks),
            checks=tuple(checks),
            runner_mode=runner,
        )
