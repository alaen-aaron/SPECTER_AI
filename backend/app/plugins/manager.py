"""
Plugin Manager (Milestone 3).

The only thing that actually invokes a plugin. Looks the plugin up in
the registry, always calls `validate_config` before `execute` (so a
plugin author can never forget to validate), and converts any
unexpected exception from `execute` into a well-formed failed
`PluginResult` rather than letting it propagate — a bug in one plugin
must never crash a scan's bookkeeping (the Scan row still needs to be
marked failed with a reason, not left stuck in `running` forever).
"""

from __future__ import annotations

from typing import Any

from app.domain.exceptions import InvalidPluginConfigError
from app.plugins.base import PluginResult
from app.plugins.registry import PluginRegistry


class PluginManager:
    def __init__(self, plugin_registry: PluginRegistry) -> None:
        self._registry = plugin_registry

    def validate(self, plugin_name: str, config: dict[str, Any]) -> None:
        """
        Look up the plugin and validate `config` without executing
        anything. Raises `PluginNotFoundError` or
        `InvalidPluginConfigError`. Used both internally by `run()` and
        by callers (e.g. `ScanService.create`) that want to fail fast
        on a bad request before persisting anything.
        """
        plugin = self._registry.get(plugin_name)  # raises PluginNotFoundError
        plugin.validate_config(config)  # raises InvalidPluginConfigError

    def run(self, plugin_name: str, config: dict[str, Any], timeout_seconds: int) -> PluginResult:
        """
        Raises `PluginNotFoundError` or `InvalidPluginConfigError` —
        both caller errors, distinct from a `PluginResult(success=False)`,
        which represents "the plugin ran but the underlying tool failed."
        """
        plugin = self._registry.get(plugin_name)
        plugin.validate_config(config)

        try:
            return plugin.execute(config, timeout_seconds)
        except InvalidPluginConfigError:
            raise
        except Exception as exc:  # noqa: BLE001 - a plugin bug must not crash the scan
            return PluginResult(
                success=False,
                stdout="",
                stderr=f"Plugin '{plugin_name}' raised an unexpected error: {exc}",
                exit_code=None,
                metadata={"plugin": plugin_name, "unexpected_error": type(exc).__name__},
            )
