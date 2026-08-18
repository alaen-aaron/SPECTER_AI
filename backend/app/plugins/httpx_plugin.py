"""
HTTPX plugin — HTTP probing and technology detection.

Runs `httpx -l <input> -silent -json` for web technology fingerprinting,
status code detection, and HTTP header analysis.
"""

from __future__ import annotations

from typing import Any

from app.domain.exceptions import InvalidPluginConfigError
from app.plugins.base import PluginCapability, PluginCategory, PluginMetadata, PluginResult
from app.plugins.subprocess_base import SubprocessPlugin


class HttpxPlugin(SubprocessPlugin):
    """HTTP probing and technology fingerprinting."""

    def name(self) -> str:
        return "httpx"

    def description(self) -> str:
        return "HTTP probing, technology detection, and web fingerprinting."

    def validate_config(self, config: dict[str, Any]) -> None:
        target = config.get("target")
        urls = config.get("urls")
        if not target and not urls:
            raise InvalidPluginConfigError(
                self.name(),
                "config must include 'target' (single URL/host) or 'urls' (list)",
            )

    def execute(self, config: dict[str, Any], timeout_seconds: int) -> PluginResult:
        target = str(config.get("target", ""))
        command = [
            "httpx",
            "-silent",
            "-json",
            "-timeout", str(min(timeout_seconds, 60)),
        ]
        if target:
            command.extend(["-u", target])
        elif "urls" in config:
            urls = config["urls"]
            if isinstance(urls, list):
                command.extend(["-l", "-"])
                # Write URLs to stdin would need more complex handling;
                # for now, pass as -u for each
                for url in urls[:100]:  # limit to prevent command line overflow
                    command.extend(["-u", str(url)])

        return self._execute_subprocess(command, timeout_seconds, target=target)

    def capability(self) -> PluginCapability:
        return PluginCapability(
            input_asset_types=frozenset({"domain", "subdomain", "host", "url"}),
            output_asset_types=frozenset({"service", "technology", "url"}),
            produces_findings=False,
            requires_host=True,
            requires_open_ports=False,
        )

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            version="1.0.0",
            author="SPECTER Team",
            category=PluginCategory.RECONNAISSANCE,
            tags=frozenset({"web", "http", "technology", "fingerprint"}),
            required_binaries=frozenset({"httpx"}),
            description_long="HTTP probing and technology fingerprinting.",
            timeout_default_seconds=60,
            timeout_max_seconds=300,
        )
