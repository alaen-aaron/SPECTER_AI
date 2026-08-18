"""
WhatWeb plugin — web technology fingerprinter.

Runs `whatweb <target> --color=never --log-json` for technology
detection and web service fingerprinting.
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
        "--color=never",
        "--log-json",
        "--verbose",
        "--no-errors",
    }
)


def _validate_target(value: str) -> bool:
    for target_type in (TargetType.URL, TargetType.DOMAIN):
        try:
            validate_target_value(value, target_type)
            return True
        except Exception:  # noqa: BLE001 - trying the next type
            continue
    return False


class WhatwebPlugin(SubprocessPlugin):
    """Web technology fingerprinting using WhatWeb."""

    def name(self) -> str:
        return "whatweb"

    def description(self) -> str:
        return (
            "Web technology fingerprinter that identifies CMS, frameworks, "
            "libraries, web servers, and other technologies on target URLs."
        )

    def validate_config(self, config: dict[str, Any]) -> None:
        target = self._validate_required_field(config, "target")
        if not _validate_target(target):
            raise InvalidPluginConfigError(
                self.name(), f"'{target}' is not a valid URL or domain"
            )
        self._validate_flag_list(config, "flags", _ALLOWED_FLAGS)

    def execute(self, config: dict[str, Any], timeout_seconds: int) -> PluginResult:
        target = str(config["target"])
        flags: list[str] = list(config.get("flags", []))
        command = [
            "whatweb",
            target,
            "--color=never",
            *flags,
        ]
        extra: dict[str, Any] = {"flags": flags}
        return self._execute_subprocess(
            command, timeout_seconds, target=target, extra_metadata=extra
        )

    def capability(self) -> PluginCapability:
        return PluginCapability(
            input_asset_types=frozenset({"url", "domain"}),
            output_asset_types=frozenset({"technology", "service"}),
            produces_findings=True,
            requires_host=True,
            requires_open_ports=False,
        )

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            version="1.0.0",
            author="SPECTER Team",
            category=PluginCategory.INFORMATION_GATHERING,
            tags=frozenset({"web", "technology", "fingerprint"}),
            required_binaries=frozenset({"whatweb"}),
            description_long=(
                "Next-generation web scanner that identifies technologies "
                "used by websites."
            ),
            timeout_default_seconds=120,
            timeout_max_seconds=600,
        )

    def supports_target_type(self, target_type: str) -> bool:
        return target_type in ("url", "domain")
