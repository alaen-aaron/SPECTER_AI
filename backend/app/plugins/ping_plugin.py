"""Ping plugin (Milestone 3) — subprocess list args only, no shell, mandatory timeout."""

from __future__ import annotations

import platform
import subprocess
from typing import Any

from app.domain.exceptions import InvalidPluginConfigError
from app.domain.target_validation import validate_target_value
from app.domain.value_objects import TargetType
from app.plugins.base import Plugin, PluginResult

_PING_COUNT = 4
_PING_DEADLINE_SECONDS = 2
_PING_DEADLINE_MS = 2000
_IS_WINDOWS = platform.system() == "Windows"


def _looks_like_ip_or_domain(value: str) -> bool:
    """Accepts an IP or a domain; rejecting anything else also rejects
    shell metacharacters as a side effect, since neither validator's
    pattern permits them — but the real safety property here is list-
    args + no shell, not this input filter (defense in depth, not the
    primary control)."""
    for target_type in (TargetType.IP, TargetType.DOMAIN):
        try:
            validate_target_value(value, target_type)
            return True
        except Exception:  # noqa: BLE001 - trying the other type next
            continue
    return False


class PingPlugin(Plugin):
    """Sends a small, fixed number of ICMP echo requests to a host."""

    def name(self) -> str:
        return "ping"

    def description(self) -> str:
        return (
            f"Sends {_PING_COUNT} ICMP echo requests to a host via subprocess "
            "(list args, no shell, mandatory timeout)."
        )

    def validate_config(self, config: dict[str, Any]) -> None:
        hostname = config.get("hostname")
        if not hostname or not isinstance(hostname, str):
            raise InvalidPluginConfigError(
                self.name(), "config must include a non-empty string 'hostname'"
            )
        if not _looks_like_ip_or_domain(hostname):
            raise InvalidPluginConfigError(
                self.name(), f"'{hostname}' is not a valid IP address or domain name"
            )

    def execute(self, config: dict[str, Any], timeout_seconds: int) -> PluginResult:
        hostname = str(config["hostname"])
        if _IS_WINDOWS:
            command = [
                "ping",
                "-n",
                str(_PING_COUNT),
                "-w",
                str(_PING_DEADLINE_MS),
                hostname,
            ]
        else:
            command = [
                "ping",
                "-c",
                str(_PING_COUNT),
                "-W",
                str(_PING_DEADLINE_SECONDS),
                hostname,
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
                stderr=f"ping timed out after {timeout_seconds}s",
                exit_code=None,
                metadata={"plugin": "ping", "hostname": hostname, "command": command},
            )
        except FileNotFoundError as exc:
            return PluginResult(
                success=False,
                stdout="",
                stderr=f"ping binary not found on this host: {exc}",
                exit_code=None,
                metadata={"plugin": "ping", "hostname": hostname},
            )

        return PluginResult(
            success=result.returncode == 0,
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.returncode,
            metadata={"plugin": "ping", "hostname": hostname, "command": command},
        )
