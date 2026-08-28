# SPECTER_AI — M7.4 Phase 3 Verification Package

**Autonomous Feedback, Observation & Controlled Re-planning**
**Status:** COMPLETE — awaiting approval. All deliverables validated against the live Docker stack. Nothing committed/pushed/tagged.

---

## 1. Objective & Scope

Deliver M7.4 **Phase 3 — Autonomous Feedback (Observation → Novelty → Re-plan), without ungoverned autonomy**:

- Re-plan travels the existing **Scout → Planner → Approval → Execution → Observation** loop (always through the Scope Guard, ScanService, Celery, M7.1 Executor).
- **Risk-based action categories** enforced: `AUTONOMOUS` (auto-execute, allow-listed `{ping}` × `{low}`), `HUMAN_REVIEW` (stalls at `AWAITING_APPROVAL`; the loop must stop), `BLOCKED` (never executes / never attaches).
- Fix the **§18 dispatch-after-commit race**: a scan is handed to Celery only *after* the DB transaction commits, eliminating `scan_execution_missing_scan` on freshly-committed rows.
- Prove the loop **terminates** (bounded iterations, `max_actions` budget, exact `observation_signature` novelty halting) against a live stack, end to end.

Out of scope: no new plugins, no prompt-driven unsafe paths, no files written by plugins, no commits/pushes/tags, no M7.5+ work.

## 2. Updated Repository Tree (delta vs Phase 2)

```
specter-ai/
├── backend/
│   ├── app/
│   │   ├── application/
│   │   │   ├── autonomous_observation.py        (NEW)  ObservationIngestService, ObservationOutcome
│   │   │   └── autonomous_orchestrator.py       (CHANGED) cycle() re-plan driver, category pipeline
│   │   ├── api/v1/
│   │   │   ├── deps.py                          (CHANGED) +get_tool_result_repository,
│   │   │   │                                    +get_scan_task_dispatcher, +observation wiring,
│   │   │   │                                    isort of autonomous_* imports, removed pre-existing
│   │   │   │                                    unused SqlAlchemyAssetObservationRepository import
│   │   │   └── routers/autonomous.py            (CHANGED) ingest wiring on cycle/approve paths
│   │   ├── infrastructure/
│   │   │   ├── celery_app/
│   │   │   │   ├── dispatch_after_commit.py     (NEW)  queue_dispatch / bind_sender /
│   │   │   │   │                                 drain_pending_dispatches, ContextVar buffer
│   │   │   │   └── dispatcher.py                (CHANGED) AfterCommitScanTaskDispatcher added
│   │   │   └── db/session.py                    (CHANGED) drain after commit; never on rollback
│   └── tests/
│       ├── fakes.py                             (CHANGED) +FakeToolResultRepository, +FakePlannedActionRepository
│       ├── unit/
│       │   ├── test_m74_phase3_feedback.py      (NEW)  20 tests
│       │   ├── test_m74_orchestrator.py         (CHANGED) re-plan pipeline tests
│       │   └── test_dispatch_after_commit.py    (NEW)  §18 dispatch tests
│       └── integration/
│           └── test_m74_phase3_live.py          (NEW)  live bounded-loop + scope-guard + dispatch smoke
```

## 3. Database Schema Changes

**None.** Phase 3 stores `observation_signature` inside the existing `AutonomousRun.result_summary` JSON column — no migration, no new tables, no breaking change to existing rows.

## 4. New API Endpoints

**None.** Phase 3 rides existing Phase-2 autonomous routes (`/autonomous-runs/{run_id}/cycle|approve|...`). The only enrichment is behavioral: `cycle` now runs Observation → Novelty → Re-plan against the live DB via the new `ObservationIngestService`.

## 5. Autonomous Loop Architecture (as delivered)

1. `POST /cycle` → orchestrator.
2. `_step`: state-machine transition (`AutonomousService`) → **Observe** (`ObservationIngestService`) → compute `observation_signature`.
3. If `has_new` → **Re-plan** (`PlannerService.propose_next_actions` with known facts) → **Classify** each proposal (`ActionClassificationPolicy`) → **Policy** (`filter_review`) → `execute_approved()`.
4. `execute_approved`: validated + classified as `AUTONOMOUS` only → `ScanLauncher` → `ScanService.create` → **Scope Guard re-validation** → after-commit Celery dispatch (§18).
5. Duplicate observation (`signature == previous`) → halt cycle, `COMPLETED`. Budget exhausted (`max_actions`) → `COMPLETED`. Terminal states always reached via bounded transitions.

