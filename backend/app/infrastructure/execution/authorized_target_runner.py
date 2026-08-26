"""
Authorized-target runner decorator (M7.3 Phase 2).

The ONLY M7.1-adjacent change approved for this phase, and it is
strictly additive: it does not redesign `ExecutorHttpRunner`, does not
touch Docker flags, iptables semantics, or the target-only policy.

Problem it solves
-----------------
Web plugins legitimately carry URL targets in their config/argv
(``http://172.18.0.10:3000``), but the executor's network policy layer
expects an IP/CIDR/domain identity and cannot resolve a URL string.
Deriving policy targets from that raw plugin string would also mean the
network allow-list follows whatever the plugin config says rather than
what was actually authorized.

What this changes
-----------------
The ExecutionEngine wraps the real runner once per scan with THIS
decorator, injecting the scan's REGISTERED, Scope-Guard-validated
target identities into runner metadata:

    registered target -> Scope Guard -> metadata["authorized_policy_targets"]
                                      -> executor policy

`ExecutorHttpRunner` reads that key (small additive read) to build its
``targets[]`` payload; the URL stays ONLY inside the plugin command
argv. If the key is absent (tests, subprocess fallback) behavior is
byte-for-byte identical to before. The allowed network can only ever
shrink-or-equal what was registered — never broaden.
"""

from __future__ import annotations

from typing import Any

from app.plugins.base import CommandRunner, PluginResult


class AuthorizedTargetRunner:
    """CommandRunner decorator carrying authorized policy identities."""

    def __init__(self, inner: CommandRunner, authorized_targets: list[str]) -> None:
        self._inner = inner
        self._authorized = [t for t in authorized_targets if t]

    def run(
        self,
        command: list[str],
        *,
        timeout_seconds: int,
        target: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> PluginResult:
        meta = dict(metadata or {})
        meta.setdefault("authorized_policy_targets", list(self._authorized))
        return self._inner.run(
            command,
            timeout_seconds=timeout_seconds,
            target=target,
            metadata=meta,
        )
