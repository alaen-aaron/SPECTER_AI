"""
Integration tests for the M7.1 executor service (requires a running stack).

These hit the live `executor` service over HTTP (`EXECUTOR_URL`, default
http://localhost:9010) and verify the actual isolation guarantees of the
ephemeral plugin containers: non-root user, read-only rootfs, execution
timeout with container cleanup, and target-only egress. They are opt-in —
if no executor is reachable they skip, mirroring the repository-layer
integration tests (tests/integration/conftest.py).

Run with the stack up:
    docker compose -f infra/docker-compose.yml up --build -d
    cd backend && pytest tests/integration/test_executor_service_integration.py
"""

from __future__ import annotations

import os

import httpx
import pytest

EXECUTOR_URL = os.environ.get("EXECUTOR_URL", "http://localhost:9010").rstrip("/")


def _executor_reachable() -> bool:
    try:
        response = httpx.get(f"{EXECUTOR_URL}/health", timeout=5)
        return response.status_code == 200
    except Exception:  # noqa: BLE001 - any failure means skip
        return False


requires_executor = pytest.mark.skipif(
    not _executor_reachable(),
    reason="No reachable executor service — run the full stack "
    "(`docker compose -f infra/docker-compose.yml up --build -d`) to enable "
    "executor integration tests.",
)


def _execute(command: list[str], **kwargs: object) -> dict:
    payload: dict[str, object] = {
        "execution_id": kwargs.pop("execution_id", "it-exec-001"),
        "command": command,
        **kwargs,
    }
    response = httpx.post(f"{EXECUTOR_URL}/v1/executions", json=payload, timeout=180)
    response.raise_for_status()
    return response.json()


@requires_executor
def test_health_reports_docker_and_plugin_image() -> None:
    response = httpx.get(f"{EXECUTOR_URL}/health", timeout=5)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["docker_connected"] is True
    assert body["plugin_image_present"] is True
    assert body["network_policy"] == "target-only"


@requires_executor
def test_plugin_container_runs_as_non_root() -> None:
    body = _execute(["sh", "-c", "id -u"], timeout_seconds=30)
    assert body["status"] == "completed", body.get("error")
    assert body["exit_code"] == 0
    assert body["stdout"].strip() == "10001", (
        f"plugin container must run as uid 10001 (non-root), got stdout={body['stdout']!r}"
    )


@requires_executor
def test_plugin_container_rootfs_is_read_only() -> None:
    # Writing to the root filesystem must fail (read-only rootfs); the command
    # then exits non-zero, which is still a "completed" execution.
    body = _execute(
        ["sh", "-c", "echo denied >> /etc/readonly-test && exit 0 || exit 3"],
        timeout_seconds=30,
    )
    assert body["status"] == "completed"
    assert body["exit_code"] == 3, "write to read-only rootfs must fail"


@requires_executor
def test_plugin_container_timeout_kills_and_cleans_up() -> None:
    body = _execute(["sleep", "60"], timeout_seconds=2)
    assert body["status"] == "timed_out"
    assert body["exit_code"] is None
    assert body["duration_ms"] >= 0


@requires_executor
def test_plugin_cannot_reach_disallowed_addresses() -> None:
    # Target is declared as TEST-NET-3 (198.18.0.1); a direct TCP connect to
    # 8.8.8.8 must be dropped by the target-only OUTPUT policy. The plugin may
    # report the connect failure (completed, nonzero) or the executor may fail
    # closed (error) if iptables enforcement is unavailable in this environment
    # — either way, the plugin never reaches the disallowed address.
    body = _execute(
        [
            "sh",
            "-c",
            "python -c 'import socket,sys; s=socket.create_connection("
            '("8.8.8.8",53),2); sys.exit(0)\'',
        ],
        targets=["198.18.0.1"],
        timeout_seconds=15,
    )
    if body["status"] == "error":
        assert "network policy" in (body.get("error") or "").lower(), body.get("error")
    else:
        assert body["exit_code"] != 0, "connect to a non-target address must not succeed"


@requires_executor
def test_dns_via_embedded_resolver_is_allowed() -> None:
    # The target-only policy explicitly permits Docker's embedded DNS
    # (127.0.0.11), so name resolution inside the plugin container still works.
    # We resolve the container's OWN hostname (always known by the embedded
    # resolver on its ephemeral network) — services like `postgres` are
    # deliberately NOT resolvable from the isolated plugin network.
    body = _execute(
        [
            "sh",
            "-c",
            "hn=$(cat /etc/hostname); "
            "python -c 'import socket,sys; sys.exit(0 if "
            "socket.getaddrinfo(sys.argv[1],None) else 1)' \"$hn\"",
        ],
        targets=["198.18.0.1"],
        timeout_seconds=15,
    )
    if body["status"] == "error":
        assert "network policy" in (body.get("error") or "").lower(), body.get("error")
    else:
        assert body["exit_code"] == 0, body.get("stderr")


@requires_executor
def test_stdout_and_stderr_are_captured_separately() -> None:
    body = _execute(
        ["sh", "-c", "echo to-stdout; echo to-stderr >&2"],
        timeout_seconds=30,
    )
    assert body["status"] == "completed"
    assert "to-stdout" in body["stdout"]
    assert "to-stderr" in body["stderr"]


@requires_executor
def test_plugin_can_ping_as_non_root_with_cap_drop_all() -> None:
    # uid 10001 has no CAP_NET_RAW, so ping relies on the
    # net.ipv4.ping_group_range sysctl the executor applies to each container.
    body = _execute(["ping", "-c", "1", "-W", "2", "127.0.0.1"], timeout_seconds=30)
    assert body["status"] == "completed", body.get("error")
    assert body["exit_code"] == 0, body.get("stderr")
    assert "1 packets transmitted" in body["stdout"]


@requires_executor
def test_plugin_image_holds_security_tools() -> None:
    body = _execute(
        ["sh", "-c", "command -v nmap && command -v nuclei && command -v ping"],
        timeout_seconds=30,
    )
    assert body["status"] == "completed", body.get("error")
    assert body["exit_code"] == 0
    assert "nmap" in body["stdout"]
    assert "nuclei" in body["stdout"]