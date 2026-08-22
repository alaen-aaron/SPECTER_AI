"""
Unit tests for `ExecutorHttpRunner` (Milestone 7.1).

The runner is the worker-side client that dispatches an already-validated
plugin command to the `executor` service (which runs it in a hardened
ephemeral container). These tests pin the wire contract — payload shape and
result mapping — using `httpx.MockTransport`, so no real executor is needed.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.infrastructure.execution.executor_runner import ExecutorHttpRunner


def _make_runner(
    handler: Any,
) -> ExecutorHttpRunner:
    transport = httpx.MockTransport(handler)
    return ExecutorHttpRunner(
        base_url="http://executor:8000",
        image="specter-plugins:local",
        cpu_limit=1.0,
        memory_limit="512m",
        transport=transport,
    )


def _json_handler(status: int, body: dict[str, Any]):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=status, json=body, request=request)

    return handler


def test_payload_contains_command_targets_and_limits() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["json"] = request.content
        return httpx.Response(
            200,
            json={
                "execution_id": "abc",
                "status": "completed",
                "exit_code": 0,
                "stdout": "ok",
                "stderr": "",
            },
            request=request,
        )

    runner = _make_runner(handler)
    result = runner.run(
        ["nmap", "-sV", "10.0.0.5"],
        timeout_seconds=30,
        target="10.0.0.5",
        metadata={"plugin": "nmap"},
    )

    import json

    payload = json.loads(seen["json"])
    assert payload["command"] == ["nmap", "-sV", "10.0.0.5"]
    assert payload["targets"] == ["10.0.0.5"]
    assert payload["image"] == "specter-plugins:local"
    assert payload["cpu_limit"] == 1.0
    assert payload["memory_limit"] == "512m"
    assert payload["timeout_seconds"] == 30
    assert result.success is True
    assert result.stdout == "ok"
    assert result.exit_code == 0
    assert result.metadata["executor"] is True
    assert result.metadata["plugin"] == "nmap"


def test_completed_with_nonzero_exit_is_failure() -> None:
    runner = _make_runner(
        _json_handler(
            200,
            {
                "execution_id": "abc",
                "status": "completed",
                "exit_code": 1,
                "stdout": "nope",
                "stderr": "nmap error",
            },
        )
    )
    result = runner.run(["nmap", "10.0.0.5"], timeout_seconds=30, target="10.0.0.5")
    assert result.success is False
    assert result.exit_code == 1
    assert result.stderr == "nmap error"
    assert result.stdout == "nope"


def test_timed_out_maps_to_exit_code_none() -> None:
    runner = _make_runner(
        _json_handler(
            200,
            {
                "execution_id": "abc",
                "status": "timed_out",
                "stdout": "partial",
                "stderr": "",
                "exit_code": None,
            },
        )
    )
    result = runner.run(["nmap", "10.0.0.5"], timeout_seconds=30, target="10.0.0.5")
    assert result.success is False
    assert result.exit_code is None
    assert "timed out after 30s" in result.stderr
    assert result.stdout == "partial"


def test_error_status_maps_to_failure() -> None:
    runner = _make_runner(
        _json_handler(
            200,
            {
                "execution_id": "abc",
                "status": "error",
                "error": "plugin image missing",
            },
        )
    )
    result = runner.run(["nmap", "10.0.0.5"], timeout_seconds=30, target="10.0.0.5")
    assert result.success is False
    assert result.exit_code is None
    assert "plugin image missing" in result.stderr


def test_http_error_maps_to_unreachable_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "down"}, request=request)

    runner = _make_runner(handler)
    result = runner.run(["nmap", "10.0.0.5"], timeout_seconds=30, target="10.0.0.5")
    assert result.success is False
    assert result.exit_code is None
    assert "Executor unreachable" in result.stderr


def test_connection_error_maps_to_unreachable_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    runner = _make_runner(handler)
    result = runner.run(["nmap", "10.0.0.5"], timeout_seconds=30, target="10.0.0.5")
    assert result.success is False
    assert "Executor unreachable" in result.stderr


def test_artifacts_are_passed_through() -> None:
    runner = _make_runner(
        _json_handler(
            200,
            {
                "execution_id": "abc",
                "status": "completed",
                "exit_code": 0,
                "stdout": "ok",
                "stderr": "",
                "artifacts": ["a.jsonl"],
            },
        )
    )
    result = runner.run(["nuclei", "10.0.0.5"], timeout_seconds=30, target="10.0.0.5")
    assert result.artifacts == ["a.jsonl"]


@pytest.mark.parametrize(
    "target",
    ["", "10.0.0.5"],
)
def test_targets_list_reflects_target_argument(target: str) -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen["targets"] = json.loads(request.content)["targets"]
        return httpx.Response(
            200,
            json={"execution_id": "abc", "status": "completed", "exit_code": 0},
            request=request,
        )

    runner = _make_runner(handler)
    runner.run(["ping", "-c", "1", target], timeout_seconds=10, target=target)
    assert seen["targets"] == ([target] if target else [])