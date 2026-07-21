"""
Tool output normalizer framework (Milestone 4A).

A normalizer converts raw plugin stdout into a structured,
tool-agnostic `normalized_payload` dict. The domain layer defines the
Protocol; concrete parsers live in `plugins/normalizers/`. The
ExecutionEngine calls the normalizer after every successful plugin
invocation to populate `ToolResult` rows.
"""

from __future__ import annotations

from typing import Any, Protocol


class ToolOutputNormalizer(Protocol):
    """Structural interface — any callable with this shape satisfies it."""

    @property
    def plugin_name(self) -> str: ...

    def normalize(
        self,
        raw_stdout: str,
        raw_stderr: str,
        plugin_config: dict[str, Any],
    ) -> dict[str, Any]: ...
