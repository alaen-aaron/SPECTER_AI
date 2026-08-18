"""
Dalfox plugin — XSS vulnerability scanner.

Runs `dalfox url <target> --format json` with allow-listed flags for
silence, skip body analysis, timeout, workers, and delay. Blind XSS
callback flags are never permitted.
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
        "--silence",
        "--format",
        "--skip-bav",
        "--timeout",
        "--worker",
        "--delay",
    }
)

_DANGEROUS_FLAGS: frozenset[str] = frozenset(
    {
        "--blind",
        "--callback",
        "--blind-payload",
    }
)


class DalfoxPlugin(SubprocessPlugin):
    """XSS vulnerability scanner using dalfox."""

    def name(self) -> str:
        return "dalfox"

    def description(self) -> str:
        return (
            "Powerful open-source XSS vulnerability scanner that detects "
            "reflected, stored, and DOM-based XSS vulnerabilities."
        )

    def validate_config(self, config: dict[str, Any]) -> None:
        target = self._validate_required_field(config, "target")
        if not _URL_PATTERN.match(target):
            raise InvalidPluginConfigError(
                self.name(), "'target' must be a valid URL (http:// or https://)"
            )

        flags = config.get("flags", [])
        if isinstance(flags, list):
            disallowed = [f for f in flags if f in _DANGEROUS_FLAGS]
            if disallowed:
                raise InvalidPluginConfigError(
                    self.name(),
                    f"flag(s) not permitted (security — blind XSS callbacks): {disallowed}",
                )

        self._validate_flag_list(config, "flags", _ALLOWED_FLAGS)

    def execute(self, config: dict[str, Any], timeout_seconds: int) -> PluginResult:
        target = str(config["target"])
        flags: list[str] = list(config.get("flags", []))

        command = [
            "dalfox",
            "url",
            target,
            "--format",
            "json",
            "--silence",
            *flags,
        ]

        return self._execute_subprocess(command, timeout_seconds, target=target)

    def capability(self) -> PluginCapability:
        return PluginCapability(
            input_asset_types=frozenset({"url"}),
            output_asset_types=frozenset({"vulnerability"}),
            produces_findings=True,
            requires_host=True,
            requires_open_ports=False,
        )

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            version="1.0.0",
            author="SPECTER Team",
            category=PluginCategory.VULNERABILITY,
            tags=frozenset({"xss", "web", "injection"}),
            required_binaries=frozenset({"dalfox"}),
            description_long="Open-source XSS vulnerability scanner with parameter analysis.",
            timeout_default_seconds=120,
            timeout_max_seconds=300,
        )

    def supports_target_type(self, target_type: str) -> bool:
        return target_type == "url"
