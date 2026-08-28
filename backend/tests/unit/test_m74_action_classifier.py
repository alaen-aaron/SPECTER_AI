"""
M7.4 Phase 2 — Action Classification Policy Tests.

The classifier translates post-validation facts into an ActionCategory:
rejected -> CATEGORY_0 (blocked), allow-listed + low risk -> CATEGORY_2
(controlled auto-execution), everything else -> CATEGORY_1 (human).
"""

from __future__ import annotations

from app.application.action_classifier import ActionClassificationPolicy
from app.domain.value_objects import ActionCategory


def test_rejected_never_auto() -> None:
    policy = ActionClassificationPolicy()
    assert (
        policy.classify(accepted=False, plugin="ping", risk_level="low")
        is ActionCategory.CATEGORY_0
    )


def test_allowlisted_low_risk_is_auto_eligible() -> None:
    policy = ActionClassificationPolicy()
    assert (
        policy.classify(accepted=True, plugin="ping", risk_level="low")
        is ActionCategory.CATEGORY_2
    )


def test_allowlisted_high_risk_requires_human() -> None:
    policy = ActionClassificationPolicy()
    assert (
        policy.classify(accepted=True, plugin="ping", risk_level="high")
        is ActionCategory.CATEGORY_1
    )


def test_non_allowlisted_validated_action_requires_human() -> None:
    policy = ActionClassificationPolicy()
    assert (
        policy.classify(accepted=True, plugin="nmap", risk_level="low")
        is ActionCategory.CATEGORY_1
    )


def test_unknown_plugin_requires_human() -> None:
    policy = ActionClassificationPolicy()
    assert (
        policy.classify(accepted=True, plugin=None, risk_level=None)
        is ActionCategory.CATEGORY_1
    )


def test_custom_policy_can_allowlist_plugins() -> None:
    policy = ActionClassificationPolicy(auto_eligible_plugins=frozenset({"nmap"}))
    assert (
        policy.classify(accepted=True, plugin="nmap", risk_level="low")
        is ActionCategory.CATEGORY_2
    )
    assert policy.auto_eligible_plugins == frozenset({"nmap"})