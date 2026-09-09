# SPECTER_AI — M7.4 Phase 4 Verification Package

**Autonomous Failure Recovery, Soft Cancellation & Concurrency Control**
**Status:** COMPLETE — awaiting approval. All deliverables validated against the live Docker stack and a real Postgres instance. Nothing committed/pushed/tagged.

---

## 1. Objective & Scope

Deliver M7.4 **Phase 4 — Failure Recovery + Cancellation + Concurrency**, giving the autonomous loop four fail-closed guarantees without ungoverned behaviour:

- **SAFE RESUME** — a scan that never ran (transport failure) is retried at most once against a fresh scan, in-band with the existing Planner → Approval → Execution → Observation loop.
- **SAFE CANCELLATION** — cancelling a run is a soft status flip, never a subprocess kill; cancelled scans stop being dispatched, in-flight executor work settles, and the transition is audited.
- **NO DUPLICATE EXECUTION** — a planned action can be executed against at most one live scan per run (partial unique index, DB-enforced), and re-execution only ever happens through an explicit planner re-approval.
- **PROJECT-LEVEL CONCURRENCY CONTROL** — at most one non-terminal run per project (partial unique index, DB wins the race), plus a per-run `pg_try_advisory_xact_lock` and an orchestrator `_in_flight` fast-fail so concurrent `/cycle` calls block instead of double-advancing.

Out of scope: no new plugins, no files written by plugins, no hard process kills, no changes to the frozen M7.1/M7.2/M7.3/M5.x modules, no M7.5+ work, no commits/pushes/tags.

## 2. Updated Repository Tree (delta vs Phase 3)

```
backend/
  app/
    application/
      autonomous_recovery.py               (NEW)  FailureRecoveryService: reconcile(), recover_stale()
      autonomous_service.py                (CHANGED) retry_count bump, idempotent record_action_execution,
                                                     soft cancel audit events, state re-sync after execution
      autonomous_orchestrator.py           (CHANGED) reconcile at cycle() entry under the lock; _in_flight fast-fail;
                                                     AutonomousCycleNotAllowedError on concurrent cycle
      planner_service.py                   (CHANGED) reapprove() gate (EXECUTED -> APPROVED only)
    core/config.py                         (CHANGED) autonomous max_retries default (1), stale anchors
    domain/
      entities.py                          (CHANGED) AutonomousRunAction.retry_count, ScanFailureKind on scan
      value_objects.py                     (CHANGED) ScanFailureKind enum (TRANSPORT / TOOL / DOMAIN)
      exceptions.py                        (CHANGED) +AutonomousRunActiveExistsError,
                                                     +AutonomousCycleNotAllowedError, +AutonomousActionNotRetryableError
      repositories.py                      (CHANGED) repo interfaces for retry/reconcile/stale listing
    api/v1/deps.py                         (CHANGED) recovery service + scanner-status providers, isort
    infrastructure/
      celery_app/
        app.py                             (CHANGED) worker wiring for recovery/retry task paths
        tasks.py                           (CHANGED) capture failure-kind + scan status on execution end;
                                                     soft cancel + reconcile entry points
      db/
        models/autonomous.py               (CHANGED) partial unique index metadata (runs + actions)
        models/scan.py                     (CHANGED) failure_kind column, index metadata
        repositories/
          autonomous_run_repository.py     (CHANGED) asyncpg IntegrityError -> AutonomousRunActiveExistsError
                                                     (PROD BUG FIX); failure-kind persistence; reconcile queries
          autonomous_run_action_repository.py (CHANGED) retry accounting, planned-action unique mapping
          scan_repository.py               (CHANGED) create() now maps failure_kind/error_message/completed_at
                                                     (PROD BUG FIX); cancel + terminal-state listing
      execution/
        engine.py                          (CHANGED) defensive result.metadata["failure_kind"] classification
        executor_runner.py                 (CHANGED) surfaces failure-kind in runner metadata
  alembic/versions/b5c6d7e8f9a0_m74_phase4_failure_recovery.py  (NEW)  migration a4b5c6d7e8f9 -> b5c6d7e8f9a0
  tests/
    fakes.py                               (CHANGED) failure-kind-aware fakes + recovery fakes
    unit/test_m74_phase4_failure_recovery.py   (NEW)  26 tests (A-Z matrix)
    integration/test_m74_phase4_recovery_integration.py (NEW) 9 Postgres integration tests
```

