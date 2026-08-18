"""
Plugin registry (Milestone 3, extended Milestone 5).

A process-wide, in-memory catalog of available plugins. Plugins
self-register by calling `registry.register(...)` at module import
time (see the bottom of each built-in plugin module) — the registry
itself has no knowledge of which plugins exist until they're imported,
which happens once, at application/worker startup, via
`app.plugins.builtin` (see that module's docstring).

Milestone 5 extensions:
- Category and tag-based filtering for workflow composition
- Health check methods for plugin availability monitoring
- Compatibility validation between plugins (upstream → downstream)
- Version tracking and duplicate detection
"""

from __future__ import annotations

from app.domain.exceptions import PluginNotFoundError
from app.plugins.base import Plugin, PluginCapability, PluginCategory, PluginMetadata


class PluginRegistry:
    """Not a singleton by construction — `app.plugins.registry` below is
    the one instance every part of the app is expected to share, but
    tests are free to construct an isolated `PluginRegistry()` too."""

    def __init__(self) -> None:
        self._plugins: dict[str, Plugin] = {}

    def register(self, plugin: Plugin) -> None:
        """Idempotent: re-registering the same name overwrites the previous entry."""
        self._plugins[plugin.name()] = plugin

    def unregister(self, name: str) -> None:
        self._plugins.pop(name, None)

    def get(self, name: str) -> Plugin:
        plugin = self._plugins.get(name)
        if plugin is None:
            raise PluginNotFoundError(name)
        return plugin

    def list(self) -> list[Plugin]:
        return list(self._plugins.values())

    # --- M5: Enhanced registry methods ---

    def get_metadata(self, name: str) -> PluginMetadata:
        """Return metadata for a plugin by name."""
        return self.get(name).metadata()

    def get_capability(self, name: str) -> PluginCapability:
        """Return capability declaration for a plugin by name."""
        return self.get(name).capability()

    def list_by_category(self, category: PluginCategory) -> list[Plugin]:
        """Return all plugins in a given category."""
        return [
            p for p in self._plugins.values()
            if p.metadata().category == category
        ]

    def list_by_tag(self, tag: str) -> list[Plugin]:
        """Return all plugins with a specific tag."""
        return [
            p for p in self._plugins.values()
            if tag in p.metadata().tags
        ]

    def find_compatible(self, upstream_name: str) -> list[Plugin]:
        """
        Find all plugins that can consume the output of `upstream_name`.

        Used by the workflow engine to discover valid next steps in a
        plugin chain (e.g., nmap output → vuln scanner input).

        Only returns plugins that declare specific input_asset_types that
        overlap with the upstream's output_asset_types. Plugins with no
        declared inputs are excluded — they're universally runnable but
        don't form meaningful dependency chains.
        """
        upstream = self.get(upstream_name)
        upstream_cap = upstream.capability()
        compatible: list[Plugin] = []
        for plugin in self._plugins.values():
            if plugin.name() == upstream_name:
                continue
            cap = plugin.capability()
            if not cap.input_asset_types:
                continue  # no declared inputs — skip universally-runnable plugins
            if cap.is_compatible_with(upstream_cap):
                compatible.append(plugin)
        return compatible

    def check_health(self) -> dict[str, bool]:
        """Return health status for all registered plugins."""
        return {
            name: plugin.health_check()
            for name, plugin in self._plugins.items()
        }

    def get_healthy_plugins(self) -> list[Plugin]:
        """Return only plugins whose required binaries are available."""
        return [
            p for p in self._plugins.values()
            if p.health_check()
        ]

    def get_unhealthy_plugins(self) -> list[Plugin]:
        """Return plugins with missing required binaries."""
        return [
            p for p in self._plugins.values()
            if not p.health_check()
        ]

    def validate_compatibility(
        self, upstream_name: str, downstream_name: str
    ) -> tuple[bool, str]:
        """
        Check if downstream can accept upstream's output.

        Returns (is_compatible, reason).
        """
        upstream = self.get(upstream_name)
        downstream = self.get(downstream_name)
        up_cap = upstream.capability()
        down_cap = downstream.capability()
        if down_cap.is_compatible_with(up_cap):
            return True, "compatible"
        missing = down_cap.input_asset_types - up_cap.output_asset_types
        return (
            False,
            f"downstream requires {missing} but upstream only produces "
            f"{up_cap.output_asset_types}",
        )

    def get_by_names(self, names: list[str]) -> list[Plugin]:
        """Return plugins by list of names, raising PluginNotFoundError for missing."""
        return [self.get(name) for name in names]


# The process-wide registry instance. Built-in plugins register
# themselves onto this exact object when `app.plugins.builtin` is
# imported (see that module).
registry = PluginRegistry()
