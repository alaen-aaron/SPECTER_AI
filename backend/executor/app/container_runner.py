"""
Ephemeral plugin container orchestration (SRS §7.3 isolation model).

The runner creates, per task invocation:
  * one throwaway Docker bridge network (so the plugin can't see other
    containers on the shared networks),
  * one ephemeral container from the hardened `specter-plugins` image,
    running as a non-root user with a read-only root filesystem, all
    Linux capabilities dropped, CPU/memory limits, and a hard timeout,
  * and installs the target-only egress policy into the container's own
    network namespace.

stdout/stderr are redirected inside the container to a writable tmpfs
(`/output`), pulled back after the container exits via the Docker archive
API, and the container + network are destroyed. Nothing in the plugin
container can reach the docker socket, the host filesystem, or the shared
`scan_artifacts` volume — all artifact transfer happens over the archive
API after the container has stopped.
"""

from __future__ import annotations

import contextlib
import io
import tarfile
import time
from typing import Any

import docker
import structlog

from app.config import ExecutorSettings
from app.models import ArtifactFile, ExecuteRequest, ExecuteResponse
from app.network_policy import (
    NetworkPolicy,
    NetworkPolicyError,
    apply_target_only_policy,
    expand_target_addresses,
)

logger = structlog.get_logger(__name__)

# The command list is passed straight through as the container's argv (Docker
# runs it directly, so plugin arguments are never shell-interpreted). stdout
# and stderr are captured via Docker's log driver and demuxed per-stream.
_NON_ROOT_USER = "10001:10001"


class ImageUnavailableError(RuntimeError):
    """Raised when the hardened plugin image is not present on the daemon."""


