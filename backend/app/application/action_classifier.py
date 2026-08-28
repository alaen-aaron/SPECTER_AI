"""Deterministic action-category policy for the autonomous cycle (M7.4 Phase 2).

The planner's output is UNTRUSTED in two senses: (1) it may be produced
by an LLM, and (2) even deterministic proposals must be classified by a
policy the operator controls. This module is that policy — every input
to `classify()` is a post-validation fact (accepted?, plugin, risk), and
no AI/LLM output feeds the decision directly.
"""

from __future__ import annotations

from app.domain.value_objects import ActionCategory

# Production default: only plugins a human operator has already reviewed
# as trivially-safe deterministic liveness/recon checks are auto-eligible.
DEFAULT_AUTO_ELIGIBLE_PLUGINS: frozenset[str] = frozenset({"ping"})

# Risk levels a plugin may carry and still be auto-eligible.
# The planner's deterministic synthesizer marks ping liveness as "low";
# anything ambiguous or riskier must wait for a human.
_AUTO_ELIGIBLE_RISK_LEVELS: frozenset[str] = frozenset({"low"})


class ActionClassificationPolicy:
    """Decides the `ActionCategory` of a validated planner proposal.

    Conservative by construction — no probabilistic inputs:

    - proposals the deterministic validator did NOT accept -> CATEGORY_0
      (blocked: never executed, never queued for human approval)
    - validator-accepted actions on the auto-eligible allow-list AND
      carrying a low risk level -> CATEGORY_2 (eligible for controlled
      autonomous execution under the bounded run)
    - everything else validator-accepted -> CATEGORY_1 (human approval
      required; the bounded cycle pauses at AWAITING_APPROVAL)
    """

    def __init__(
        self,
        auto_eligible_plugins: frozenset[str] = DEFAULT_AUTO_ELIGIBLE_PLUGINS,
    ) -> None:
        self._auto_eligible = frozenset(auto_eligible_plugins)

    @property
    def auto_eligible_plugins(self) -> frozenset[str]:
        return self._auto_eligible

    def classify(
        self,
        *,
        accepted: bool,
        plugin: str | None,
        risk_level: str | None,
    ) -> ActionCategory:
        if not accepted:
            return ActionCategory.CATEGORY_0
        if (
            plugin is not None
            and plugin in self._auto_eligible
            and (risk_level or "low") in _AUTO_ELIGIBLE_RISK_LEVELS
        ):
            return ActionCategory.CATEGORY_2
        return ActionCategory.CATEGORY_1