Every re-plan path transits the validator (`ActionProposalValidator`) — a low-risk `ping` proposal survives; anything touching unauthorised hosts or dangerous flags is rejected before classification.

## 6. Observation Subsystem (ObservationIngestService)

- Inputs: run, project, **all** tool results (any source), findings (linked via `tool_result_ids` intersection).
- Output `ObservationOutcome`: `signature` (SHA-256 of sorted json payload incl. all `ToolResult` content + counts), `executed_scan_ids`, `observations_total` (`isinstance`-guarded cumulative), `has_new`, `counts` (`dict[str, int]`).
- **Data-inert**: tool/finding content is never ad-hoc-attached to a future action payload (avoids prompt-injection-ish feedback loops / unbounded growth).
- Discovery FYI: `_first_target_value` is a staticmethod → always called via `self._first_target_value(...)`.

## 7. Re-Plan / Approval Handoff

- `has_new == True` with proposals → next cycle runs planner.
- `HUMAN_REVIEW` proposal → run parks in `AWAITING_APPROVAL`; cycle returns without executing; `/approve` (approve-all) resumes → `EXECUTING`.
- The `_plan_and_execute` flow under test: create → PLANNING → propose → classify → restricted execute → OBSERVING → duplicate signature → COMPLETED. Verified in unit + live tests.

## 8. Category Enforcement (frozen)

| Category | Behavior | Enforced by |
|---|---|---|
| `AUTONOMOUS` | auto-execute; playback allow-list **`ping` only**, risk **`low` only** | orchestrator `_screen` + `ActionClassificationPolicy` playback |
| `HUMAN_REVIEW` | blocks at `AWAITING_APPROVAL`; loop halts | orchestrator + `AutonomousService.transition` |
| `BLOCKED` | never executes, never fuzzy-attaches | orchestrator `_screen` (skip) + plugin allow-list |

## 9. Scope Guard Integration

- Unchanged `validate_targets` (exact-value match on `allowed_targets`, temporal validity, any-active-record-covering semantics).
- Called **both** at proposal classification time and at `ScanService.create` (execution-time re-validation), preserved from Phase 2 — a scan created while authorization changed is still rejected at run time.
- Live-proven: fresh project authorized `["10.0.0.2"]`; target `8.8.8.8` fails scope-check (4xx) **and** direct `POST /scans` for it is rejected — the same guard the loop depends on.

## 10. §18 Dispatch-After-Commit Fix

- `dispatch_after_commit.py` (`queue_dispatch`): buffers scan ids in a `ContextVar` (`_pending_scan_ids`, deduped). `bind_sender` installs a `ScanTaskDispatcher` that only queues.
- `db/session.py`: `get_db_session` calls `drain_pending_dispatches()` **only after `await session.commit()`**; rollback never drains. Dispatch is durability-first: a drain failure or an unbound sender is logged and never fails the user request.
- Delivered scan ids are compared with `str(scan_id)` normalization (PL I-52 production incident regression).
- Live: worker logs show zero `scan_execution_missing_scan` events over the whole validation session.

## 11. Concurrency & State Machine Rules

- All transitions via `VALID_AUTONOMOUS_TRANSITIONS`; illegal transitions raise and cannot double-advance.
- Single-writer loop: one cycle call advances at most one state; `_write_result_summary` persists observation facts within the same transaction as the state flip.
- Soft cancellation semantics unchanged (status flip, not process kill).

## 12. Error Mapping

- No new error-handler entries required (Phase 3 throws existing domain exceptions; all already mapped to RFC 7807). Live 4xx responses observed for out-of-scope targets and invalid state transitions confirm the mapping path end to end.

## 13. Test Summary (all green)

- **Full backend suite: 790 passed / 12 skipped / 0 failed** (`pytest -q`).
- 12 skips = repository integration tests requiring Postgres on the pytest `DATABASE_URL` (env-blocked, same class as Phase-2 baseline).
- Live integration suite (stack up): **8 passed** = 6 × `test_m74_api_smoke.py` + 2 × `test_m74_phase3_live.py`.

## 14. Unit Tests

- `test_m74_phase3_feedback.py` (20): ObservationIngestService signature stability/novelty, counts guard, finding linkage via `tool_result_ids`, recompute determinism, `_ping_spec` target dedupe (same-seed callers).
- `test_m74_orchestrator.py` (updated): full re-plan loop, category filtering, awaiting-approval path (scripted 3-cycle), outcome `Mapping[str, object]` typing.
- `test_dispatch_after_commit.py`: buffer semantics, dedupe, deliver-after-commit, deliver-on-rollback = none, commit/rollback hook wiring.
- **54 passed** in the phase-3 trio.

