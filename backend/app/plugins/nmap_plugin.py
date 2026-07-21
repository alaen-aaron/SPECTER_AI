"""
Nmap plugin (Milestone 3) — subprocess list args only, no shell,
mandatory timeout, and a strict allow-list on the `arguments` field.

`target`/`ports` are validated against known-safe formats before ever
reaching `subprocess.run`. `arguments` is the most dangerous field —
nmap has flags that write arbitrary files (`-oN`/`-oX`/`-oG`/`-oA`),
run arbitrary NSE scripts (`--script`), or read arbitrary target lists
from disk (`-iL`) — so rather than trying to blocklist "the dangerous
ones," this plugin only permits a small, explicit allow-list of
value-less scan-behavior flags. Anything not on that list is rejected
before a subprocess is ever spawned.
"""

from __future__ import annotations

import re
import subprocess
from typing import Any

from app.domain.exceptions import InvalidPluginConfigError
from app.domain.target_validation import validate_target_value
from app.domain.value_objects import TargetType
from app.plugins.base import Plugin, PluginResult

_PORTS_PATTERN = re.compile(r"^[0-9]+(-[0-9]+)?(,[0-9]+(-[0-9]+)?)*$")

# Deliberately only value-less behavior flags — no flag here can write
# a file, execute a script, or read target lists from disk.
_ALLOWED_ARGUMENTS = frozenset(
    {
        "-sV",  # service/version detection
        "-sC",  # default NSE script set (safe, read-only category)
        "-sS",  # TCP SYN scan (requires privileges; nmap falls back if unavailable)
        "-sT",  # TCP connect scan
        "-A",  # aggressive: OS/version/script/traceroute
        "-O",  # OS detection
        "-Pn",  # skip host discovery
        "-n",  # never do DNS resolution
        "-F",  # fast mode (fewer ports)
        "-T2",
        "-T3",
        "-T4",  # timing templates
        "-v",
        "-vv",  # verbosity
    }
)

_TIMEOUT_ARG_TEMPLATE = "--host-timeout"  # applied internally, never user-supplied


def _validate_target(value: str) -> bool:
    for target_type in (TargetType.IP, TargetType.CIDR, TargetType.DOMAIN):
        try:
            validate_target_value(value, target_type)
            return True
        except Exception:  # noqa: BLE001 - trying the next type
            continue
    return False


class NmapPlugin(Plugin):
    """Runs an allow-listed Nmap scan against a single target."""

    def name(self) -> str:
        return "nmap"

    def description(self) -> str:
        return (
            "Runs Nmap against a target with an allow-listed set of scan-behavior "
            "flags. File-writing, script-execution, and file-input flags are never "
            "permitted, regardless of what's requested."
        )

    def validate_config(self, config: dict[str, Any]) -> None:
        target = config.get("target")
        if not target or not isinstance(target, str):
            raise InvalidPluginConfigError(
                self.name(), "config must include a non-empty string 'target'"
            )
        if not _validate_target(target):
            raise InvalidPluginConfigError(
                self.name(), f"'{target}' is not a valid IP, CIDR, or domain"
            )

        ports = config.get("ports", "1-1000")
        if not isinstance(ports, str) or not _PORTS_PATTERN.match(ports):
            raise InvalidPluginConfigError(
                self.name(),
                f"'{ports}' is not a valid ports specification "
                "(expected digits/commas/dashes only, e.g. '22,80,443' or '1-1000')",
            )

        arguments = config.get("arguments", [])
        if not isinstance(arguments, list) or not all(isinstance(a, str) for a in arguments):
            raise InvalidPluginConfigError(self.name(), "'arguments' must be a list of strings")
        disallowed = [a for a in arguments if a not in _ALLOWED_ARGUMENTS]
        if disallowed:
            raise InvalidPluginConfigError(
                self.name(),
                f"argument(s) not permitted: {disallowed}. Allowed: {sorted(_ALLOWED_ARGUMENTS)}",
            )

    def execute(self, config: dict[str, Any], timeout_seconds: int) -> PluginResult:
        target = str(config["target"])
        ports = str(config.get("ports", "1-1000"))
        arguments: list[str] = list(config.get("arguments", []))

        # Nmap's own timeout, layered under subprocess's timeout as a
        # second line of defense — if the process ignores SIGTERM at
        # the subprocess timeout boundary, nmap's internal host-timeout
        # gives it a chance to exit cleanly first.
        internal_timeout_ms = max(1000, (timeout_seconds - 1) * 1000)
        command = [
            "nmap",
            *arguments,
            "-p",
            ports,
            _TIMEOUT_ARG_TEMPLATE,
            f"{internal_timeout_ms}ms",
            target,
        ]

        try:
            result = subprocess.run(  # noqa: S603 - fixed binary, list args, no shell
                command,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raw_stdout = exc.stdout
            decoded_stdout: str = (
                raw_stdout.decode("utf-8", errors="replace")
                if isinstance(raw_stdout, bytes)
                else (raw_stdout or "")
            )
            return PluginResult(
                success=False,
                stdout=decoded_stdout,
                stderr=f"nmap timed out after {timeout_seconds}s",
                exit_code=None,
                metadata={"plugin": "nmap", "target": target, "command": command},
            )
        except FileNotFoundError as exc:
            return PluginResult(
                success=False,
                stdout="",
                stderr=f"nmap binary not found on this host: {exc}",
                exit_code=None,
                metadata={"plugin": "nmap", "target": target},
            )

        return PluginResult(
            success=result.returncode == 0,
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.returncode,
            metadata={"plugin": "nmap", "target": target, "ports": ports, "command": command},
        )
