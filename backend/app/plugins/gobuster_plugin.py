"""
Gobuster plugin — directory, DNS, and vhost brute-forcer.

Runs `gobuster <mode> -u/-d <target> -w <wordlist>` with allow-listed
flags for quiet output, threads, and timeout control.
"""

from __future__ import annotations

from typing import Any

from app.domain.exceptions import InvalidPluginConfigError
from app.plugins.base import PluginCapability, PluginCategory, PluginMetadata, PluginResult
from app.plugins.subprocess_base import SubprocessPlugin

_VALID_MODES: frozenset[str] = frozenset({"dir", "dns", "vhost"})

_ALLOWED_FLAGS: frozenset[str] = frozenset(
    {
        "-q",
        "-t",
        "-timeout",
        "-z",
        "--wildcard",
    }
)


class GobusterPlugin(SubprocessPlugin):
    """Directory, DNS, and vhost brute-forcer using gobuster."""

    def name(self) -> str:
        return "gobuster"

    def description(self) -> str:
        return (
            "Directory, DNS, and vhost brute-forcer. Discovers hidden paths, "
            "subdomains, and virtual hosts using wordlist-based attacks."
        )

    def validate_config(self, config: dict[str, Any]) -> None:
        _target = self._validate_required_field(config, "target")

        mode = config.get("mode")
        if not isinstance(mode, str) or mode not in _VALID_MODES:
            raise InvalidPluginConfigError(
                self.name(),
                f"'mode' must be one of: {sorted(_VALID_MODES)}",
            )

        wordlist = config.get("wordlist")
        if wordlist is None or not isinstance(wordlist, str) or not wordlist:
            raise InvalidPluginConfigError(
                self.name(),
                "config must include a non-empty string 'wordlist' (path to wordlist file)",
            )

        self._validate_flag_list(config, "flags", _ALLOWED_FLAGS)

    def execute(self, config: dict[str, Any], timeout_seconds: int) -> PluginResult:
        target = str(config["target"])
        mode = str(config["mode"])
        wordlist = str(config["wordlist"])
        flags: list[str] = list(config.get("flags", []))

        command = [
            "gobuster",
            mode,
        ]

        # dir and vhost modes use -u, dns mode uses -d
        if mode in ("dir", "vhost"):
            command.extend(["-u", target])
        else:
            command.extend(["-d", target])

        command.extend([
            "-w", wordlist,
            "-q",
            *flags,
        ])

        meta = {"mode": mode, "wordlist": wordlist}
        return self._execute_subprocess(
            command, timeout_seconds, target=target, extra_metadata=meta
        )

    def capability(self) -> PluginCapability:
        return PluginCapability(
            input_asset_types=frozenset({"url", "domain"}),
            output_asset_types=frozenset({"url", "service", "subdomain"}),
            produces_findings=True,
            requires_host=True,
            requires_open_ports=False,
        )

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            version="1.0.0",
            author="SPECTER Team",
            category=PluginCategory.ENUMERATION,
            tags=frozenset({"directory", "dns", "brute-force", "enumeration"}),
            required_binaries=frozenset({"gobuster"}),
            description_long="Directory, DNS, and vhost brute-forcing tool.",
            timeout_default_seconds=120,
            timeout_max_seconds=300,
        )

    def supports_target_type(self, target_type: str) -> bool:
        return target_type in ("url", "domain")
