"""Echo plugin (Milestone 3) — no subprocess, no shell, pure demonstration."""

from __future__ import annotations

from typing import Any

from app.plugins.base import Plugin, PluginCapability, PluginCategory, PluginMetadata, PluginResult


class EchoPlugin(Plugin):
    """Returns a fixed greeting. Exists to prove the execution pipeline
    (ScanService -> ExecutionEngine -> PluginManager -> Plugin -> Scan
    row update) end-to-end without depending on any external tool."""

    def name(self) -> str:
        return "echo"

    def description(self) -> str:
        return "Demonstration plugin that returns a fixed greeting. No shell execution."

    def validate_config(self, config: dict[str, Any]) -> None:
        # No required fields — this plugin accepts (and ignores) any config.
        return None

    def execute(self, config: dict[str, Any], timeout_seconds: int) -> PluginResult:
        return PluginResult(
            success=True,
            stdout="Hello from SPECTER",
            stderr="",
            exit_code=0,
            artifacts=[],
            metadata={"plugin": "echo"},
        )

    def capability(self) -> PluginCapability:
        return PluginCapability(
            input_asset_types=frozenset(),
            output_asset_types=frozenset(),
            produces_findings=False,
            requires_host=False,
            requires_open_ports=False,
        )

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            version="1.0.0",
            author="SPECTER Team",
            category=PluginCategory.UTILITY,
            tags=frozenset({"demo", "testing"}),
            required_binaries=frozenset(),
            description_long="Demonstration plugin for testing the execution pipeline.",
        )