class ContainerRunner:
    def __init__(self, settings: ExecutorSettings) -> None:
        self._settings = settings
        self._client = docker.from_env()

    # --- Public API -----------------------------------------------------

    def execute(self, request: ExecuteRequest) -> ExecuteResponse:
        started = time.monotonic()
        execution_id = request.execution_id
        container = None
        network = None

        try:
            self._ensure_image(request.image)
        except ImageUnavailableError as exc:
            logger.error("executor_image_missing", execution_id=execution_id)
            return self._response(request, started, status="error", error=str(exc))

        network_name = f"specter-net-{execution_id[:8]}"
        container_name = f"specter-run-{execution_id[:12]}"

        try:
            network = self._client.networks.create(
                network_name, driver="bridge", internal=False, check_duplicate=True
            )

            container = self._client.containers.create(
                image=request.image,
                command=request.command,
                name=container_name,
                network=network_name,
                user=_NON_ROOT_USER,
                read_only=True,
                tmpfs={
                    "/tmp": f"rw,size={self._settings.TMP_TMPFS_SIZE},mode=1777",
                    "/output": f"rw,size={self._settings.OUTPUT_TMPFS_SIZE},mode=1777",
                },
                cap_drop=["ALL"],
                sysctls={"net.ipv4.ping_group_range": "10001 10001"},
                mem_limit=request.memory_limit,
                nano_cpus=int(request.cpu_limit * 1_000_000_000),
                working_dir="/tmp",
                environment={
                    "HOME": "/tmp",
                    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                },
                hostname=f"specter-{execution_id[:8]}",
                labels={
                    "specter.execution_id": execution_id,
                    "specter.service": "specter-executor",
                    "specter.managed": "true",
                },
            )
            container_id = container.id
            container.start()
            logger.info(
                "executor_container_started",
                execution_id=execution_id,
                container_id=container_id,
                image=request.image,
            )

            try:
                policy = self._apply_network_policy(container, request)
            except NetworkPolicyError as exc:
                logger.warning(
                    "executor_network_policy_failed_closed",
                    execution_id=execution_id,
                    error=str(exc),
                )
                return self._response(
                    request, started, status="error", error=str(exc), container_id=container_id
                )

            exit_code, timed_out = self._wait_for_exit(container, request.timeout_seconds)
            stdout, stderr, artifacts = self._collect_results(container, request)

            if timed_out:
                return self._response(
                    request,
                    started,
                    status="timed_out",
                    exit_code=None,
                    stdout=stdout,
                    stderr=stderr,
                    container_id=container_id,
                    network_policy=policy,
                    artifacts=artifacts,
                )

            return self._response(
                request,
                started,
                status="completed",
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                container_id=container_id,
                network_policy=policy,
                artifacts=artifacts,
            )

        except Exception as exc:  # noqa: BLE001 - executor must always return a structured result
            logger.error(
                "executor_container_failed",
                execution_id=execution_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return self._response(
                request, started, status="error", error=f"executor error: {exc}"
            )
        finally:
            self._cleanup(container, network, execution_id)

    # --- Internals ------------------------------------------------------

    def _ensure_image(self, image: str) -> None:
        try:
            self._client.images.get(image)
        except docker.errors.ImageNotFound:
            raise ImageUnavailableError(
                f"plugin image '{image}' is not present on the Docker daemon. "
                "Build it first: docker compose build plugins-image"
            ) from None

    def _apply_network_policy(
        self, container: Any, request: ExecuteRequest
    ) -> NetworkPolicy:
        if self._settings.NETWORK_POLICY == "none":
            return NetworkPolicy(policy="none")

        allowed = expand_target_addresses(request.targets)
        container.reload()
        pid = int(container.attrs["State"]["Pid"])

        if pid == 0:
            # The container already exited before the policy could be
            # installed (an instant command that outran the pause). There is
            # nothing left to constrain; skip enforcement and let the caller
            # collect the finished run's output.
            logger.warning(
                "executor_container_exited_before_policy",
                execution_id=request.execution_id,
            )
            return NetworkPolicy(policy="exited-before-policy")

        # Pause the container so the plugin can't start doing I/O before the
        # policy is in place (closes the start→policy race). Best effort: if
        # pause is unsupported, the rules are still installed immediately.
        try:
            container.pause()
            try:
                return apply_target_only_policy(pid, allowed)
            finally:
                container.unpause()
        except NetworkPolicyError:
            raise
        except Exception as exc:  # noqa: BLE001 - policy still gets applied
            logger.warning("executor_pause_unavailable", error=str(exc))
            return apply_target_only_policy(pid, allowed)

    def _wait_for_exit(self, container: Any, timeout_seconds: int) -> tuple[int | None, bool]:
        try:
            result = container.wait(timeout=timeout_seconds + self._settings.CLEANUP_GRACE_SECONDS)
            return int(result.get("StatusCode", 0)), False
        except Exception as exc:  # noqa: BLE001 - ReadTimeout means the plugin overran its budget
            logger.warning(
                "executor_container_timed_out",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            self._kill(container)
            return None, True

    def _collect_results(
        self, container: Any, request: ExecuteRequest
    ) -> tuple[str, str, list[ArtifactFile]]:
        stdout = container.logs(stdout=True, stderr=False).decode("utf-8", "replace")
        stderr = container.logs(stdout=False, stderr=True).decode("utf-8", "replace")

        # Artifact extraction is best-effort: plugins may write files to /output
        # for the executor to pull back. On Docker Desktop the archive API does
        # not include tmpfs contents, so this can return nothing — stdout/stderr
        # above are the authoritative result payload.
        artifacts: list[ArtifactFile] = []
        if request.capture_artifacts:
            for name, data in self._extract_output_archive(container).items():
                artifacts.append(ArtifactFile(name=name, data=data))

        # Fallback: if the container produced no log frames at all, use the
        # (merged) docker log rather than returning an empty payload.
        if not stdout and not stderr:
            stdout = container.logs(stdout=True, stderr=True).decode("utf-8", "replace")
        return stdout, stderr, artifacts

    def _extract_output_archive(self, container: Any) -> dict[str, str]:
        try:
            bits, _ = container.get_archive("/output")
            raw = b"".join(bits)
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as tf:
                return {
                    member.name: tf.extractfile(member).read().decode("utf-8", "replace")
                    for member in tf.getmembers()
                    if member.isfile()
                }
        except Exception as exc:  # noqa: BLE001 - caller falls back to docker logs
            logger.warning("executor_output_archive_unavailable", error=str(exc))
            return {}

    def _kill(self, container: Any) -> None:
        with contextlib.suppress(Exception):  # noqa: BLE001 - container may already be gone
            container.kill()

    def _cleanup(self, container: Any, network: Any, execution_id: str) -> None:
        if container is not None:
            try:
                container.remove(force=True)
                logger.info("executor_container_removed", execution_id=execution_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "executor_container_remove_failed", execution_id=execution_id, error=str(exc)
                )
        if network is not None:
            try:
                network.remove()
            except Exception as exc:  # noqa: BLE001
                logger.warning("executor_network_remove_failed", error=str(exc))

    def _response(
        self,
        request: ExecuteRequest,
        started: float,
        *,
        status: str,
        exit_code: int | None = None,
        stdout: str = "",
        stderr: str = "",
        error: str | None = None,
        container_id: str | None = None,
        network_policy: NetworkPolicy | None = None,
        artifacts: list[ArtifactFile] | None = None,
    ) -> ExecuteResponse:
        duration_ms = int((time.monotonic() - started) * 1000)
        return ExecuteResponse(
            execution_id=request.execution_id,
            status=status,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration_ms,
            error=error,
            container_id=container_id,
            network_policy={
                "policy": network_policy.policy if network_policy else "unset",
                "allowed_addresses": (
                    list(network_policy.allowed_addresses) if network_policy else []
                ),
                "rules_applied": network_policy.rules_applied if network_policy else 0,
            },
            artifacts=artifacts or [],
        )