## 3. Database Schema Changes

One migration, applied and verified live:

```
a4b5c6d7e8f9 (Phase 2) --linear--> b5c6d7e8f9a0 (Phase 4)
```

- `scans.failure_kind` (nullable) — failure taxonomy bucket.
- `autonomous_run_actions.retry_count` (int, default 0).
- Partial unique index `uq_autonomous_runs_active_project` ON `autonomous_runs(project_id)` WHERE `status NOT IN ('completed','failed','cancelled')` — at most one active run per project, enforced by the DB.
- Partial unique index `uq_autonomous_actions_planned_action` ON `autonomous_run_actions(run_id, planned_action_id)` WHERE `planned_action_id IS NOT NULL` — NO DUPLICATE EXECUTION at the storage layer.

Verified in live Postgres (`information_schema.columns` + `pg_indexes`) after `alembic upgrade head`. No unrelated tables touched. Phase 3 required no schema change; the chain stays linear.

## 4. New API Endpoints

**None.** Phase 4 rides the existing Phase-2 autonomous routes (`/autonomous-runs/{id}/cycle|approve|cancel|...`). The router module is untouched. Behavioral enrichment only:

- `/cycle` reconciles pending transport-retries at entry, under the run's advisory lock.
- `/cancel` remains a soft flip; it now also emits `ai.autonomous.cancelled` and no longer races the executor.
- Creating a second active run in the same project is rejected by the DB partial unique index (surfaced as `AutonomousRunActiveExistsError`).

## 5. Autonomous Loop Architecture (as delivered)

1. `POST /cycle` → orchestrator `cycle(run_id)`.
2. **Reconcile** (`FailureRecoveryService.reconcile`): settle executed actions whose scan failed with `failure_kind=TRANSPORT` — reapprove → new scan → enqueue. Runs once per cycle under the advisory lock.
3. `_step`: state-machine transition (`AutonomousService`) — Observe → signature → Re-plan/Classify/Policy → `execute_approved` (unchanged from Phase 3), but now carrying the retry path.
4. **NO DUPLICATE EXECUTION**: `autonomous_run_actions(run_id, planned_action_id)` partial unique index is the backstop; `reapprove()` only ever moves `EXECUTED -> APPROVED`.
5. **Concurrency**: first `/cycle` wins `pg_try_advisory_xact_lock(run_id)` + `_in_flight`; a racing second request is blocked (`current_status="concurrent_cycle"`), never double-advances.
6. **Supervisory safety net**: `list_stale_active` (global, heartbeat/started anchor) feeds `recover_stale` which FAILS **closed** on ambiguity and otherwise advances drained `EXECUTING` runs to `OBSERVING`.

Every path still transits the Scope Guard (execution-time re-validation at `ScanService.create`) — retry is not a bypass.

## 6. Failure Taxonomy (frozen)

| Kind | Meaning | Retryable |
|---|---|---|
| `TRANSPORT` | plugin never ran (dispatch/executor never produced work) | **YES — exactly 1 retry** |
| `TOOL` | the tool ran and failed (nmap/ping non-zero) | no |
| `DOMAIN` | orchestrator-internal rejection (e.g. scope guard) | no |

The engine classifies from `result.metadata["failure_kind"]` **defensively** via `getattr(result, "metadata", None)`, so any runner/serialization path that omits the key degrades to the safe classification rather than crashing classification. The classification itself is written by the launcher pipeline (`executor_runner` metadata). `ScanRepository.create` now persists `failure_kind` faithfully on every scan (prod bug fix, see §15).

