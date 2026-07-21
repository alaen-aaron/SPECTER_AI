"""Unit tests for `PluginRegistry` and `PluginManager` (Milestone 3)."""

from __future__ import annotations

from typing import Any

import pytest

from app.domain.exceptions import InvalidPluginConfigError, PluginNotFoundError
from app.plugins.base import Plugin, PluginResult
from app.plugins.manager import PluginManager
from app.plugins.registry import PluginRegistry


class _FakePlugin(Plugin):
    """A minimal, deterministic plugin for isolating registry/manager
    logic from any real subprocess behavior."""

    def __init__(
        self,
        plugin_name: str = "fake",
        *,
        should_raise_on_execute: Exception | None = None,
    ) -> None:
        self._name = plugin_name
        self._should_raise = should_raise_on_execute

    def name(self) -> str:
        return self._name

    def description(self) -> str:
        return "A fake plugin for tests."

    def validate_config(self, config: dict[str, Any]) -> None:
        if config.get("must_fail_validation"):
            raise InvalidPluginConfigError(self._name, "must_fail_validation was set")

    def execute(self, config: dict[str, Any], timeout_seconds: int) -> PluginResult:
        if self._should_raise is not None:
            raise self._should_raise
        return PluginResult(success=True, stdout="ok", stderr="", exit_code=0)


@pytest.fixture
def registry() -> PluginRegistry:
    return PluginRegistry()


def test_register_and_get(registry: PluginRegistry) -> None:
    plugin = _FakePlugin("alpha")
    registry.register(plugin)
    assert registry.get("alpha") is plugin


def test_get_unknown_plugin_raises(registry: PluginRegistry) -> None:
    with pytest.raises(PluginNotFoundError):
        registry.get("does-not-exist")


def test_list_returns_all_registered(registry: PluginRegistry) -> None:
    registry.register(_FakePlugin("alpha"))
    registry.register(_FakePlugin("beta"))
    names = {p.name() for p in registry.list()}
    assert names == {"alpha", "beta"}


def test_register_same_name_overwrites(registry: PluginRegistry) -> None:
    first = _FakePlugin("alpha")
    second = _FakePlugin("alpha")
    registry.register(first)
    registry.register(second)
    assert registry.get("alpha") is second
    assert len(registry.list()) == 1


def test_unregister_removes_plugin(registry: PluginRegistry) -> None:
    registry.register(_FakePlugin("alpha"))
    registry.unregister("alpha")
    with pytest.raises(PluginNotFoundError):
        registry.get("alpha")


def test_unregister_unknown_plugin_is_a_no_op(registry: PluginRegistry) -> None:
    registry.unregister("never-registered")  # must not raise


def test_manager_run_success(registry: PluginRegistry) -> None:
    registry.register(_FakePlugin("alpha"))
    manager = PluginManager(registry)
    result = manager.run("alpha", {}, timeout_seconds=5)
    assert result.success is True
    assert result.stdout == "ok"


def test_manager_run_unknown_plugin_raises(registry: PluginRegistry) -> None:
    manager = PluginManager(registry)
    with pytest.raises(PluginNotFoundError):
        manager.run("nonexistent", {}, timeout_seconds=5)


def test_manager_run_validates_config_before_executing(registry: PluginRegistry) -> None:
    registry.register(_FakePlugin("alpha"))
    manager = PluginManager(registry)
    with pytest.raises(InvalidPluginConfigError):
        manager.run("alpha", {"must_fail_validation": True}, timeout_seconds=5)


def test_manager_validate_only_does_not_execute(registry: PluginRegistry) -> None:
    """`validate()` must never call `execute()` — used by ScanService to
    fail fast before persisting a scan row."""
    plugin = _FakePlugin("alpha", should_raise_on_execute=RuntimeError("should never run"))
    registry.register(plugin)
    manager = PluginManager(registry)
    manager.validate("alpha", {})  # must not raise, must not execute


def test_manager_converts_unexpected_plugin_exception_to_failed_result(
    registry: PluginRegistry,
) -> None:
    """A bug in a plugin's `execute()` must become a failed PluginResult,
    never propagate and crash the caller (ExecutionEngine/Celery task)."""
    plugin = _FakePlugin("buggy", should_raise_on_execute=RuntimeError("boom"))
    registry.register(plugin)
    manager = PluginManager(registry)

    result = manager.run("buggy", {}, timeout_seconds=5)

    assert result.success is False
    assert "boom" in result.stderr


def test_manager_reraises_invalid_config_from_execute() -> None:
    """If a plugin somehow raises InvalidPluginConfigError from inside
    execute() (rather than validate_config()), it should propagate as
    a caller error, not be swallowed into a generic failed result."""
    registry = PluginRegistry()
    plugin = _FakePlugin(
        "alpha", should_raise_on_execute=InvalidPluginConfigError("alpha", "late failure")
    )
    registry.register(plugin)
    manager = PluginManager(registry)

    with pytest.raises(InvalidPluginConfigError):
        manager.run("alpha", {}, timeout_seconds=5)
