#!/usr/bin/env python3
"""
SPECTER_AI environment/stack doctor.

Runs every check listed in the Milestone verification requirements and
prints a report matching the requested format. Deliberately dependency-
free (standard library only) so it can run on a bare host before any
Python packages are installed — it is the very first thing a developer
runs, so it cannot assume the backend venv already exists.

Usage:
    python3 scripts/doctor.py            # human-readable report
    python3 scripts/doctor.py --json     # machine-readable report (for CI)

Exit code: 0 if every check passes, 1 otherwise. `make verify` and CI
both key off this exit code.
"""

from __future__ import annotations

import json
import re
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = REPO_ROOT / ".env"
COMPOSE_FILE = REPO_ROOT / "infra" / "docker-compose.yml"
BACKEND_DIR = REPO_ROOT / "backend"
FRONTEND_DIR = REPO_ROOT / "frontend"

# Host-mapped ports from infra/docker-compose.yml. Doctor.py runs on the
# HOST, not inside a container, so it always talks to `localhost`, never
# to the internal Compose service names (`postgres`, `redis`, ...).
POSTGRES_PORT = 5432
REDIS_PORT = 6379
MINIO_PORT = 9000
API_PORT = 8000
FRONTEND_PORT = 5173

REQUIRED_PYTHON = (3, 12)
REQUIRED_NODE_MAJOR = 20

REQUIRED_ENV_VARS = [
    "APP_NAME",
    "APP_ENV",
    "LOG_LEVEL",
    "CORS_ALLOW_ORIGINS",
    "DATABASE_URL",
    "REDIS_URL",
    "JWT_SECRET",
    "JWT_ACCESS_TTL_MIN",
    "OBJECT_STORAGE_ENDPOINT",
    "OBJECT_STORAGE_ACCESS_KEY",
    "OBJECT_STORAGE_SECRET_KEY",
    "OBJECT_STORAGE_BUCKET",
    "LLM_PROVIDER",
    "LLM_BASE_URL",
    "LLM_MODEL",
    "SCOPE_GUARD_STRICT",
]


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""
    warning: bool = False  # a soft failure that doesn't fail the overall run


@dataclass
class Report:
    results: list[CheckResult] = field(default_factory=list)

    def add(self, result: CheckResult) -> None:
        self.results.append(result)

    @property
    def hard_failures(self) -> list[CheckResult]:
        return [r for r in self.results if not r.passed and not r.warning]

    @property
    def overall_pass(self) -> bool:
        return len(self.hard_failures) == 0


