"""
Subprocess plugin base class (Milestone 5).

Shared base for all plugins that invoke external tools via
subprocess. Reduces duplication across 17+ production plugins
by providing common patterns for:
- Binary validation
- Target validation
- Subprocess execution with timeout
- Error handling
- Standard PluginResult construction
"""

from __future__ import annotations

import subprocess
from typing import Any, cast

from app.domain.exceptions import InvalidPluginConfigError
from app.plugins.base import Plugin, PluginResult


class SubprocessPlugin(Plugin):
    """
    Base class for plugins that invoke external CLI tools.

    Subclasses define:
    - name(), description(), capability(), metadata() (from Plugin ABC)
    - validate_config() for plugin-specific validation
    - _build_command() to construct the subprocess command list
    - _allowed_flags() for flag allow-listing (optional)

    The base class provides:
    - _execute_subprocess() with timeout, error handling, timeout/file-not-found
    - _validate_required_field() helper
    - _validate_target() helper
    """

    def _execute_subprocess(
        self,
        command: list[str],
        timeout_seconds: int,
        target: str = "",
        extra_metadata: dict[str, Any] | None = None,
    ) -> PluginResult:
        """Execute a subprocess with standard error handling."""
        meta = {"plugin": self.name(), "command": command}
        if target:
            meta["target"] = target
        if extra_metadata:
            meta.update(extra_metadata)

        try:
            result = subprocess.run(  # noqa: S603 - controlled by subclass
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
                stderr=f"{self.name()} timed out after {timeout_seconds}s",
                exit_code=None,
                metadata=meta,
            )
        except FileNotFoundError as exc:
            binary = command[0] if command else self.name()
            return PluginResult(
                success=False,
                stdout="",
                stderr=f"{binary} binary not found on this host: {exc}",
                exit_code=None,
                metadata=meta,
            )

        return PluginResult(
            success=result.returncode == 0,
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.returncode,
            metadata=meta,
        )

    def _validate_required_field(
        self, config: dict[str, Any], field_name: str, field_type: type = str
    ) -> Any:
        """Validate a required field exists and has the correct type."""
        value = config.get(field_name)
        if value is None or (field_type is str and not isinstance(value, str)):
            raise InvalidPluginConfigError(
                self.name(),
                f"config must include a valid '{field_name}' ({field_type.__name__})",
            )
        if field_type is str and not value:
            raise InvalidPluginConfigError(
                self.name(), f"'{field_name}' must not be empty"
            )
        return value

    def _validate_target(self, config: dict[str, Any]) -> str:
        """Validate and return the target field."""
        return cast(str, self._validate_required_field(config, "target"))

    def _validate_flag_list(
        self, config: dict[str, Any], field_name: str, allowed: frozenset[str]
    ) -> list[str]:
        """Validate a list of flags against an allow-list."""
        flags = config.get(field_name, [])
        if not isinstance(flags, list):
            raise InvalidPluginConfigError(
                self.name(), f"'{field_name}' must be a list of strings"
            )
        if not all(isinstance(f, str) for f in flags):
            raise InvalidPluginConfigError(
                self.name(), f"'{field_name}' must contain only strings"
            )
        disallowed = [f for f in flags if f not in allowed]
        if disallowed:
            raise InvalidPluginConfigError(
                self.name(),
                f"flag(s) not permitted: {disallowed}. Allowed: {sorted(allowed)}",
            )
        return flags
