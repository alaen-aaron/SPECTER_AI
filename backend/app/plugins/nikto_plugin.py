"""
Nikto plugin — web server scanner.

Runs `nikto -h <target>` with JSON output. Nikto is chatty; the plugin
uses `-Format json` and `-output /dev/stdout` (where available) for
structured output. Only non-destructive flags are allow-listed.
"""

from __future__ import annotations

from typing import Any

from app.plugins.base import PluginCapability, PluginCategory, PluginMetadata, PluginResult
from app.plugins.subprocess_base import SubprocessPlugin

_ALLOWED_FLAGS: frozenset[str] = frozenset(
    {
        "-Format",
        "-Tuning",
        "-Timeout",
        "-maxtime",
    }
)


class NiktoPlugin(SubprocessPlugin):
    """Web server scanner using nikto."""

    def name(self) -> str:
        return "nikto"

    def description(self) -> str:
        return (
            "Web server scanner that checks for dangerous files/programs, "
            "outdated server versions, and configuration issues."
        )

    def validate_config(self, config: dict[str, Any]) -> None:
        self._validate_required_field(config, "target")
        self._validate_flag_list(config, "flags", _ALLOWED_FLAGS)

    def execute(self, config: dict[str, Any], timeout_seconds: int) -> PluginResult:
        target = str(config["target"])
        flags: list[str] = list(config.get("flags", []))

        command = [
            "nikto",
            "-h",
            target,
            "-Format",
            "json",
            "-output",
            "/dev/stdout",
            *flags,
        ]

        return self._execute_subprocess(
            command, timeout_seconds, target=target, extra_metadata={"format": "json"}
        )

    def capability(self) -> PluginCapability:
        return PluginCapability(
            input_asset_types=frozenset({"url", "host", "domain"}),
            output_asset_types=frozenset({"service", "technology"}),
            produces_findings=True,
            requires_host=True,
            requires_open_ports=False,
        )

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            version="1.0.0",
            author="SPECTER Team",
            category=PluginCategory.VULNERABILITY,
            tags=frozenset({"web", "server", "misconfiguration"}),
            required_binaries=frozenset({"nikto"}),
            description_long="Open-source web server scanner for misconfigurations.",
            timeout_default_seconds=180,
            timeout_max_seconds=600,
        )

    def supports_target_type(self, target_type: str) -> bool:
        return target_type in ("url", "host", "domain")
