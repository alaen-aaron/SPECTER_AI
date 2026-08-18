"""
Nuclei plugin — template-based vulnerability scanner.

Runs `nuclei -u <target>` with allow-listed flags for severity filtering,
template tags, and output format. The `-update-templates` and `-headless`
flags are never permitted as they could download arbitrary code or launch
browser processes.
"""

from __future__ import annotations

from typing import Any

from app.domain.exceptions import InvalidPluginConfigError
from app.plugins.base import PluginCapability, PluginCategory, PluginMetadata, PluginResult
from app.plugins.subprocess_base import SubprocessPlugin

_ALLOWED_FLAGS: frozenset[str] = frozenset(
    {
        "-silent",
        "-jsonl",
        "-severity",
        "-tags",
        "-exclude-tags",
        "-nc",
        "-timeout",
        "-retries",
    }
)

_DANGEROUS_FLAGS: frozenset[str] = frozenset(
    {
        "-update-templates",
        "-headless",
    }
)


class NucleiPlugin(SubprocessPlugin):
    """Template-based vulnerability scanner using nuclei."""

    def name(self) -> str:
        return "nuclei"

    def description(self) -> str:
        return (
            "Template-based vulnerability scanner that detects known CVEs, "
            "misconfigurations, and web vulnerabilities using community templates."
        )

    def validate_config(self, config: dict[str, Any]) -> None:
        _target = self._validate_required_field(config, "target")

        flags = config.get("flags", [])
        if isinstance(flags, list):
            disallowed = [f for f in flags if f in _DANGEROUS_FLAGS]
            if disallowed:
                raise InvalidPluginConfigError(
                    self.name(),
                    f"flag(s) not permitted (security): {disallowed}",
                )

        self._validate_flag_list(config, "flags", _ALLOWED_FLAGS)

        templates = config.get("templates")
        if templates is not None and not isinstance(templates, str):
            raise InvalidPluginConfigError(
                self.name(), "'templates' must be a comma-separated string of template categories"
            )

        severity = config.get("severity")
        if severity is not None and not isinstance(severity, str):
            raise InvalidPluginConfigError(
                self.name(), "'severity' must be a string (e.g. 'critical,high,medium')"
            )

    def execute(self, config: dict[str, Any], timeout_seconds: int) -> PluginResult:
        target = str(config["target"])
        flags: list[str] = list(config.get("flags", []))
        templates = config.get("templates")
        severity = config.get("severity")

        command = [
            "nuclei",
            "-u",
            target,
            "-silent",
            "-jsonl",
            "-nc",
            *flags,
        ]

        if templates:
            command.extend(["-tags", str(templates)])
        if severity:
            command.extend(["-severity", str(severity)])

        meta = {"templates": templates, "severity": severity}
        return self._execute_subprocess(
            command, timeout_seconds, target=target, extra_metadata=meta
        )

    def capability(self) -> PluginCapability:
        return PluginCapability(
            input_asset_types=frozenset({"url", "domain", "host"}),
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
            tags=frozenset({"vulnerability", "templates", "cve", "web"}),
            required_binaries=frozenset({"nuclei"}),
            description_long="Fast template-based vulnerability scanner from ProjectDiscovery.",
            timeout_default_seconds=180,
            timeout_max_seconds=600,
        )

    def supports_target_type(self, target_type: str) -> bool:
        return target_type in ("url", "domain", "host")
