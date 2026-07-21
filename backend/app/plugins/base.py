"""
Plugin subsystem (Milestone 3; SRS §7 groundwork).

Every plugin is a subclass of `Plugin`, self-registering with the
module-level `registry` on import (see `registry.py`). Plugins invoke
external tools via `subprocess.run([...])` — list arguments, never
`shell=True`, always a timeout — this is the load-bearing security
property of this subsystem and every built-in plugin (`echo_plugin.py`,
`ping_plugin.py`, `nmap_plugin.py`) follows it without exception.

Scope note: this milestone runs plugins as validated subprocesses in
the API/worker process, not yet in the per-invocation ephemeral
containers the frozen SRS's full plugin architecture calls for
(§7.3). That's a real, larger infrastructure lift (container
orchestration from the Celery worker) which this milestone's spec
doesn't ask for — this is an interim, explicitly-flagged step, not a
replacement for §7.3's isolation model.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class PluginResult:
    """What every plugin execution returns, regardless of which tool it wraps."""

    success: bool
    stdout: str
    stderr: str
    exit_code: int | None
    artifacts: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class Plugin(ABC):
    """
    Abstract base for every scan plugin.

    `validate_config` is always called before `execute` (by
    `PluginManager`, never left to each plugin to remember) so a
    malformed request fails fast with a clear error instead of a
    half-executed subprocess.
    """

    @abstractmethod
    def name(self) -> str:
        """Unique, stable plugin identifier used for registry lookup."""

    @abstractmethod
    def description(self) -> str:
        """Human-readable summary shown in plugin listings."""

    @abstractmethod
    def validate_config(self, config: dict[str, Any]) -> None:
        """
        Raise `app.domain.exceptions.InvalidPluginConfigError` if
        `config` is unusable for this plugin. Must not perform any I/O
        (no subprocess, no filesystem, no network) — validation is
        pure so it can run safely ahead of execution, including from
        the API layer if ever needed for pre-flight checks.
        """

    @abstractmethod
    def execute(self, config: dict[str, Any], timeout_seconds: int) -> PluginResult:
        """
        Run the plugin. Must enforce `timeout_seconds` itself (e.g. via
        `subprocess.run(..., timeout=timeout_seconds)`) and must never
        raise for an ordinary tool failure (non-zero exit, timeout) —
        those are reported via `PluginResult(success=False, ...)`.
        Only truly exceptional conditions (e.g. the underlying binary
        isn't installed) should raise.
        """