## 15. Live Integration Tests (`test_m74_phase3_live.py`)

1. **`test_phase3_bounded_loop_observes_and_completes`** — fresh project (draft → authorized → active), `allowed_targets=["10.0.0.1"]`, autonomous run (`ping`, `max_actions=4`); drives `/cycle` up to 24×; approves on `AWAITING_APPROVAL`; asserts **COMPLETED**, iterations < bound, `current_cycle >= 1`, `observation_signature` present in `result_summary`, and ≥ 1 scan produced.
2. **`test_phase3_out_of_scope_target_never_executes`** — scope-check 200 for in-scope, 4xx for `8.8.8.8`; direct scan-create for `8.8.8.8` rejected; control scan for `10.0.0.2` queues and is picked up by the worker (proves after-commit dispatch). Outcome `failed` for the real host is accepted (host unreachable) — it still proves execution, not loss.

## 16. Live API Smoke

`test_m74_api_smoke.py` **6/6** against `http://localhost:9002` — auth, projects, targets, scans, autonomous run lifecycle — all functional after Phase 3 changes.

## 17. Lint / Type Check

- **ruff/sort/black**: clean on all touched/new files. `deps.py` additionally had a pre-existing unused import (`asset_observation_repository.SqlAlchemyAssetObservationRepository`) removed; the `autonomous_*` imports re-sorted to correct isort position. Net diff is isort-only, zero behavior change.
- **mypy**: clean on all Phase-3 files (`autonomous_observation.py`, `autonomous_orchestrator.py`, `dispatch_after_commit.py`, `dispatcher.py`, `session.py`, `deps.py`). Remaining **27** errors are the documented pre-existing baseline in 7 untouched files (`workflow_templates.py`, `metrics.py`, `report_service.py`, `plugins/registry.py`, `workflow_suggestion_service.py`, `execution/engine.py`, `db/models/graph.py`).

## 18. Full-Suite Gate