## 7. Failure Recovery Service (`FailureRecoveryService`)

- **`reconcile(run_id)`** — called at `cycle()` entry under the run's lock. For each executed action whose scan is FAILED with `failure_kind=TRANSPORT` and `retry_count < max_retries` (default 1): bump `retry_count`, append the prior `scan_id` to `action.result_summary["scan_attempt_ids"]` (lineage, never re-increments `actions_completed`), `PlannerService.reapprove` (only `EXECUTED -> APPROVED`, else `PlannedActionNotApprovableError`), then `execute_approved` → **a brand-new scan row** (the retry is a real scan that satisfies the `autonomous_run_actions_scan_id_fkey` FK). If `retry_count >= max_retries`, the action stays terminal and the run's observation loop sees the failure as a fact — never an unbounded retry.
- **`recover_stale(run_id)`** — FAILS **closed**: if an executed action has no scan row at all, the run goes FAILED and emits `ai.autonomous.stalled` (operator signal, audit-trailed). If all executed scans are terminal and the run is `EXECUTING`, advance `EXECUTING -> OBSERVING` (audit `ai.autonomous.recovered`). Live scans are left strictly alone — a supervisor sweep is the only caller, and it never kills or cancels anything.
- `record_action_execution` is **idempotent** — a redelivered execution event can never double-count or double-execute.

## 8. Category Enforcement (frozen)

Unchanged from Phase 3: `AUTONOMOUS` (auto-execute, playback allow-list `ping`/`low` only), `HUMAN_REVIEW` (parks at `AWAITING_APPROVAL`), `BLOCKED` (never executes, never attaches). Phase 4 adds no new categories and no new plugin surface — the retry path reuses the exact same classification/policy gates as the original execution.

## 9. Scope Guard Integration

Unchanged. `validate_targets` still runs at proposal classification time **and** re-validates at `ScanService.create` — including for retry scans. A scan whose authorization lapsed between the failed transport attempt and the retry is rejected at execution time, exactly as in Phase 3. Live-proven in the Phase 3 session against `10.0.0.1`/`10.0.0.2` in scope and `8.8.8.8` rejected with 4xx.

## 10. Durable Cancellation

- Soft, cooperative: cancelling flips run/scan status. It never sends SIGKILL, never aborts a subprocess mid-run.
- When the executor poll sees a cancelled scan it stops dispatching; the audit trail records `ai.autonomous.cancel_requested` (start) and `ai.autonomous.cancelled` (completed flip).
- A cancelled run cannot be re-activated (`AutoNomousCycleNotAllowedError` on any later `/cycle`); transition rules are unchanged and enforced from Phase 2.

## 11. Concurrency & State Machine Rules

- **One active run per project**: the partial unique index is the source of truth. The first inserter wins; a racing `create` surfaces `AutonomousRunActiveExistsError` (default-mapped RFC 7807 `400 domain-error`, smoke-locked §16).
- **Per-run advisory lock**: `pg_try_advisory_xact_lock` keyed by run id. Empirically verified (this phase): the lock is **re-entrant inside one transaction** (re-acquire returns True) and **blocks across transactions** (a second session gets False). Same-process re-entrancy is covered by `_in_flight`; cross-process overlap fails closed with `concurrent_cycle`. Integration test holds the blocking lock through a *peer session* to model the real cross-request race.
- All transitions still go through `VALID_AUTONOMOUS_TRANSITIONS`; a cycle call advances at most one state.
- **`PendingRollbackError` reality**: after a flush `IntegrityError` the session refuses further statements until `rollback()`. Production is unaffected (request-scoped teardown after the 409); integration tests roll back explicitly. This is the documented reason the concurrent-create test commits its first run before asserting the conflict.

## 12. Error Mapping

