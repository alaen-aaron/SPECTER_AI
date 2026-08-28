"""Autonomous observation ingestion — OBSERVE/UNDERSTAND step (M7.4 Phase 3).

Feeds the controlled feedback loop (OBSERVE -> UNDERSTAND -> UPDATE CONTEXT ->
RE-PLAN -> VALIDATE -> CLASSIFY -> AUTO-EXECUTE / HUMAN-GATE / BLOCK ->
EXECUTE -> OBSERVE). Responsibilities:

- Ingest only **persisted** project state: executed autonomous actions, their
  Scans, ToolResults, Assets, Findings, and correlated services/technologies.
  There is deliberately NO parallel observation database — provenance stays in
  the entities M7.1-M7.3 already write.
- Produce a **deterministic novelty signature** over facts (not planner text):
  tool-result ids, discovered assets, findings, services, technologies, and
  scan terminal states. A scan that completes with zero facts is *not* invented
  evidence; it changes the signature and the next bounded re-plan gracefully
  dedups any repeat — exactly the "no fabricated observations" rule.
- Build a compact **observability snapshot** (`summary`) the orchestrator
  persists into `AutonomousRun.result_summary` so the loop's observations are
  auditable across requests without a migration.

Safety notes:

- Every repository call is keyed by `run.project_id` — one project's
  observation can never leak into another's (tenant isolation by construction).
- All free text (finding titles, asset values, tool payloads) is treated as
  DATA: it flows into read-only summaries/reports, never into configuration or
  a decision. Hostile content such as "ignore all previous instructions"
  embedded in a finding can only ever be observed, never executed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.domain.entities import (
    AutonomousRun,
    AutonomousRunAction,
    Scan,
    ToolResult,
)
from app.domain.repositories import (
    AssetRepository,
    AutonomousRunActionRepository,
    FindingRepository,
    ScanRepository,
    TargetRepository,
    ToolResultRepository,
)


def _canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, default=str)


def _stable_signature(
    *,
    assets: list[dict[str, str]],
    findings: list[tuple[str, str, str]],
    services: list[str],
    technologies: list[str],
    tool_result_ids: list[str],
    scan_completions: list[tuple[str, str, int | None]],
) -> str:
    payload: dict[str, Any] = {
        "assets": sorted(assets),
        "findings": sorted(findings),
        "services": sorted(services),
        "technologies": sorted(technologies),
        "tool_result_ids": sorted(tool_result_ids),
        "scan_completions": sorted(scan_completions),
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ObservedScanFact:
    """One executed action's observation, with full provenance pointers.

    run -> action -> scan -> tool_result -> asset/finding is reconstructible
    from these ids plus the persisted entities; `result_summary` only keeps a
    compact projection, not the raw rows.
    """

    action_id: UUID
    scan_id: UUID
    plugin: str
    target: str
    scan_completed: bool
    exit_code: int | None
    tool_result_ids: tuple[UUID, ...]
    new_asset_values: tuple[str, ...]
    new_finding_titles: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ObservationOutcome:
    """Result of one OBSERVE + UNDERSTAND pass over the run's project state."""

    has_new: bool
    signature: str
    facts: tuple[ObservedScanFact, ...]
    counts: dict[str, object]
    summary: dict[str, object]


