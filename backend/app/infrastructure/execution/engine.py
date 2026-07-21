"""
Execution Engine (Milestone 3, extended Milestone 4A).

This is what the Celery task actually calls — never the API layer
directly (SRS's "no API router executes tools directly" requirement).
It re-validates Scope Guard immediately before invoking the plugin,
even though `ScanService.create` already validated it at enqueue time:
a scan can sit in the queue for an unknown amount of time, and an
authorization record can expire or be revoked in that window. Only
revalidating at both points actually closes that gap — checking once
at enqueue time and trusting it forever would be a real bypass
disguised as a performance optimization.

Milestone 4A addition: after plugin execution, the engine runs the
output through the normalizer registry (if a normalizer is registered
for the plugin) and persists a `ToolResult` row. Normalization failure
is non-fatal — raw output is still available via logs_path.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import structlog

from app.application.scope_guard_service import ScopeGuardService
from app.domain.entities import AuditLogEntry, Scan, ToolResult
from app.domain.exceptions import DomainError
from app.domain.repositories import AuditLogRepository, ScanRepository, ToolResultRepository
from app.domain.value_objects import ScanStatus
from app.infrastructure.storage.local_artifact_store import LocalArtifactStore
from app.plugins.manager import PluginManager
from app.plugins.normalizer_registry import NormalizerRegistry

logger = structlog.get_logger(__name__)


class ExecutionEngine:
    def __init__(
        self,
        scan_repository: ScanRepository,
        scope_guard: ScopeGuardService,
        plugin_manager: PluginManager,
        artifact_store: LocalArtifactStore,
        audit_log_repository: AuditLogRepository,
        tool_result_repository: ToolResultRepository,
        normalizer_registry: NormalizerRegistry,
        default_timeout_seconds: int,
    ) -> None:
        self._scans = scan_repository
        self._scope_guard = scope_guard
        self._plugin_manager = plugin_manager
        self._artifacts = artifact_store
        self._audit = audit_log_repository
        self._tool_results = tool_result_repository
        self._normalizers = normalizer_registry
        self._default_timeout_seconds = default_timeout_seconds

    async def run(self, scan_id: UUID) -> None:
        scan = await self._scans.get(scan_id)
        if scan is None:
            logger.error("scan_execution_missing_scan", scan_id=str(scan_id))
            return

        if scan.status is ScanStatus.CANCELLED:
            # A cancellation request landed before the worker picked this
            # scan up — honor it and never invoke the plugin at all.
            logger.info("scan_execution_skipped_cancelled", scan_id=str(scan_id))
            return

        log = logger.bind(
            scan_id=str(scan_id),
            project_id=str(scan.project_id),
            plugin=scan.plugin,
            initiated_by=str(scan.initiated_by),
        )

        # --- Defense-in-depth re-validation, immediately before execution ---
        try:
            await self._scope_guard.validate_targets(scan.project_id, scan.target_ids)
        except DomainError as exc:
            log.warning("scan_execution_scope_guard_rejected", reason=str(exc))
            await self._scans.fail(scan_id, f"Scope Guard rejected at execution time: {exc}", None)
            await self._write_audit(scan, "scan.failed", {"reason": str(exc)})
            return

        scan = await self._scans.get(scan_id)
        if scan is None:
            log.error("scan_execution_vanished_after_scope_guard", scan_id=str(scan_id))
            return
        if scan.status is ScanStatus.CANCELLED:
            log.info("scan_execution_cancelled_during_scope_check", scan_id=str(scan_id))
            return

        await self._scans.update_status(scan_id, ScanStatus.RUNNING)
        started_at = datetime.now(UTC)
        log.info("scan_started", started_at=started_at.isoformat())
        await self._write_audit(scan, "scan.started", {})

        try:
            result = self._plugin_manager.run(
                scan.plugin, scan.plugin_config, self._default_timeout_seconds
            )
        except Exception as exc:  # noqa: BLE001 - must never leave a scan stuck in `running`
            log.error("scan_execution_unexpected_error", error=str(exc))
            await self._scans.fail(scan_id, f"Unexpected execution error: {exc}", None)
            await self._write_audit(scan, "scan.failed", {"reason": str(exc)})
            return

        scan = await self._scans.get(scan_id)
        if scan is None:
            log.error("scan_execution_vanished_after_plugin", scan_id=str(scan_id))
            return
        if scan.status is ScanStatus.CANCELLED:
            log.info("scan_execution_cancelled_during_execution", scan_id=str(scan_id))
            return

        # --- Milestone 4A: Normalize tool output ---
        normalized_payload: dict[str, object] = {}
        normalizer = self._normalizers.get(scan.plugin)
        if normalizer is not None:
            try:
                normalized_payload = normalizer.normalize(
                    result.stdout, result.stderr, scan.plugin_config
                )
                log.info(
                    "scan_output_normalized",
                    plugin=scan.plugin,
                    payload_keys=list(normalized_payload.keys()),
                )
            except Exception as exc:  # noqa: BLE001 - normalization failure is non-fatal
                log.warning("scan_normalization_failed", error=str(exc))

        logs_path = self._artifacts.write_logs(scan_id, result.stdout, result.stderr)

        # Persist the tool result with normalized payload.
        tool_result = ToolResult(
            id=uuid4(),
            scan_id=scan_id,
            plugin=scan.plugin,
            target=str(scan.plugin_config.get("target", scan.plugin_config.get("hostname", ""))),
            normalized_payload=normalized_payload,
            raw_output_path=logs_path,
            created_at=datetime.now(UTC),
        )
        await self._tool_results.add(tool_result)

        await self._scans.append_log(scan_id, logs_path)
        artifacts_path = self._artifacts.artifacts_directory_if_any(scan_id)

        completed_at = datetime.now(UTC)
        duration_seconds = (completed_at - started_at).total_seconds()

        if result.success:
            await self._scans.complete(scan_id, result.exit_code or 0, artifacts_path)
            log.info(
                "scan_completed",
                completed_at=completed_at.isoformat(),
                duration_seconds=duration_seconds,
                exit_code=result.exit_code,
            )
            await self._write_audit(
                scan,
                "scan.completed",
                {"exit_code": result.exit_code, "duration_seconds": duration_seconds},
            )
        else:
            await self._scans.fail(
                scan_id, result.stderr or "Plugin reported failure", result.exit_code
            )
            log.warning(
                "scan_failed",
                completed_at=completed_at.isoformat(),
                duration_seconds=duration_seconds,
                exit_code=result.exit_code,
                stderr=result.stderr[:500],
            )
            await self._write_audit(
                scan,
                "scan.failed",
                {"exit_code": result.exit_code, "duration_seconds": duration_seconds},
            )

    async def _write_audit(self, scan: Scan, action: str, extra: dict[str, object]) -> None:
        await self._audit.add(
            AuditLogEntry(
                id=uuid4(),
                organization_id=None,
                actor_id=scan.initiated_by,
                action=action,
                target_type="scan",
                target_id=scan.id,
                ip_address=None,
                created_at=datetime.now(UTC),
                after_state={"plugin": scan.plugin, **extra},
            )
        )