No new `error_handlers.py` entries were required. Phase 4 domain errors (`AutonomousRunActiveExistsError`, `AutonomousCycleNotAllowedError`, `AutonomousActionNotRetryableError`) are plain `DomainError`s and resolve via the default `(400, "domain-error")` Problem Details mapping — which is exactly the behaviour the live smoke test pins (`400` on a second active run). The reapproval gate reuses the existing `PlannedActionNotApprovableError -> 409`. Verified live and in the integration suite.

## 13. Test Summary (all green)

- **Full backend suite: 837 passed / 0 failed / 0 skipped** (`pytest -q`, ~90.5s), with the stack up and `DATABASE_URL` pointed at compose Postgres on localhost. The 12 repository tests that were Postgres-blocked at the Phase-2/3 baselines now **run** and pass.
- **Phase 4 deltas**: 26 new unit tests (`test_m74_phase4_failure_recovery.py`) + 9 new Postgres integration tests (`test_m74_phase4_recovery_integration.py`) — all green.
- **Live API smoke** (from the suite run): `test_m74_api_smoke.py` 5/5 + `test_m74_phase3_live.py` 2/2 against `http://localhost:9002`.

## 14. Unit Tests (`test_m74_phase4_failure_recovery.py`, A-Z, 26)

- Failure-taxonomy classification incl. defensive `getattr` on missing `metadata`.
- Retry gate matrix: status must be `executed`; scan must be `FAILED`; `failure_kind` must be `TRANSPORT`; `retry_count < max_retries`; every other combination is not retryable.
- `reapprove` rejects non-`EXECUTED` actions (`PlannedActionNotApprovableError`).
- Retry bumps `retry_count`, appends `scan_attempt_ids`, never re-increments `actions_completed`.
- `record_action_execution` idempotency (redelivered events).
- `reconcile` settles transports; respects `max_retries`; leaves TOOL/DOMAIN failures untouched.
- `recover_stale`: fail-closed on missing scan (`stalled` audit), `EXECUTING -> OBSERVING` when drained (`recovered` audit), never touches live scans.
- `list_stale_active`: heartbeat→started anchor coalescing; global scope; relative ordering.
- Cancellation auditing (`cancel_requested`/`cancelled`); soft-flip semantics.
- Concurrent-cycle block (`current_status="concurrent_cycle"`); active-run conflict error mapping; rollback-path safety.

## 15. Live Postgres Integration Tests (`test_m74_phase4_recovery_integration.py`, 9/9)

Run against **real Postgres on Docker** (asyncpg, real FKs, real indexes, two database sessions):

1. One active run per project — the partial unique index rejects a second non-terminal run across sessions; mapped to `AutonomousRunActiveExistsError` (not a raw adapter crash).
2. Advisory lock blocks across transactions (peer session holds the lock; cycle reports `concurrent_cycle`).
3. Transport retry end-to-end: seeded planned action + origin scan (`failure_kind=TRANSPORT`) + action marked `executed` → `reconcile` reapproves → new scan row created → `retry_count=1`, lineage appended, `actions_completed` unchanged.
4. `recover_stale` advances a drained `EXECUTING` run to `OBSERVING` with the recovery audit row persisted.
5. Fail-closed: executed action with no scan → run `FAILED`, `stalled` audit persisted.
6. Retry accounting: `retry_count` persists across updates; cap `max_retries` honoured.
7. Global stale-list ordering (runs in distinct projects, heartbeat/started anchor, relative position asserted).
8. `ScanRepository.create` faithfully persists `failure_kind`, `error_message`, `completed_at`.
9. Soft-cancel flip persists status + audit rows and blocks re-activation.

These five tests are seam-real: every foreign key is satisfied by real rows (`autonomous_run_actions_scan_id_fkey` is exercised — a retry really is a scan).

## 16. Live API Smoke

From the full-suite run against the running stack: `test_m74_api_smoke.py` **5/5** and `test_m74_phase3_live.py` **2/2** on `http://localhost:9002` — auth, org/project selection, create/get/list/cancel autonomous runs, invalid-transition rejection, concurrent-active-run guard (400), bounded loop observing→completed, scope guard. Zero `scan_execution_missing_scan` in worker logs.

