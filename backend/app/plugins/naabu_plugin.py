"""
Naabu plugin — fast port scanner.

Runs `naabu -host <target> -silent` for fast TCP port scanning
with optional port specification and CDN exclusion.
"""

from __future__ import annotations

import re
from typing import Any

from app.domain.exceptions import InvalidPluginConfigError
from app.domain.target_validation import validate_target_value
from app.domain.value_objects import TargetType
from app.plugins.base import PluginCapability, PluginCategory, PluginMetadata, PluginResult
from app.plugins.subprocess_base import SubprocessPlugin

_PORTS_PATTERN = re.compile(r"^[0-9]+(-[0-9]+)?(,[0-9]+(-[0-9]+)?)*$")

_ALLOWED_FLAGS: frozenset[str] = frozenset(
    {
        "-silent",
        "-top-ports",
        "-Pn",
        "-exclude-cdn",
        "-verbose",
        "-json",
    }
)


def _validate_target(value: str) -> bool:
    for target_type in (TargetType.IP, TargetType.DOMAIN, TargetType.CIDR):
        try:
            validate_target_value(value, target_type)
            return True
        except Exception:  # noqa: BLE001 - trying the next type
            continue
    return False


class NaabuPlugin(SubprocessPlugin):
    """Fast TCP port scanner using naabu."""

    def name(self) -> str:
        return "naabu"

    def description(self) -> str:
        return (
            "Fast port scanner that discovers open TCP ports on targets. "
            "Supports CDN exclusion and top-ports selection."
        )

    def validate_config(self, config: dict[str, Any]) -> None:
        target = self._validate_required_field(config, "target")
        if not _validate_target(target):
            raise InvalidPluginConfigError(
                self.name(), f"'{target}' is not a valid IP, domain, or CIDR"
            )

        ports = config.get("ports")
        if ports is not None and (not isinstance(ports, str) or not _PORTS_PATTERN.match(ports)):
                raise InvalidPluginConfigError(
                    self.name(),
                    f"'{ports}' is not a valid ports specification "
                    "(expected digits/commas/dashes only, e.g. '80,443' or '1-1000')",
                )

        self._validate_flag_list(config, "flags", _ALLOWED_FLAGS)

    def execute(self, config: dict[str, Any], timeout_seconds: int) -> PluginResult:
        target = str(config["target"])
        flags: list[str] = list(config.get("flags", []))
        ports = config.get("ports")

        command = [
            "naabu",
            "-host",
            target,
            *flags,
        ]
        if ports is not None:
            command.extend(["-ports", str(ports)])

        extra: dict[str, Any] = {"flags": flags}
        if ports is not None:
            extra["ports"] = str(ports)
        return self._execute_subprocess(
            command, timeout_seconds, target=target, extra_metadata=extra
        )

    def capability(self) -> PluginCapability:
        return PluginCapability(
            input_asset_types=frozenset({"host", "domain", "cidr"}),
            output_asset_types=frozenset({"host", "port", "service"}),
            produces_findings=True,
            requires_host=True,
            requires_open_ports=False,
        )

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            version="1.0.0",
            author="SPECTER Team",
            category=PluginCategory.SCANNING,
            tags=frozenset({"ports", "fast", "tcp"}),
            required_binaries=frozenset({"naabu"}),
            description_long="Fast TCP port scanner from ProjectDiscovery.",
            timeout_default_seconds=120,
            timeout_max_seconds=600,
        )

    def supports_target_type(self, target_type: str) -> bool:
        return target_type in ("ip", "domain", "cidr")
