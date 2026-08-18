"""
SQLMap plugin — SQL injection detection and exploitation.

Runs `sqlmap -u <target> --batch` with allow-listed flags for level,
risk, threads, and timeout. Dangerous flags for OS shell access, privilege
escalation, and file read/write are never permitted.
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
        "--batch",
        "--random-agent",
        "--level",
        "--risk",
        "--threads",
        "--timeout",
    }
)

_DANGEROUS_FLAGS: frozenset[str] = frozenset(
    {
        "--os-shell",
        "--os-pwn",
        "--priv-esc",
        "--file-write",
        "--file-read",
    }
)


class SqlmapPlugin(SubprocessPlugin):
    """SQL injection detection and exploitation using sqlmap."""

    def name(self) -> str:
        return "sqlmap"

    def description(self) -> str:
        return (
            "Automated SQL injection detection and exploitation tool. "
            "Tests URL parameters for SQL injection vulnerabilities."
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
                    f"flag(s) not permitted (security): {disallowed}",
                )

        self._validate_flag_list(config, "flags", _ALLOWED_FLAGS)

        level = config.get("level")
        if level is not None and (not isinstance(level, int) or not (1 <= level <= 5)):
            raise InvalidPluginConfigError(
                self.name(), "'level' must be an integer between 1 and 5"
            )

        risk = config.get("risk")
        if risk is not None and (not isinstance(risk, int) or not (1 <= risk <= 3)):
            raise InvalidPluginConfigError(
                self.name(), "'risk' must be an integer between 1 and 3"
            )

    def execute(self, config: dict[str, Any], timeout_seconds: int) -> PluginResult:
        target = str(config["target"])
        flags: list[str] = list(config.get("flags", []))
        level = config.get("level", 1)
        risk = config.get("risk", 1)

        command = [
            "sqlmap",
            "-u",
            target,
            "--batch",
            "--random-agent",
            "--level",
            str(level),
            "--risk",
            str(risk),
            *flags,
        ]

        return self._execute_subprocess(
            command, timeout_seconds, target=target, extra_metadata={"level": level, "risk": risk}
        )

    def capability(self) -> PluginCapability:
        return PluginCapability(
            input_asset_types=frozenset({"url"}),
            output_asset_types=frozenset({"service", "vulnerability"}),
            produces_findings=True,
            requires_host=True,
            requires_open_ports=False,
        )

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            version="1.0.0",
            author="SPECTER Team",
            category=PluginCategory.VULNERABILITY,
            tags=frozenset({"sql", "injection", "database"}),
            required_binaries=frozenset({"sqlmap"}),
            description_long="Automatic SQL injection detection and exploitation tool.",
            timeout_default_seconds=180,
            timeout_max_seconds=600,
        )

    def supports_target_type(self, target_type: str) -> bool:
        return target_type == "url"