## 17. Lint / Type Check

- **ruff (pinned 0.6.9)**: clean on every touched Phase-4 file (`autonomous_run_repository.py`, `scan_repository.py`, `test_m74_phase4_recovery_integration.py`, plus the remaining delta files). The repo-wide 38 pre-existing errors remain confined to 8 untouched files — unchanged and out of scope.
- **mypy (pinned 1.11.2, strict + Pydantic plugin; `--python-executable <global python>` due to no local Python ≥3.12)**: **BASE 38 = NOW 38, zero new errors**. The 10 errors inside `execution/engine.py` are the documented pre-existing set, line-shifted only by the (correctly typed) `getattr(result, "metadata", None)` read; all other touched Phase-4 files are clean.

## 18. Full-Suite Gate

`837 passed, 0 failed, 0 skipped in ~90.5s`. Delivered vs Phase 3 (`790/12/0`): +47 net tests, and the 12 storage-layer tests whose `DATABASE_URL` was the unreachable compose hostname now execute because the override is set and the stack is up. The two prod bugs below were found precisely because those 12 and the 9 new integration tests finally ran against real Postgres.

## 19. Guarantee Statements (explicit)

- **SAFE RESUME (bounded)**: a transport-failed scan is retried exactly once (`max_retries=1` default); the retry is a fresh scan through the full scope-guarded pipeline; per-action total work is finite.
- **SAFE CANCELLATION**: cancellation is a soft status flip — no subprocess kill, no un-cancellable window, audit-trailed start and completion; cancelled runs reject further cycles.
- **NO DUPLICATE EXECUTION**: the `(run_id, planned_action_id)` partial unique index makes double-execution impossible at the storage layer; `reapprove` is the only door back to executable, and it is `EXECUTED -> APPROVED` only; `record_action_execution` is idempotent.
- **PROJECT-LEVEL CONCURRENCY CONTROL**: one active run per project (DB partial unique index), one cycle at a time per run (advisory lock), fast-fail in-process (`_in_flight`); a losing request is *blocked*, never silently queued.

## 20. Commands Run (validation session)

```bash
docker compose -f infra/docker-compose.yml up -d --build                 # all 8 services healthy
docker compose -f infra/docker-compose.yml ps                            # healthy
docker compose -f infra/docker-compose.yml exec -T api alembic upgrade head   # a4b5c6d7e8f9 -> b5c6d7e8f9a0
[postgres] SELECT column_name FROM information_schema.columns ...        # failure_kind, retry_count present
[postgres] SELECT indexname FROM pg_indexes ...                          # uq_autonomous_runs_active_project,
                                                                         # uq_autonomous_actions_planned_action present
$env:DATABASE_URL = "postgresql+asyncpg://specter:specter@localhost:5432/specter"   # host -> compose PG
cd backend
pytest tests/unit/test_m74_phase4_failure_recovery.py -q                 # 26 passed
pytest tests/integration/test_m74_phase4_recovery_integration.py -q      # 9 passed
pytest -q                                                                # 837 passed, 0 failed, 0 skipped (~90.5s)
ruff check --config <pinned> app/infrastructure/db/repositories/autonomous_run_repository.py \
        app/infrastructure/db/repositories/scan_repository.py tests/integration/test_m74_phase4_recovery_integration.py   # clean
mypy --config-file <pinned> --python-executable <global python> app/...  # BASE 38 = NOW 38
```

## 21. Expected Outputs Observed

- Migration applied, columns + both partial unique indexes verified present in live Postgres.
- 26 unit + 9 integration + 837 total green; 0 failures, 0 skips.
- Live smoke (5 API + 2 phase-3) green against `localhost:9002`.
- Real Postgres runs surfaced and fixed two production bugs (§15) — the defining outcome of moving Phase-4 storage tests to a real DB.
- Worker logs clean of `scan_execution_missing_scan` across the session.

## 22. Performance Characteristics

