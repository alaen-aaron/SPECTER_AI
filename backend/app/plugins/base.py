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

import shutil
from abc import ABC, abstractmethod
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, TypeVar


class PluginCategory(StrEnum):
    """Functional categories for plugin classification and workflow composition."""

    RECONNAISSANCE = "reconnaissance"
    SCANNING = "scanning"
    ENUMERATION = "enumeration"
    VULNERABILITY = "vulnerability"
    EXPLOITATION = "exploitation"
    INFORMATION_GATHERING = "information_gathering"
    BRUTE_FORCE = "brute_force"
    REPORTING = "reporting"
    UTILITY = "utility"


@dataclass(frozen=True, slots=True)
class PluginCapability:
    """
    Declares what a plugin consumes and produces.

    Used by the workflow engine to build dependency graphs and validate
    that plugin chains are coherent (e.g., a vuln scanner that requires
    open ports must be preceded by a port scanner that produces them).
    """

    input_asset_types: frozenset[str] = field(default_factory=frozenset)
    output_asset_types: frozenset[str] = field(default_factory=frozenset)
    produces_findings: bool = False
    requires_host: bool = True
    requires_open_ports: bool = False
    max_targets: int | None = 1

    def can_accept(self, available_asset_types: frozenset[str]) -> bool:
        """Check if this plugin can run given available asset types."""
        if not self.input_asset_types:
            return True
        return bool(self.input_asset_types & available_asset_types)

    def is_compatible_with(self, upstream: PluginCapability) -> bool:
        """Check if an upstream plugin's outputs can feed this plugin's inputs."""
        if not self.input_asset_types:
            return True
        if not upstream.output_asset_types:
            return True
        return bool(self.input_asset_types & upstream.output_asset_types)


@dataclass(frozen=True, slots=True)
class PluginMetadata:
    """
    Extended metadata for a plugin — versioning, dependencies, categories.

    Stored alongside the plugin instance in the registry for discovery,
    compatibility checking, and health monitoring.
    """

    version: str = "1.0.0"
    author: str = ""
    category: PluginCategory = PluginCategory.SCANNING
    tags: frozenset[str] = field(default_factory=frozenset)
    required_binaries: frozenset[str] = field(default_factory=frozenset)
    description_long: str = ""
    min_python_version: str = ""
    timeout_default_seconds: int = 120
    timeout_max_seconds: int = 600

    def check_binaries(self) -> list[str]:
        """Return list of missing required binaries (empty = all present)."""
        missing: list[str] = []
        for binary in self.required_binaries:
            if shutil.which(binary) is None:
                missing.append(binary)
        return missing

    def is_healthy(self) -> bool:
        """Check if all required binaries are available on PATH."""
        return len(self.check_binaries()) == 0


@dataclass(frozen=True, slots=True)
class PluginResult:
    """What every plugin execution returns, regardless of which tool it wraps."""

    success: bool
    stdout: str
    stderr: str
    exit_code: int | None
    artifacts: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class CommandRunner(Protocol):
    """
    Executes an already-validated plugin command in an isolated environment.

    Milestone 7.1: production uses `ExecutorHttpRunner` (worker → executor
    service → ephemeral Docker container). Tests and local development use
    `None` (plain subprocess in the current process). A plugin never decides
    which one is active — `PluginManager.run` installs the active runner as a
    context variable before invoking `plugin.execute`, and the subprocess
    helpers delegate to it when present.
    """

    def run(
        self,
        command: list[str],
        *,
        timeout_seconds: int,
        target: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> PluginResult: ...


_T = TypeVar("_T")

_active_runner: ContextVar[CommandRunner | None] = ContextVar(
    "specter_active_plugin_runner", default=None
)


def get_active_runner() -> CommandRunner | None:
    """Return the runner currently installed for this execution context."""
    return _active_runner.get()


def run_with_active_runner(
    runner: CommandRunner | None, func: Callable[[], _T]
) -> _T:
    """Run `func` with `runner` active for this context, restoring on exit."""
    token = _active_runner.set(runner)
    try:
        return func()
    finally:
        _active_runner.reset(token)


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

    # --- Capability & metadata (M5 defaults for backward compat) -----

    def capability(self) -> PluginCapability:
        """
        Declare input/output asset types and requirements.

        Override in subclasses for workflow composition. Defaults indicate
        a generic host-scanning plugin that produces findings.
        """
        return PluginCapability()

    def metadata(self) -> PluginMetadata:
        """
        Extended plugin metadata — version, category, required binaries.

        Override in subclasses. Defaults are minimal/healthy.
        """
        return PluginMetadata()

    def health_check(self) -> bool:
        """Quick health check — are all required binaries available?"""
        return self.metadata().is_healthy()

    def supports_target_type(self, target_type: str) -> bool:
        """Whether this plugin can scan the given target type."""
        return True