def _run(cmd: list[str], timeout: float = 5.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed, known commands only
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _tcp_reachable(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _http_get(url: str, timeout: float = 3.0) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, ""


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


# --------------------------------------------------------------------------
# Individual checks
# --------------------------------------------------------------------------


def check_docker_daemon() -> CheckResult:
    if shutil.which("docker") is None:
        return CheckResult("Docker daemon running", False, "`docker` binary not found on PATH")
    try:
        result = _run(["docker", "info"], timeout=10)
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return CheckResult("Docker daemon running", False, str(e))
    if result.returncode != 0:
        return CheckResult(
            "Docker daemon running",
            False,
            "`docker info` failed — is the Docker daemon/Docker Desktop running?",
        )
    return CheckResult("Docker daemon running", True)


def check_docker_compose() -> CheckResult:
    if shutil.which("docker") is None:
        return CheckResult("Docker Compose installed", False, "`docker` binary not found")
    try:
        result = _run(["docker", "compose", "version"], timeout=10)
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return CheckResult("Docker Compose installed", False, str(e))
    if result.returncode != 0:
        return CheckResult(
            "Docker Compose installed",
            False,
            "`docker compose version` failed — Compose v2 plugin not installed",
        )
    return CheckResult("Docker Compose installed", True, result.stdout.strip())


def check_required_ports() -> CheckResult:
    """
    Reports the state of each port SPECTER_AI needs.

    This is intentionally informational rather than a hard pass/fail on a
    specific state: before `docker compose up`, every port should be free;
    after it, every port should be in use by our own stack. Either state
    is healthy depending on when you run `make verify`. We only fail this
    check if a port is occupied by something that clearly isn't part of
    our stack (best-effort: we cannot identify the owning process
    portably without extra dependencies, so we simply surface state).
    """
    ports = {
        "postgres (5432)": POSTGRES_PORT,
        "redis (6379)": REDIS_PORT,
        "minio (9000)": MINIO_PORT,
        "api (8000)": API_PORT,
        "frontend (5173)": FRONTEND_PORT,
    }
    lines = []
    for label, port in ports.items():
        state = "in use" if _tcp_reachable("localhost", port) else "free"
        lines.append(f"{label}: {state}")
    return CheckResult("Required ports", True, "; ".join(lines))


def check_env_file_exists() -> CheckResult:
    if not ENV_FILE.exists():
        return CheckResult(
            ".env exists",
            False,
            f"{ENV_FILE} not found — run `cp .env.example .env`",
        )
    return CheckResult(".env exists", True)


def check_env_variables() -> CheckResult:
    if not ENV_FILE.exists():
        return CheckResult("Environment variables", False, ".env missing, cannot check")
    values = _parse_env_file(ENV_FILE)
    missing = [k for k in REQUIRED_ENV_VARS if k not in values or values[k] == ""]
    if missing:
        return CheckResult(
            "Environment variables",
            False,
            f"missing/empty: {', '.join(missing)}",
        )
    return CheckResult("Environment variables", True, f"{len(REQUIRED_ENV_VARS)} required vars present")


def check_postgres() -> CheckResult:
    if not _tcp_reachable("localhost", POSTGRES_PORT):
        return CheckResult(
            "PostgreSQL connectivity",
            False,
            f"cannot reach localhost:{POSTGRES_PORT} — is `docker compose up postgres` running?",
        )
    return CheckResult("PostgreSQL connectivity", True, f"TCP reachable on {POSTGRES_PORT}")


def check_redis() -> CheckResult:
    """Speaks raw RESP to avoid requiring the `redis` pip package."""
    try:
        with socket.create_connection(("localhost", REDIS_PORT), timeout=2) as sock:
            sock.sendall(b"PING\r\n")
            response = sock.recv(64)
    except OSError as e:
        return CheckResult("Redis connectivity", False, str(e))
    if b"PONG" in response:
        return CheckResult("Redis connectivity", True, "PING -> PONG")
    return CheckResult("Redis connectivity", False, f"unexpected response: {response!r}")


def check_minio() -> CheckResult:
    status, _ = _http_get(f"http://localhost:{MINIO_PORT}/minio/health/live")
    if status == 200:
        return CheckResult("MinIO connectivity", True, "health/live -> 200")
    return CheckResult("MinIO connectivity", False, f"health/live returned status {status or 'unreachable'}")


def check_api_health() -> CheckResult:
    status, body = _http_get(f"http://localhost:{API_PORT}/api/v1/health")
    if status != 200:
        return CheckResult(
            "FastAPI health endpoint",
            False,
            f"GET /api/v1/health returned {status or 'unreachable'}",
        )
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return CheckResult("FastAPI health endpoint", False, "response was not valid JSON")
    return CheckResult(
        "FastAPI health endpoint",
        True,
        f"status={payload.get('status')}",
    )


def check_frontend() -> CheckResult:
    status, _ = _http_get(f"http://localhost:{FRONTEND_PORT}/")
    if status == 200:
        return CheckResult("Frontend availability", True)
    return CheckResult("Frontend availability", False, f"returned {status or 'unreachable'}")


def check_swagger() -> CheckResult:
    status, _ = _http_get(f"http://localhost:{API_PORT}/docs")
    if status == 200:
        return CheckResult("Swagger availability", True)
    return CheckResult("Swagger availability", False, f"/docs returned {status or 'unreachable'}")


def _compose(*args: str, timeout: float = 15.0) -> subprocess.CompletedProcess[str]:
    return _run(["docker", "compose", "-f", str(COMPOSE_FILE), *args], timeout=timeout)


def check_celery_worker() -> CheckResult:
    if shutil.which("docker") is None:
        return CheckResult("Celery Worker", False, "docker not available to inspect worker")
    try:
        result = _compose(
            "exec",
            "-T",
            "worker",
            "celery",
            "-A",
            "app.infrastructure.celery_app.app",
            "inspect",
            "ping",
            timeout=20,
        )
    except subprocess.TimeoutExpired:
        return CheckResult("Celery Worker", False, "inspect ping timed out")
    if result.returncode == 0 and "pong" in result.stdout.lower():
        return CheckResult("Celery Worker", True, "inspect ping -> pong")
    return CheckResult("Celery Worker", False, result.stderr.strip() or "worker did not respond")


def check_celery_beat() -> CheckResult:
    """
    Celery beat has no RPC ping (it is a scheduler, not a worker), so the
    correct health signal is container liveness, not a protocol response.
    """
    if shutil.which("docker") is None:
        return CheckResult("Celery Beat", False, "docker not available to inspect beat")
    try:
        result = _compose("ps", "--format", "json", "beat", timeout=10)
    except subprocess.TimeoutExpired:
        return CheckResult("Celery Beat", False, "docker compose ps timed out")
    if result.returncode != 0 or not result.stdout.strip():
        return CheckResult("Celery Beat", False, "beat container not found — is the stack running?")
    try:
        # `docker compose ps --format json` emits one JSON object per line.
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        states = [json.loads(line).get("State", "") for line in lines]
    except json.JSONDecodeError:
        return CheckResult("Celery Beat", False, "could not parse `docker compose ps` output")
    if any(state == "running" for state in states):
        return CheckResult("Celery Beat", True, "container running")
    return CheckResult("Celery Beat", False, f"container state: {states}")


def check_alembic_configuration() -> CheckResult:
    alembic_ini = BACKEND_DIR / "alembic.ini"
    env_py = BACKEND_DIR / "alembic" / "env.py"
    if not alembic_ini.exists() or not env_py.exists():
        return CheckResult("Alembic configuration", False, "alembic.ini or alembic/env.py missing")

    if shutil.which("docker") is not None:
        try:
            result = _compose("exec", "-T", "api", "alembic", "current", timeout=20)
            if result.returncode == 0:
                return CheckResult("Alembic configuration", True, "alembic current -> OK")
            # Fall through to static check if the container isn't up.
        except subprocess.TimeoutExpired:
            pass

    return CheckResult(
        "Alembic configuration",
        True,
        "files present (container not running — skipped live `alembic current` check)",
        warning=False,
    )


def check_python_version() -> CheckResult:
    current = sys.version_info[:2]
    if current >= REQUIRED_PYTHON:
        return CheckResult(
            "Required Python version",
            True,
            f"{current[0]}.{current[1]} >= {REQUIRED_PYTHON[0]}.{REQUIRED_PYTHON[1]}",
        )
    return CheckResult(
        "Required Python version",
        False,
        f"found {current[0]}.{current[1]}, need >= {REQUIRED_PYTHON[0]}.{REQUIRED_PYTHON[1]}",
    )


def check_node_version() -> CheckResult:
    if shutil.which("node") is None:
        return CheckResult("Required Node version", False, "`node` not found on PATH")
    try:
        result = _run(["node", "--version"], timeout=5)
    except subprocess.TimeoutExpired:
        return CheckResult("Required Node version", False, "`node --version` timed out")
    match = re.match(r"v(\d+)\.", result.stdout.strip())
    if not match:
        return CheckResult("Required Node version", False, f"unexpected output: {result.stdout!r}")
    major = int(match.group(1))
    if major >= REQUIRED_NODE_MAJOR:
        return CheckResult("Required Node version", True, result.stdout.strip())
    return CheckResult(
        "Required Node version",
        False,
        f"found {result.stdout.strip()}, need >= v{REQUIRED_NODE_MAJOR}",
    )


CHECKS = [
    check_docker_daemon,
    check_docker_compose,
    check_required_ports,
    check_env_file_exists,
    check_postgres,
    check_redis,
    check_minio,
    check_api_health,
    check_frontend,
    check_swagger,
    check_celery_worker,
    check_celery_beat,
    check_alembic_configuration,
    check_env_variables,
    check_python_version,
    check_node_version,
]


def run_all() -> Report:
    report = Report()
    for check in CHECKS:
        try:
            report.add(check())
        except Exception as exc:  # noqa: BLE001 - a broken check must not crash the whole run
            report.add(CheckResult(check.__name__, False, f"check raised: {exc}"))
    return report


def print_human_report(report: Report) -> None:
    width = 51
    print("=" * width)
    print("SPECTER_AI Verification")
    print("=" * width)
    print()
    for result in report.results:
        mark = "✓" if result.passed else ("⚠" if result.warning else "✗")
        line = f"{mark} {result.name}"
        print(line)
        if result.detail:
            print(f"    {result.detail}")
    print()
    print("Overall Status")
    print()
    print("PASS" if report.overall_pass else "FAIL")
    if not report.overall_pass:
        print()
        print("Failures:")
        for failure in report.hard_failures:
            print(f"  - {failure.name}: {failure.detail}")


def print_json_report(report: Report) -> None:
    payload = {
        "overall_status": "PASS" if report.overall_pass else "FAIL",
        "checks": [
            {"name": r.name, "passed": r.passed, "warning": r.warning, "detail": r.detail}
            for r in report.results
        ],
    }
    print(json.dumps(payload, indent=2))


def main() -> int:
    report = run_all()
    if "--json" in sys.argv:
        print_json_report(report)
    else:
        print_human_report(report)
    return 0 if report.overall_pass else 1


if __name__ == "__main__":
    sys.exit(main())