class ObservationIngestService:
    """Reads persisted state and decides whether meaningful new facts exist."""

    def __init__(
        self,
        *,
        action_repository: AutonomousRunActionRepository,
        scan_repository: ScanRepository,
        tool_result_repository: ToolResultRepository,
        asset_repository: AssetRepository,
        finding_repository: FindingRepository,
        target_repository: TargetRepository | None = None,
    ) -> None:
        self._actions = action_repository
        self._scans = scan_repository
        self._tool_results = tool_result_repository
        self._assets = asset_repository
        self._findings = finding_repository
        self._targets = target_repository

    async def ingest(self, run: AutonomousRun) -> ObservationOutcome:
        """Compute the current observation snapshot and novelty vs the last one.

        The previous signature is read from `run.result_summary` (persisted by
        the orchestrator after a prior cycle). The returned `summary` carries
        the new signature plus provenance counts for the orchestrator to store.
        """
        executed = [
            a
            for a in await self._actions.list_for_run(run.id)
            if a.status == "executed" and a.scan_id is not None
        ]

        target_values = await self._target_values(run.project_id)
        project_assets = list(
            await self._assets.list_for_project(run.project_id, limit=100)
        )
        project_findings = list(
            await self._findings.list_for_project(run.project_id, limit=100)
        )

        scans: list[tuple[AutonomousRunAction, Scan]] = []
        for action in executed:
            scan = await self._scans.get(action.scan_id) if action.scan_id else None
            if scan is not None:
                scans.append((action, scan))

        # Normative facts — a stable, non-textual fingerprint of everything the
        # run has observed so far.
        tool_result_ids: list[str] = []
        scan_completions: list[tuple[str, str, int | None]] = []
        facts: list[ObservedScanFact] = []
        for action, scan in scans:
            trs: list[ToolResult] = await self._tool_results.list_for_scan(scan.id)
            tool_result_ids.extend(str(tr.id) for tr in trs)
            scan_completions.append(
                (str(action.id), str(scan.id), scan.exit_code)
            )

            scan_assets = [
                a for a in project_assets if a.source_scan_id == scan.id
            ]
            # Findings are correlated FROM tool results, not from scans: a
            # finding belongs to this scan when any of its tool_result_ids
            # was produced by the scan.
            scan_tr_ids = {tr.id for tr in trs}
            scan_findings = [
                f
                for f in project_findings
                if scan_tr_ids.intersection(f.tool_result_ids or [])
            ]
            facts.append(
                ObservedScanFact(
                    action_id=action.id,
                    scan_id=scan.id,
                    plugin=action.plugin or "",
                    target=self._first_target_value(action, target_values),
                    scan_completed=scan.completed_at is not None,
                    exit_code=scan.exit_code,
                    tool_result_ids=tuple(tr.id for tr in trs),
                    new_asset_values=tuple(
                        a.value for a in scan_assets if a.value
                    ),
                    new_finding_titles=tuple(f.title for f in scan_findings),
                )
            )

        services, technologies = self._services_and_technologies(project_assets)

        signature = _stable_signature(
            assets=[
                {
                    "type": a.asset_type.value,
                    "value": a.value,
                    "identity": a.identity_key or "",
                }
                for a in project_assets
            ],
            findings=[
                (str(f.id), f.severity.value, f.status.value)
                for f in project_findings
            ],
            services=services,
            technologies=technologies,
            tool_result_ids=tool_result_ids,
            scan_completions=scan_completions,
        )

        previous = run.result_summary.get("observation_signature")
        has_new = signature != previous
        previous_total = run.result_summary.get("observations_total")
        observations_total = (
            int(previous_total) if isinstance(previous_total, int) else 0
        ) + 1

        counts: dict[str, object] = {
            "executed_actions": len(executed),
            "terminal_scans": sum(1 for _, s in scans if s.completed_at is not None),
            "tool_results": len(tool_result_ids),
            "assets": len(project_assets),
            "services": len(services),
            "technologies": len(technologies),
            "findings": len(project_findings),
            "new_facts": sum(
                1
                for f in facts
                if f.tool_result_ids or f.new_asset_values or f.new_finding_titles
            ),
        }

        summary: dict[str, object] = {
            "observation_signature": signature,
            "observations_total": observations_total,
            "last_observation_at": datetime.now(UTC).isoformat(),
            "observation_counts": counts,
            "provenance": [
                {
                    "action_id": str(f.action_id),
                    "scan_id": str(f.scan_id),
                    "plugin": f.plugin,
                    "target": f.target,
                    "scan_completed": f.scan_completed,
                    "exit_code": f.exit_code,
                    "tool_result_ids": [str(i) for i in f.tool_result_ids],
                    "assets": list(f.new_asset_values),
                    "findings": list(f.new_finding_titles),
                }
                for f in facts
            ],
        }

        return ObservationOutcome(
            has_new=has_new,
            signature=signature,
            facts=tuple(facts),
            counts=counts,
            summary=summary,
        )

    async def _target_values(self, project_id: UUID) -> dict[UUID, str]:
        if self._targets is None:
            return {}
        return {t.id: t.value for t in await self._targets.list_for_project(project_id)}

    @staticmethod
    def _first_target_value(
        action: AutonomousRunAction, target_values: dict[UUID, str]
    ) -> str:
        for tid in (action.target_ids or []):
            value = target_values.get(tid)
            if value:
                return value
        return ""

    @staticmethod
    def _services_and_technologies(
        assets: list[Any],
    ) -> tuple[list[str], list[str]]:
        services: set[str] = set()
        technologies: set[str] = set()
        for a in assets:
            if a.asset_type.value == "technology":
                technologies.add(a.value)
            elif a.asset_type.value == "service":
                services.add(a.identity_key or a.value)
        return sorted(services), sorted(technologies)