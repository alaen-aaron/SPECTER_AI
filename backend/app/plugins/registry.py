"""
Plugin registry (Milestone 3).

A process-wide, in-memory catalog of available plugins. Plugins
self-register by calling `registry.register(...)` at module import
time (see the bottom of each built-in plugin module) — the registry
itself has no knowledge of which plugins exist until they're imported,
which happens once, at application/worker startup, via
`app.plugins.builtin` (see that module's docstring).
"""

from __future__ import annotations

from app.domain.exceptions import PluginNotFoundError
from app.plugins.base import Plugin


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


# The process-wide registry instance. Built-in plugins register
# themselves onto this exact object when `app.plugins.builtin` is
# imported (see that module).
registry = PluginRegistry()
