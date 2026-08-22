"""
Unit tests for the M7.1 plugin→runner dispatch boundary.

When the ExecutionEngine installs an active `CommandRunner` (via
`PluginManager.run`), the subprocess-based plugins must hand their
already-built command to the runner instead of calling `subprocess.run`.
These tests prove the boundary with a recording fake runner and no Docker.
"""

from __future__ import annotations

from typing import Any

from app.plugins.base import (
    PluginResult,
    get_active_runner,
    run_with_active_runner,
)
from app.plugins.manager import PluginManager
from app.plugins.nmap_plugin import NmapPlugin
from app.plugins.registry import PluginRegistry
from app.plugins.subfinder_plugin import SubfinderPlugin


class RecordingRunner:
    """Fake CommandRunner that records invocations and returns a canned result."""

    def __init__(self, result: PluginResult | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._result = result or PluginResult(
            success=True,
            stdout="runner output",
            stderr="",
            exit_code=0,
            metadata={"via": "runner"},
        )

    def run(
        self,
        command: list[str],
        *,
        timeout_seconds: int,
        target: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> PluginResult:
        self.calls.append(
            {
                "command": command,
                "timeout_seconds": timeout_seconds,
                "target": target,
                "metadata": metadata,
            }
        )
        return self._result


def test_nmap_plugin_dispatches_command_to_active_runner() -> None:
    plugin = NmapPlugin()
    runner = RecordingRunner()
    config = {"target": "10.0.0.5", "ports": "1-1000", "arguments": ["-sV", "-Pn"]}

    result = run_with_active_runner(runner, lambda: plugin.execute(config, timeout_seconds=30))

    assert result.stdout == "runner output"
    assert len(runner.calls) == 1
    call = runner.calls[0]
    # Command built by the plugin (allow-listed flags + internal host-timeout)
    assert call["command"][0] == "nmap"
    assert "10.0.0.5" in call["command"]
    assert "-sV" in call["command"]
    assert call["target"] == "10.0.0.5"
    assert call["timeout_seconds"] == 30
    assert call["metadata"]["plugin"] == "nmap"


def test_subprocess_plugin_dispatches_command_to_active_runner() -> None:
    plugin = SubfinderPlugin()
    runner = RecordingRunner()
    config = {"target": "example.com"}

    result = run_with_active_runner(runner, lambda: plugin.execute(config, timeout_seconds=30))

    assert result.success is True
    assert len(runner.calls) == 1
    assert runner.calls[0]["command"][0] == "subfinder"
    assert runner.calls[0]["target"] == "example.com"


def test_ping_plugin_dispatches_command_to_active_runner() -> None:
    from app.plugins.ping_plugin import PingPlugin

    plugin = PingPlugin()
    runner = RecordingRunner()
    config = {"hostname": "127.0.0.1"}

    run_with_active_runner(runner, lambda: plugin.execute(config, timeout_seconds=15))

    assert len(runner.calls) == 1
    assert runner.calls[0]["command"][0] == "ping"
    assert runner.calls[0]["target"] == "127.0.0.1"


def test_active_runner_is_none_by_default() -> None:
    # No runner installed → plugins fall back to local subprocess (existing
    # behavior). This is the default that keeps unit tests Docker-free.
    assert get_active_runner() is None


def test_runner_context_is_restored_after_execution() -> None:
    plugin = NmapPlugin()
    runner = RecordingRunner()
    config = {"target": "10.0.0.5", "ports": "80", "arguments": []}

    run_with_active_runner(runner, lambda: plugin.execute(config, timeout_seconds=30))

    assert get_active_runner() is None, "runner context must not leak"


def test_plugin_manager_run_passes_runner_to_plugin() -> None:
    registry = PluginRegistry()
    registry.register(NmapPlugin())
    manager = PluginManager(registry)
    runner = RecordingRunner()

    result = manager.run(
        "nmap", {"target": "10.0.0.5", "ports": "443", "arguments": []}, 30, runner=runner
    )

    assert result.success is True
    assert len(runner.calls) == 1
    assert runner.calls[0]["command"][0] == "nmap"


def test_plugin_manager_run_without_runner_uses_subprocess_fallback() -> None:
    registry = PluginRegistry()
    registry.register(NmapPlugin())
    manager = PluginManager(registry)

    # Without a runner, validation passes but execution would try nmap on the
    # host. We only assert the manager doesn't raise config errors and that no
    # runner context is active — the actual subprocess path is covered by the
    # existing engine tests with the echo plugin.
    result = manager.validate("nmap", {"target": "10.0.0.5", "ports": "80", "arguments": []})
    assert result is None
    assert get_active_runner() is None