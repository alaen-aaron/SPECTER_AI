"""
DNSX plugin — DNS resolver and lookup tool.

Runs `dnsx -l <target> -silent` for DNS resolution and record lookup
across subdomains and domains.
"""

from __future__ import annotations

from typing import Any

from app.domain.exceptions import InvalidPluginConfigError
from app.domain.target_validation import validate_target_value
from app.domain.value_objects import TargetType
from app.plugins.base import PluginCapability, PluginCategory, PluginMetadata, PluginResult
from app.plugins.subprocess_base import SubprocessPlugin

_ALLOWED_FLAGS: frozenset[str] = frozenset(
    {
        "-silent",
        "-a",
        "-aaaa",
        "-cname",
        "-mx",
        "-ns",
        "-txt",
        "-resp",
        "-verbose",
    }
)


def _validate_target(value: str) -> bool:
    for target_type in (TargetType.DOMAIN, TargetType.IP):
        try:
            validate_target_value(value, target_type)
            return True
        except Exception:  # noqa: BLE001 - trying the next type
            continue
    return False


class DnsxPlugin(SubprocessPlugin):
    """DNS resolver and lookup using dnsx."""

    def name(self) -> str:
        return "dnsx"

    def description(self) -> str:
        return (
            "DNS resolver and record lookup. Performs A, AAAA, CNAME, MX, NS, "
            "and TXT record queries against domains and subdomains."
        )

    def validate_config(self, config: dict[str, Any]) -> None:
        target = self._validate_required_field(config, "target")
        if not _validate_target(target):
            raise InvalidPluginConfigError(
                self.name(), f"'{target}' is not a valid domain or IP address"
            )
        self._validate_flag_list(config, "flags", _ALLOWED_FLAGS)

    def execute(self, config: dict[str, Any], timeout_seconds: int) -> PluginResult:
        target = str(config["target"])
        flags: list[str] = list(config.get("flags", []))
        command = [
            "dnsx",
            "-l",
            target,
            *flags,
        ]
        extra: dict[str, Any] = {"flags": flags}
        return self._execute_subprocess(
            command, timeout_seconds, target=target, extra_metadata=extra
        )

    def capability(self) -> PluginCapability:
        return PluginCapability(
            input_asset_types=frozenset({"subdomain", "domain"}),
            output_asset_types=frozenset({"host", "subdomain"}),
            produces_findings=True,
            requires_host=True,
            requires_open_ports=False,
        )

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            version="1.0.0",
            author="SPECTER Team",
            category=PluginCategory.RECONNAISSANCE,
            tags=frozenset({"dns", "resolution", "lookup"}),
            required_binaries=frozenset({"dnsx"}),
            description_long="DNS resolver and record lookup tool from ProjectDiscovery.",
            timeout_default_seconds=60,
            timeout_max_seconds=300,
        )

    def supports_target_type(self, target_type: str) -> bool:
        return target_type in ("domain", "ip")
