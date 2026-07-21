"""
Normalizer registry (Milestone 4A).

Maps plugin names to their `ToolOutputNormalizer` implementations. The
ExecutionEngine looks up a normalizer by plugin name after every
successful invocation; if no normalizer is registered, raw output is
still persisted but `normalized_payload` is an empty dict.
"""

from __future__ import annotations

from app.plugins.normalizer import ToolOutputNormalizer


class NormalizerRegistry:
    """Not a singleton by construction — same pattern as PluginRegistry."""

    def __init__(self) -> None:
        self._normalizers: dict[str, ToolOutputNormalizer] = {}

    def register(self, normalizer: ToolOutputNormalizer) -> None:
        self._normalizers[normalizer.plugin_name] = normalizer

    def get(self, plugin_name: str) -> ToolOutputNormalizer | None:
        return self._normalizers.get(plugin_name)

    def list(self) -> list[ToolOutputNormalizer]:
        return list(self._normalizers.values())


# Process-wide registry instance.
normalizer_registry = NormalizerRegistry()
