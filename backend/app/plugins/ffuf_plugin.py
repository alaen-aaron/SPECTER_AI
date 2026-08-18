"""
FFUF plugin — web fuzzer and directory brute-forcer.

Runs `ffuf -u <target> -w <wordlist>` with allow-listed flags for
match/filter codes, threads, and output format. Recursive fuzzing
and file-writing flags are never permitted.
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
        "-mc",
        "-fc",
        "-t",
        "-timeout",
        "-json",
        "-s",
        "-noninteractive",
    }
)

_DANGEROUS_FLAGS: frozenset[str] = frozenset(
    {
        "-recursion",
        "-recursion-depth",
        "-o",
        "-of",
        "-od",
    }
)


class FfufPlugin(SubprocessPlugin):
    """Web fuzzer and directory brute-forcer using ffuf."""

    def name(self) -> str:
        return "ffuf"

    def description(self) -> str:
        return (
            "Web fuzzer and directory brute-forcer. Sends HTTP requests with "
            "wordlist-derived paths and filters results by match/filter codes."
        )

    def validate_config(self, config: dict[str, Any]) -> None:
        target = self._validate_required_field(config, "target")
        if not _URL_PATTERN.match(target):
            raise InvalidPluginConfigError(
                self.name(), "'target' must be a valid URL (http:// or https://)"
            )

        wordlist = config.get("wordlist")
        if wordlist is None or not isinstance(wordlist, str) or not wordlist:
            msg = "config must include a non-empty string 'wordlist'"
            raise InvalidPluginConfigError(self.name(), msg)

        flags = config.get("flags", [])
        if isinstance(flags, list):
            disallowed = [f for f in flags if f in _DANGEROUS_FLAGS]
            if disallowed:
                raise InvalidPluginConfigError(
                    self.name(),
                    f"flag(s) not permitted (security): {disallowed}",
                )
        self._validate_flag_list(config, "flags", _ALLOWED_FLAGS)

    def execute(self, config: dict[str, Any], timeout_seconds: int) -> PluginResult:
        target = str(config["target"])
        wordlist = str(config["wordlist"])
        flags: list[str] = list(config.get("flags", []))

        command = [
            "ffuf",
            "-u",
            target,
            "-w",
            wordlist,
            "-noninteractive",
            "-s",
            *flags,
        ]

        return self._execute_subprocess(
            command, timeout_seconds, target=target, extra_metadata={"wordlist": wordlist}
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
            category=PluginCategory.VULNERABILITY,
            tags=frozenset({"web", "fuzzing", "directory", "brute-force"}),
            required_binaries=frozenset({"ffuf"}),
            description_long="Fast web fuzzer for directory and file brute-forcing.",
            timeout_default_seconds=120,
            timeout_max_seconds=300,
        )

    def supports_target_type(self, target_type: str) -> bool:
        return target_type in ("url", "domain")
