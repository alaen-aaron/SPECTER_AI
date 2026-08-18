"""
Subfinder plugin — passive subdomain enumeration.

Runs `subfinder -d <target> -silent -oJ` for subdomain discovery
using multiple passive sources (crt.sh, VirusTotal, etc.).
"""

from __future__ import annotations

from typing import Any

from app.domain.exceptions import InvalidPluginConfigError
from app.domain.target_validation import validate_target_value
from app.domain.value_objects import TargetType
from app.plugins.base import PluginCapability, PluginCategory, PluginMetadata, PluginResult
from app.plugins.subprocess_base import SubprocessPlugin


class SubfinderPlugin(SubprocessPlugin):
    """Passive subdomain enumeration using multiple sources."""

    def name(self) -> str:
        return "subfinder"

    def description(self) -> str:
        return "Passive subdomain discovery using multiple enumeration sources."

    def validate_config(self, config: dict[str, Any]) -> None:
        target = self._validate_required_field(config, "target")
        try:
            validate_target_value(target, TargetType.DOMAIN)
        except Exception:
            raise InvalidPluginConfigError(
                self.name(), f"'{target}' is not a valid domain name"
            ) from None

    def execute(self, config: dict[str, Any], timeout_seconds: int) -> PluginResult:
        target = str(config["target"])
        command = [
            "subfinder",
            "-d", target,
            "-silent",
            "-oJ",
            "-timeout", str(min(timeout_seconds, 300)),
        ]
        return self._execute_subprocess(command, timeout_seconds, target=target)

    def capability(self) -> PluginCapability:
        return PluginCapability(
            input_asset_types=frozenset({"domain"}),
            output_asset_types=frozenset({"subdomain"}),
            produces_findings=False,
            requires_host=True,
            requires_open_ports=False,
        )

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            version="1.0.0",
            author="SPECTER Team",
            category=PluginCategory.RECONNAISSANCE,
            tags=frozenset({"subdomain", "dns", "passive", "enumeration"}),
            required_binaries=frozenset({"subfinder"}),
            description_long="Discover subdomains using passive DNS sources.",
            timeout_default_seconds=120,
            timeout_max_seconds=300,
        )

    def supports_target_type(self, target_type: str) -> bool:
        return target_type == "domain"
