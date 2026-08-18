"""
WPScan plugin — WordPress security scanner.

Runs `wpscan --url <target> --format json` for WordPress plugin, theme,
and core vulnerability detection. Only non-destructive flags are allow-listed.
"""

from __future__ import annotations

import re
from typing import Any

from app.domain.exceptions import InvalidPluginConfigError
from app.plugins.base import PluginCapability, PluginCategory, PluginMetadata, PluginResult
from app.plugins.subprocess_base import SubprocessPlugin

_URL_PATTERN = re.compile(r"^https?://", re.IGNORECASE)

_ALLOWED_FLAGS: frozenset[str] = frozenset(
    {
        "--format",
        "--no-banner",
        "--disable-tls-checks",
        "--throttle",
        "--timeout",
        "--random-user-agent",
    }
)


class WpscanPlugin(SubprocessPlugin):
    """WordPress security scanner using wpscan."""

    def name(self) -> str:
        return "wpscan"

    def description(self) -> str:
        return (
            "WordPress security scanner that enumerates plugins, themes, "
            "and detects known vulnerabilities and misconfigurations."
        )

    def validate_config(self, config: dict[str, Any]) -> None:
        target = self._validate_required_field(config, "target")
        if not _URL_PATTERN.match(target):
            raise InvalidPluginConfigError(
                self.name(), "'target' must be a valid URL (http:// or https://)"
            )

        self._validate_flag_list(config, "flags", _ALLOWED_FLAGS)

    def execute(self, config: dict[str, Any], timeout_seconds: int) -> PluginResult:
        target = str(config["target"])
        flags: list[str] = list(config.get("flags", []))

        command = [
            "wpscan",
            "--url",
            target,
            "--format",
            "json",
            "--no-banner",
            "--random-user-agent",
            *flags,
        ]

        return self._execute_subprocess(
            command, timeout_seconds, target=target, extra_metadata={"format": "json"}
        )

    def capability(self) -> PluginCapability:
        return PluginCapability(
            input_asset_types=frozenset({"url", "domain"}),
            output_asset_types=frozenset({"technology", "vulnerability", "service"}),
            produces_findings=True,
            requires_host=True,
            requires_open_ports=False,
        )

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            version="1.0.0",
            author="SPECTER Team",
            category=PluginCategory.VULNERABILITY,
            tags=frozenset({"wordpress", "cms", "vulnerability", "plugins"}),
            required_binaries=frozenset({"wpscan"}),
            description_long="WordPress security scanner for plugins and themes.",
            timeout_default_seconds=180,
            timeout_max_seconds=600,
        )

    def supports_target_type(self, target_type: str) -> bool:
        return target_type in ("url", "domain")