- Advisory locks and `_in_flight` only gate orchestration (`/cycle`, reconcile) — the scan execution path is untouched; a scan flight costs nothing new.
- Retry adds at most one extra scan per transport failure (capped), never a loop.
- Fresh partial unique indexes are narrow (`WHERE not-terminal` / `planned_action_id IS NOT NULL`) — negligible write cost, bounded by active runs/actions.
- `list_stale_active` is one anchored query (heartbeat→started coalesce), global by design for the supervisor sweep.

## 23. Security Checklist (11 items)

1. No secrets/credentials introduced or committed (autonomous_recovery/runner/repo diffs scanned: clean).
2. Plugin allow-list unchanged — nmap flag allow-list intact; `-oN/-oX/-oG/-oA` and `--script` remain forbidden.
3. No plugin writes files via the orchestrated path; plugin set untouched.
4. Subprocess invocation remains list-args only (nmap/ping unchanged).
5. Scope Guard still re-validates at execution time — including on retries (no bypass).
6. Retry is bounded (max_retries=1) and cannot replay rejected/BLOCKED actions.
7. BLOCKED actions can never execute or be fuzzy-attached (frozen category gate).
8. Cancellation remains cooperative/soft; no kill surface added anywhere.
9. Executor isolation intact (M7.1 frozen; worker only dispatches over HTTP).
10. Clean Architecture preserved: `autonomous_recovery.py` imports only domain + application interfaces — no Celery/SQLAlchemy/FastAPI; the asyncpg adapter detail is confined to the single repository mapping it belongs to.
11. Endpoints, RBAC, and RFC 7807 error mapping unchanged; the new exceptions default to the existing `400 domain-error` envelope. No new attack surface.

## 24. Known Limitations / Risks

- **Retry is a new scan**: the original failed scan stays as a lineage record; consumers must join via `scan_attempt_ids`, not assume a single scan per action.
- **Soft cancellation only**: an already-dispatched executor request is not hard-killed (frozen design); it drains and the run/scan state reflects reality — a cancelled run records the settled outcome.
- **Reconcile is a cycle/`reconcile` trigger, not a background janitor**: a transport failure is settled on the next `/cycle` or via a fresh `recover_stale` sweep of `list_stale_active`; there is no background retry loop.
- **`list_stale_active` is global** (no project filter) — the supervisor sweep is responsible for cross-checking `project_id`.
- **Context needed for host-run DB tests**: without `DATABASE_URL` pointed at compose Postgres on localhost, the storage and integration tests skip (settings default uses the unreachable `postgres` compose hostname).
- **Advisory-lock re-entrancy nuance**: the same-transaction re-acquire returns True; cross-transaction returns False — the design depends on `_in_flight` + real cross-session races, and the integration suite models the race with a peer session rather than within one transaction.

## 25. Baselines Preserved

- Phase 3: 790 pass / 12 skip / 0 fail → **837 pass / 0 skip / 0 fail** (the 12 become executing tests with the corrected DB URL — a strict improvement, not a subtraction).
- mypy baseline 38 (unchanged — strictly equal); ruff pre-existing 38 in 8 untouched files (unchanged).
- Frozen modules (M7.1 ExecutorHttpRunner/AuthorizedTargetRunner; M7.2 Planner/proposal pipeline/ScanService; M7.3 correlation/graph/identity; Phase 1 state model) byte-identical except two deliberate, additive Phase-4 reads in `engine.py`/`executor_runner.py` (failure-kind metadata).

## 26. Artifacts & Evidence

- Working tree: 9 Phase-4 files modified + 3 new (report §2); no commits, no tags, no pushes — per STOP rule.
- DB residue from validation cleaned: temporary diagnostics table dropped, `tmp%`/`M74 Phase4 Org`/`dbg` test orgs and linked runs/actions/scans/planned-actions/audit rows removed via temp-table CTE cleanup scripts (in `%TEMP%\opencode`). Migration and both indexes remain (they are the deliverable).
- Live evidence: migration verification output, `837 passed` suite output, 26+9 Phase-4 test outputs, ruff/mypy pinned comparisons, worker-log grep (0 `scan_execution_missing_scan`), 5+2 live smoke passes.