`790 passed, 12 skipped in ~2:09` — matches/delivers against the Phase-2 baseline (755/12/1-env-dependent + this phase's work). The 6 Phase-2 env-dependent smoke failures now pass because the stack is up.

## 19. Loop-Termination Validation (explicit statement)

- Deterministic bounded loop proven in unit tests (scripted 3-cycle) and live (project-drained `/cycle` loop terminates `COMPLETED` within bound; never observed hanging).
- **New-information halt**: duplicate `observation_signature` ends the cycle.
- **Budget halt**: `max_actions` cap ends the cycle.
- **Human halt**: `AWAITING_APPROVAL` parks the loop until `/approve`/`/cancel`; `BLOCKED` items are skipped, never executed.
- If a planner ever returns zero proposals the run completes at PLANNING (no OBSERVING) — surfaced as a controlled, terminal, bounded outcome, never an infinite loop.

## 20. Commands Run (validation session)

```bash
make up   # implied: docker compose -f infra/docker-compose.yml up -d --build (all services healthy)
cd backend
pytest tests/unit/test_m74_phase3_feedback.py tests/unit/test_m74_orchestrator.py tests/unit/test_dispatch_after_commit.py -q   # 54 passed
pytest -q                                                                                                   # 790 passed / 12 skipped
pytest tests/integration/test_m74_api_smoke.py -q                                                           # 6 passed (live)
pytest tests/integration/test_m74_phase3_live.py -q                                                         # 2 passed (live)
ruff check <all touched files>                                                                               # clean
mypy app/application/autonomous_observation.py ... deps.py                                                   # clean on phase files
docker compose -f infra/docker-compose.yml logs worker --since 30m | grep scan_execution_missing_scan       # 0 hits
```

## 21. Expected Outputs Observed

- Unit trio: `54 passed`; full suite: `790 passed, 12 skipped`.
- Live: loop run reaches `COMPLETED`, `observation_signature` present, ≥ 1 real scan row; out-of-scope target rejected with 4xx on both scope-check and scan-create; in-scope scan transitions queued→(worker)→terminal; worker logs clean of `scan_execution_missing_scan`.

## 22. Performance Characteristics

- Loop steps are single-transaction state flips; observation fingerprints O(total bytes of tool-result payloads) with SHA-256, read-only, no stored fingerprints.
- Dispatch buffer is bounded per request (dedup set); drain is a single `send_task` batch after commit.

## 23. Security Checklist (11 items)

1. No secrets/credentials introduced or committed (secret-pattern scan of dispatch/session diffs: clean).
2. Plugin allow-list unchanged — nmap flag allow-list intact; `-oN/-oX/-oG/-oA` and `--script` remain forbidden.
3. No plugin writes files via the orchestrated path; plugin set untouched (no new plugins shipped).
4. Subprocess invocation remains list-args only (nmap/ping unchanged).
5. Scope Guard still re-validates at execution time; live-verified.
6. Feedback content is data-inert (never re-injected ungoverned into action payloads).
7. BLOCKED actions can never execute or be fuzzy-attached.
8. Soft cancellation semantics preserved (status flip).
9. Executor isolation intact (M7.1 unchanged; worker only dispatches over HTTP).
10. Clean Architecture preserved: `autonomous_observation.py` / `autonomous_orchestrator.py` import only domain + application (no Celery/SQLAlchemy/FastAPI).
11. Endpoints, RBAC, and RFC 7807 error mapping unchanged; no new attack surface.

## 24. Known Limitations / Risks

- The deterministic synthesizer can complete at PLANNING (zero proposals) — a bounded, reported outcome; a richer planner (M8) unlocks more loop depth.
- `10.0.0.x` hosts are typically unreachable from the host network, so live scans realistically end `failed` (execution proven, result empty). Deterministic actives target: friendly hosts on the compose network.
- Live negative test asserts "failed or completed" only — asserting specific ping results requires a controlled reachable host in scope.
- The 27 pre-existing mypy errors and Postgres-on-pytest skips remain unpursued (baseline, out of scope).

## 25. Baselines Preserved

- Phase 2: 755 pass / 12 skip / 1 env-dependent fail → now 790 pass / 12 skip / 0 fail (delivery adds all new tests + smoke converges with stack up).
- mypy baseline 27 (unchanged); ruff pre-existing deps.py noise reduced (unused import removed, isort clean).
- Milestone 2/3/4/5/6 behavior untouched; diff shows only Phase-2-autonomous + Phase-3 + §18 paths.

## 26. Artifacts & Evidence

- Working tree: 5 modified + 7 new files (see §2). No commits, no tags, no pushes — per STOP rule.
- Live evidence: full-suite output, `-rs` skip reasons (all Postgres-on-pytest), 8 live integration passes, worker log clean (0 `scan_execution_missing_scan`), ruff/mypy clean on touched files.

## 27. Risks & Mitigations

- **Worker missing committed rows** → eliminated by §18 after-commit drain; verified by live log grep + unit tests.
- **Unbounded loop** → three independent halts (novelty signature, `max_actions`, terminal-state break) + test-time bound of 24 cycles.
- **Category misuse** → frozen policy in orchestrator with playback allow-list (`ping`, `low`); unit + live coverage of approval parking vs auto-exec.
- **DB drift** → zero schema change; `result_summary` JSON column reused.

## 28. Files Touched (with reason)

| File | Reason |
|---|---|
| `app/application/autonomous_observation.py` | NEW — ObservationIngestService (signature, has_new, counts, finding linkage, `_first_target_value` fix) |
| `app/application/autonomous_orchestrator.py` | re-plan/cycle driver; category screen; `Mapping[str, object]`+`dict[str, int]` typing |
| `app/infrastructure/celery_app/dispatch_after_commit.py` | NEW — §18 buffer (`bind_sender`/`queue_dispatch`/`drain_pending_dispatches`) |
| `app/infrastructure/celery_app/dispatcher.py` | add `AfterCommitScanTaskDispatcher` |
| `app/infrastructure/db/session.py` | drain pending dispatches only after commit |
| `app/api/v1/deps.py` | observation wiring providers; isort; removed pre-existing unused import |
| `app/api/v1/routers/autonomous.py` | ingest on cycle/approve paths |
| `tests/fakes.py` | +FakeToolResultRepository, +FakePlannedActionRepository |
| `tests/unit/test_m74_phase3_feedback.py` | NEW — 20 observation tests |
| `tests/unit/test_m74_orchestrator.py` | re-plan pipeline tests |
| `tests/unit/test_dispatch_after_commit.py` | NEW — §18 tests |
| `tests/integration/test_m74_phase3_live.py` | NEW — live bounded loop + scope guard + dispatch smoke |

## 29. Conclusion

Phase 3 is **complete and verified**: the autonomous loop observes real finished work, computes a deterministic novelty signature, halts on duplicate findings, honours human approval parking, auto-executes only low-risk `ping` actions within scope, and terminates within a bounded number of cycles — with the §18 dispatch-after-commit race fixed and proven absent in live worker logs. All 790 tests pass; ruff/mypy are clean on touched files; the live stack validates end to end. **No commit/push/tag has been made.**