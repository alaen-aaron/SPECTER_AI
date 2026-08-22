"""
Execution Engine (Milestone 3, extended Milestone 4A / M5.5).

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

Pipeline integration: after the ToolResult is persisted, the engine
invokes ``CorrelationService.correlate()`` (if injected) so that
Findings are created automatically from the normalised output.

M5.5 Phase 2: after ToolResult persistence the engine optionally
invokes ``AssetService.upsert_from_tool_result()`` to create/update
Asset entities.  After correlation, findings are linked to their
matching assets (by target) and projected to the knowledge graph.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import structlog

from app.application.correlation_service import CorrelationService
from app.application.scope_guard_service import ScopeGuardService
from app.core.metrics import metrics
from app.domain.entities import AuditLogEntry, Scan, ToolResult
from app.domain.exceptions import DomainError
from app.domain.repositories import AuditLogRepository, ScanRepository, ToolResultRepository
from app.domain.value_objects import ScanStatus
from app.infrastructure.storage.local_artifact_store import LocalArtifactStore
from app.plugins.base import CommandRunner
from app.plugins.manager import PluginManager
from app.plugins.normalizer_registry import NormalizerRegistry

if TYPE_CHECKING:
    from app.application.asset_service import AssetService
    from app.application.graph_service import GraphService

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
        correlation_service: CorrelationService | None = None,
        asset_service: AssetService | None = None,
        graph_service: GraphService | None = None,
        runner: CommandRunner | None = None,
    ) -> None:
        self._scans = scan_repository
        self._scope_guard = scope_guard
        self._plugin_manager = plugin_manager
        self._artifacts = artifact_store
        self._audit = audit_log_repository
        self._tool_results = tool_result_repository
        self._normalizers = normalizer_registry
        self._default_timeout_seconds = default_timeout_seconds
        self._correlation = correlation_service
        self._asset_service = asset_service
        self._graph_service = graph_service
        self._runner = runner

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
        metrics.inc_counter("scans_total", tags={"plugin": scan.plugin, "status": "started"})

        try:
            result = self._plugin_manager.run(
                scan.plugin,
                scan.plugin_config,
                self._default_timeout_seconds,
                runner=self._runner,
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

        log.info(
            "scan_tool_result_persisted",
            tool_result_id=str(tool_result.id),
            plugin=tool_result.plugin,
            payload_keys=list(normalized_payload.keys()),
            correlation_enabled=self._correlation is not None,
        )

        # --- M5.5 Phase 2: create/update Assets from the ToolResult ---
        upserted_assets: list = []
        if self._asset_service is not None:
            try:
                upserted_assets = await self._asset_service.upsert_from_tool_result(
                    scan.project_id, tool_result
                )
                log.info(
                    "scan_assets_upserted",
                    asset_count=len(upserted_assets),
                    asset_ids=[str(a.id) for a in upserted_assets],
                )
                for _ in upserted_assets:
                    metrics.inc_counter("assets_upserted_total", tags={"plugin": scan.plugin})
            except Exception as exc:  # noqa: BLE001 — asset creation must not fail the scan
                log.warning(
                    "scan_asset_upsert_failed",
                    scan_id=str(scan_id),
                    error=str(exc),
                    error_type=type(exc).__name__,
                )

        # --- Pipeline integration: create Findings from the ToolResult ---
        findings: list = []
        if self._correlation is not None:
            try:
                findings = await self._correlation.correlate(
                    scan.project_id, [tool_result]
                )
                log.info(
                    "scan_correlation_completed",
                    findings_created=len(findings),
                    finding_ids=[str(f.id) for f in findings],
                )
                for _ in findings:
                    metrics.inc_counter("findings_created_total", tags={"plugin": scan.plugin})
            except Exception as exc:  # noqa: BLE001 — correlation failure must not fail the scan
                log.warning(
                    "scan_correlation_failed",
                    scan_id=str(scan_id),
                    error=str(exc),
                    error_type=type(exc).__name__,
                )

        # --- M5.5 Phase 2: link findings to assets and project to graph ---
        if findings and upserted_assets and self._graph_service is not None:
            await self._link_findings_to_assets_and_graph(
                scan.project_id, findings, upserted_assets, tool_result
            )

        await self._scans.append_log(scan_id, logs_path)
        artifacts_path = self._artifacts.artifacts_directory_if_any(scan_id)

        completed_at = datetime.now(UTC)
        duration_seconds = (completed_at - started_at).total_seconds()

        # --- Final cancellation guard ---
        # A cancellation request can land at any point during
        # normalization / correlation / log-writing above.  Re-check
        # the scan's current status here; if it was cancelled during
        # that window, do NOT overwrite it back to completed/failed.
        scan = await self._scans.get(scan_id)
        if scan is not None and scan.status is ScanStatus.CANCELLED:
            log.info(
                "scan_execution_cancelled_before_final_write",
                scan_id=str(scan_id),
                duration_seconds=duration_seconds,
            )
            return

        if result.success:
            await self._scans.complete(scan_id, result.exit_code or 0, artifacts_path)
            metrics.inc_counter("scans_total", tags={"plugin": scan.plugin, "status": "completed"})
            metrics.observe_histogram(
                "scan_duration_seconds", duration_seconds, tags={"plugin": scan.plugin}
            )
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
            metrics.inc_counter("scans_total", tags={"plugin": scan.plugin, "status": "failed"})
            metrics.observe_histogram(
                "scan_duration_seconds", duration_seconds, tags={"plugin": scan.plugin}
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

    async def _link_findings_to_assets_and_graph(
        self,
        project_id: UUID,
        findings: list,
        assets: list,
        tool_result: ToolResult,
    ) -> None:
        """Link findings to matching assets by target and project to graph."""
        assert self._graph_service is not None

        # Build a lookup of asset value → asset for matching
        target = tool_result.target
        asset_by_value: dict[str, object] = {}
        for asset in assets:
            asset_by_value[asset.value] = asset

        for finding in findings:
            # Try to match finding to an asset by the tool_result target
            matched_asset = None
            for asset in assets:
                asset_val = asset.value.lower()
                target_lower = target.lower()
                if target_lower and (target_lower in asset_val or asset_val in target_lower):
                    matched_asset = asset
                    break

            if matched_asset is not None:
                finding.asset_id = matched_asset.id
                logger.info(
                    "finding_linked_to_asset",
                    finding_id=str(finding.id),
                    asset_id=str(matched_asset.id),
                )

            # Project finding to knowledge graph
            try:
                finding_node = await self._graph_service.upsert_finding_node(
                    project_id,
                    finding.id,
                    finding.title,
                    severity=finding.severity.value,
                )
                # Link finding node → asset node
                if matched_asset is not None:
                    asset_node = await self._graph_service.find_node_by_source(
                        project_id, "assets", matched_asset.id
                    )
                    if asset_node is not None:
                        from app.domain.value_objects import GraphEdgeType
                        await self._graph_service.add_edge(
                            project_id,
                            finding_node.id,
                            asset_node.id,
                            GraphEdgeType.EVIDENCED_BY,
                        )
            except Exception as exc:  # noqa: BLE001 — graph projection must not fail the scan
                logger.warning(
                    "finding_graph_projection_failed",
                    finding_id=str(finding.id),
                    error=str(exc),
                )