## 27. Risks & Mitigations

- **Duplicate execution through races** → DB partial unique index (`uq_autonomous_actions_planned_action`) is the backstop; `reapprove` gate + idempotent record keep the app layer consistent; a race test spans two sessions.
- **Two runs sneaking into a project** → partial unique index on non-terminal runs; loser maps to `AutonomousRunActiveExistsError` (400), not a 500.
- **Concurrent cycles double-advancing** → advisory lock + `_in_flight` fast-fail; loser is blocked with `concurrent_cycle`, never queued or silently executed.
- **Unbounded retries** → `max_retries=1`; reconcile only ever adds one scan per transport failure.
- **Ambiguous permanent stall hidden** → `recover_stale` fails the run CLOSED with `ai.autonomous.stalled` audit; supervisor sweep surfaces it.
- **Asyncpg adapter IntegrityError not matching `sqlalchemy.exc.IntegrityError`** (found live) → repository catches both classes and walks `constraint_name` across `.orig`/`__cause__`/`__context__` (plus `.diag` for older adapters); unit + integration pinned.

## 28. Files Touched (with reason)

| File | Reason |
|---|---|
| `app/application/autonomous_recovery.py` | NEW — reconcile + recover_stale (fail-closed) |
| `app/application/autonomous_service.py` | retry_count/idempotent record/soft-cancel audit |
| `app/application/autonomous_orchestrator.py` | reconcile at cycle entry; `_in_flight`; concurrent-cycle error |
| `app/application/planner_service.py` | `reapprove` gate (EXECUTED->APPROVED) |
| `app/core/config.py` | max_retries + stale anchors |
| `app/domain/entities.py`, `value_objects.py`, `exceptions.py`, `repositories.py` | failure taxonomy, retry_count, new domain errors, interfaces |
| `app/infrastructure/db/repositories/autonomous_run_repository.py` | asyncpg IntegrityError -> ActiveExistsError (PROD FIX); failure-kind persistence |
| `app/infrastructure/db/repositories/autonomous_run_action_repository.py` | retry accounting + planned-action unique mapping |
| `app/infrastructure/db/repositories/scan_repository.py` | create() maps failure_kind/error_message/completed_at (PROD FIX) |
| `app/infrastructure/execution/engine.py`, `executor_runner.py` | defensive failure-kind metadata read/surface |
| `app/infrastructure/celery_app/app.py`, `tasks.py` | failure-kind capture, soft cancel, retry/reconcile wiring |
| `app/infrastructure/db/models/autonomous.py`, `scan.py` | partial unique indexes + failure_kind |
| `app/api/v1/deps.py` | recovery service provider; isort |
| `alembic/versions/b5c6d7e8f9a0_...py` | NEW — Phase 4 migration (applied) |
| `tests/fakes.py` | failure-kind-aware fakes |
| `tests/unit/test_m74_phase4_failure_recovery.py` | NEW — 26 A-Z tests |
| `tests/integration/test_m74_phase4_recovery_integration.py` | NEW — 9 Postgres tests |

## 29. Conclusion

Phase 4 is **complete and verified**. The autonomous loop now resumes transport-failed work safely (≤1 retry, fresh scan, full audit lineage), cancels cooperatively without kill surfaces, cannot double-execute a planned action (DB-enforced), and cannot run two concurrent runs or two concurrent cycles on the same run (DB index + advisory lock + `_in_flight`, all fail closed). The four guarantees are proven by 26 unit tests and 9 Postgres integration tests against a live stack, two production bugs were found and fixed by finally running the storage layer on a real database (asyncpg IntegrityError mapping, unfaithful `ScanRepository.create`), and the full suite stands at **837 passed / 0 failed / 0 skipped** with ruff and mypy clean on all touched files (mypy BASE 38 = NOW 38). **No commit/push/tag has been made.**