"""
Worker-side client for the isolated plugin executor (Milestone 7.1).

Implements `CommandRunner` (defined in `app.plugins.base`) by dispatching an
already-validated plugin command to the `executor` service over HTTP. The
executor owns the Docker socket and runs the command in a hardened ephemeral
container (non-root, read-only rootfs, dropped capabilities, resource limits,
target-only network policy), then returns stdout/stderr/exit code.

The result is mapped back into a `PluginResult`, so the ExecutionEngine's
normalize → ToolResult → asset/finding pipeline is completely unchanged:
isolation is an execution-backend swap, not a pipeline change.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import structlog

from app.plugins.base import CommandRunner, PluginResult

logger = structlog.get_logger(__name__)


class ExecutorHttpRunner(CommandRunner):
    """Dispatches plugin commands to the executor service via HTTP."""

    def __init__(
        self,
        base_url: str,
        image: str,
        cpu_limit: float,
        memory_limit: str,
        timeout_buffer_seconds: int = 30,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._image = image
        self._cpu_limit = cpu_limit
        self._memory_limit = memory_limit
        self._timeout_buffer_seconds = timeout_buffer_seconds
        self._transport = transport

    def run(
        self,
        command: list[str],
        *,
        timeout_seconds: int,
        target: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> PluginResult:
        execution_id = str(uuid.uuid4())
        payload: dict[str, Any] = {
            "execution_id": execution_id,
            "command": command,
            "targets": [target] if target else [],
            "timeout_seconds": timeout_seconds,
            "image": self._image,
            "cpu_limit": self._cpu_limit,
            "memory_limit": self._memory_limit,
        }
        meta: dict[str, Any] = dict(metadata or {})
        meta.update({"executor": True, "execution_id": execution_id})

        try:
            with httpx.Client(
                timeout=timeout_seconds + self._timeout_buffer_seconds,
                transport=self._transport,
            ) as client:
                response = client.post(
                    f"{self._base_url}/v1/executions", json=payload
                )
                response.raise_for_status()
                body = response.json()
        except httpx.HTTPError as exc:
            logger.warning(
                "executor_http_error",
                execution_id=execution_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return PluginResult(
                success=False,
                stdout="",
                stderr=f"Executor unreachable for plugin execution: {exc}",
                exit_code=None,
                metadata=meta,
            )

        status = body.get("status")
        if status == "timed_out":
            logger.warning(
                "executor_timed_out",
                execution_id=execution_id,
                timeout_seconds=timeout_seconds,
            )
            return PluginResult(
                success=False,
                stdout=body.get("stdout", ""),
                stderr=f"Plugin execution timed out after {timeout_seconds}s",
                exit_code=None,
                metadata=meta,
            )

        if status == "completed":
            exit_code = body.get("exit_code")
            return PluginResult(
                success=exit_code == 0,
                stdout=body.get("stdout", ""),
                stderr=body.get("stderr", ""),
                exit_code=exit_code,
                artifacts=list(body.get("artifacts", [])),
                metadata=meta,
            )

        # status == "failed" or "error"
        error = body.get("error") or body.get("stderr") or "Executor reported failure"
        return PluginResult(
            success=False,
            stdout=body.get("stdout", ""),
            stderr=error,
            exit_code=body.get("exit_code"),
            metadata=meta,
        )