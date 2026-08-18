"""
Katana plugin — web crawler and spider.

Runs `katana -u <target> -silent` for URL discovery and web crawling
with JavaScript rendering support.
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
        "-jc",
        "-d",
        "-timeout",
        "-retry",
        "-json",
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


class KatanaPlugin(SubprocessPlugin):
    """Web crawler and spider using katana."""

    def name(self) -> str:
        return "katana"

    def description(self) -> str:
        return (
            "Web crawler and spider that discovers URLs and endpoints, "
            "with optional JavaScript rendering support."
        )

    def validate_config(self, config: dict[str, Any]) -> None:
        target = self._validate_required_field(config, "target")
        if not _validate_target(target):
            raise InvalidPluginConfigError(
                self.name(), f"'{target}' is not a valid URL or domain"
            )

        depth = config.get("depth")
        if depth is not None and (not isinstance(depth, int) or depth < 0):
                raise InvalidPluginConfigError(
                    self.name(), "'depth' must be a non-negative integer"
                )

        self._validate_flag_list(config, "flags", _ALLOWED_FLAGS)

    def execute(self, config: dict[str, Any], timeout_seconds: int) -> PluginResult:
        target = str(config["target"])
        flags: list[str] = list(config.get("flags", []))
        depth = config.get("depth")

        command = [
            "katana",
            "-u",
            target,
            *flags,
        ]
        if depth is not None:
            command.extend(["-d", str(depth)])

        extra: dict[str, Any] = {"flags": flags}
        if depth is not None:
            extra["depth"] = str(depth)
        return self._execute_subprocess(
            command, timeout_seconds, target=target, extra_metadata=extra
        )

    def capability(self) -> PluginCapability:
        return PluginCapability(
            input_asset_types=frozenset({"url", "domain"}),
            output_asset_types=frozenset({"url", "service"}),
            produces_findings=True,
            requires_host=True,
            requires_open_ports=False,
        )

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            version="1.0.0",
            author="SPECTER Team",
            category=PluginCategory.RECONNAISSANCE,
            tags=frozenset({"web", "crawler", "spider", "url-discovery"}),
            required_binaries=frozenset({"katana"}),
            description_long=(
                "Next-generation crawling and spidering framework from ProjectDiscovery."
            ),
            timeout_default_seconds=120,
            timeout_max_seconds=600,
        )

    def supports_target_type(self, target_type: str) -> bool:
        return target_type in ("url", "domain")
