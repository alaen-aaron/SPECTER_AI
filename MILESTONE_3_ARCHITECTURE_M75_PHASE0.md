# SPECTER_AI — M7.5 Phase 0 Architecture & Scope Recon

**Production Orchestration Layer — Discovery Report**
**Status:** READ-ONLY RECON ONLY. No source code, schema, API, or tests were modified. Nothing committed.

Baseline: commit `b91bb77` (M7.4 Phase 4) — 837 tests passed / 0 failed / 0 skipped, live Postgres integration green.

---

## 1. Current Architecture Snapshot

Six-layer Clean Architecture (`backend/app/`): `api/` (routers+schemas) → `application/` (use-cases) → `domain/` (entities, repositories, **zero framework imports**) → `infrastructure/` (SQLAlchemy repos, Celery, execution) → `plugins/` + `execution/`. Transport: FastAPI; workers: Celery (worker + beat); DB: Postgres 16; queues/result: Redis; storage: MinIO; UI: React/Vite stub.

Runtime topology (`infra/docker-compose.yml`): `api`, `worker`, `beat`, `frontend`, `postgres`, `redis`, `minio`, `executor` (isolated ephemeral-container executor), plus `plugins-image`. Two periodic beat tasks `celery_app/app.py:35-44`:
- `specter.tick_schedules` every 30s → fires due `Schedule` entries.
- `specter.recover_autonomous_runs` every 60s → fail-closed stale-run recovery.

Autonomous engine lives entirely in orchestration-driven endpoints; every control action is human-invoked today.

## 2. M7.1–M7.4 Capability Boundary (protected contract)

| Milestone | Concept | Where | STABLE surface |
|---|---|---|---|
| M7.1 | Executor isolation, `ExecutorHttpRunner`, `AuthorizedTargetRunner` (ephemeral container plugin exec) | `execution/` | invoke session → isolated plugin container, list-args only |
| M7.2 | AI planning `PlannerService`, `ActionProposalValidator`, `ActionClassifier`, `execute_approved`→`ScanService.create` | `application/planner_service.py`, `action_classifier.py`, `action_validator.py` | approval gate, proposal validity, scope at proposal+execution |
| M7.3 | Correlation, canonical identity, graph projection | `correlation_service.py`, `graph_projector.py` | finding/asset/identity linkage |
| M7.4 P1 | `AutonomousRun` state machine (`value_objects.py:281-336`), budget (`max_actions`, `max_runtime_seconds`) | `autonomous_service.py` | `created→planning→awaiting_approval→executing→observing→completed/failed/cancelled` |
| M7.4 P2 | Classification + approval (`CATEGORY_0/1/2`, `ApprovalMode`) | `action_classifier.py`, `autonomous_orchestrator.py` | human gate on CAT_1; CAT_2 auto-exec only `ping`-low |
| M7.4 P3 | Observation, novelty signature, bounded re-plan termination | `autonomous_observation.py` | `observation_signature` halt; `max_actions` halt |
| M7.4 P4 | Failure taxonomy (`TRANSPORT/TOOL/DOMAIN`), retry≤1, reconcile, fail-closed `recover_stale`, soft cancel, one-active-per-project partial unique index (`uq_autonomous_runs_active_project`), per-run `pg_try_advisory_xact_lock` + `_in_flight`, `scan_attempt_ids` lineage, audit `ai.autonomous.*` | `autonomous_recovery.py`, `autonomous_run_repository.py:169-179`, `models/autonomous.py:60-67` | concurrency + idempotency + recovery guarantees |

Audit catalog: `scan.started/completed/failed`, `ai.planner.proposal`, `ai.action.execute_rejected/execute_started`, `ai.autonomous.concurrent_cycle_blocked/planner_error/observation_error/observation/blocked/duplicate/execute_failed/scope_rejected/executed/awaiting_human/stalled/recovered/execution_retry`, `auth.*`. Written best-effort (try/except swallow), append-only table `audit_logs`.

Every autonomous scan still transits `ScanService.create` (scope at create, `scan_service.py:89`) and `ExecutionEngine.run` (re-validation at `engine.py:114`). **This is the guarantee any M7.5 amplification must preserve.**

## 3. Existing Workflow Architecture

Two disconnected subsystems:

1. **M5 in-memory template engine** (`domain/workflow_engine.py`, `workflow_templates.py`, `conditional_engine.py`, `builtin_templates.py`) — no DB persistence, referenced only by its own tests and `GET /plugins/workflow-templates` (read-only). Not production-wired.
2. **Persisted workflow system (M6)** — `workflows`, `workflow_steps`, `workflow_executions` tables; `WorkflowService` → Celery `specter.execute_workflow` → `WorkflowExecutor`.

**Model:** `Workflow` (status `draft/active/archived`), `WorkflowStep` (`step_type` ∈ {`scan`, `correlate`} but only `scan` used; `plugin` = plugin-name string; `depends_on` JSONB adjacency, **no FK**; optional `condition`; `max_retries`), `WorkflowExecution` (`status` is **`ScanStatus`** — `queued/running/completed/failed/cancelled`, no approval/observing states). DAG validity via `validate_dag` Kahn's algorithm (application-only).

**Capabilities today:** single Celery task executes the whole DAG synchronously; layers of the DAG run **sequentially** (no true parallelism, `workflow_executor.py:175`, BFS with `for step in batch:`). Steps create a `Scan` row and call `PluginManager.run` **directly**. No human gate inside execution. Soft cancel = status flip + check between steps.

**Critical findings (Section 6 also documents):**
- `WorkflowExecutor` **bypasses** `ScanService.create`, Scope Guard, `PluginManager.validate`, and `ExecutionEngine` — `workflow_executor.py:220-250` calls `self._scans.create(step_scan)` then `self._plugin_manager.run(...)` with `target_ids=[]`.
- Workflow execution is triggered by **any project member** incl. `READ_ONLY`/`CLIENT_VIEWER` (`routers/workflows.py:238-244`, `require_project_role()`).
- No FK from workflow tables to `scans`/`targets`/`planned_actions`/`autonomous_runs`. `planned_actions` with `action_type="workflow"` are produced by `WorkflowSuggestionService` but **no consumer** ever converts them into a real workflow execution — a dead-end.
- No workflow ↔ autonomous-run linkage, no budgets, no per-step status records (single `step_results` JSONB).

## 4. Existing Scheduling Architecture

`Schedule` entity + `schedules` table (`workflow_id` FK, `project_id` FK, `frequency` ∈ {`once`,`hourly`,`daily`,`weekly`}, `cron_expression` string, `is_active` bool, `last_run_at`, `next_run_at`, `created_by`).

**Execution model:** beat fires `specter.tick_schedules` every 30s → `_tick_schedules()` (`tasks.py:260-312`) → `schedule_repo.list_due(now)` → for each due schedule: `workflow_service.execute(...)` then `schedule_service.mark_run(...)`, one commit.

**Findings:**
- Schedules can ONLY trigger **Workflows** — there is zero code path schedule→autonomous run (grep `schedule.*autonomous` = no matches) and zero schedule→bare scan.
- **No concurrency protection**: `list_due` is a plain `SELECT` (no `FOR UPDATE`/`SKIP LOCKED`, `workflow_repository.py:340-362`); `next_run_at` advances only in `mark_run` after dispatch → two beats (deploy race) can double-fire the same schedule.
- `cron_expression` is **advisory only** — the tick compares `next_run_at <= now`, and `_compute_next_run` uses timedelta math (`schedule_service.py:119-133`); the stored cron string is never parsed.
- No `expires_at`, no `max_runs`, no `run_count`; `ONCE` auto-deactivates; DAILY runs forever.
- No audit coverage on create/pause/resume/delete.
- `ScheduleStatus` enum (`value_objects.py:241-248`) is dead code; status is boolean `is_active`.
- Archiving a workflow does not cascade-pause its schedules → tick retries the archived workflow every 30s, swallow-error loop (`tasks.py:308-310`).
- No `updated_at` column; no update endpoint (delete+recreate only).

## 5. Existing Event/Trigger Architecture

**There is none as a subsystem.** Exhaustive grep evidence:
- webhook / notification / subscribe / publish / SSE / outbox / broker-as-events: **zero real hits**. `GraphProjector` "subscribes to domain events" only in its docstring — it is invoked imperatively by application services.
- The only trigger-like machinery: 2 Celery beat periodic tasks; an **in-memory** after-commit scan-dispatch buffer (`dispatch_after_commit.py` + `db/session.py:100`) — non-durable, scan-specific, request-scoped; and the append-only `audit_log` table.
- No durable outbox, no event emitter, no deliverer, no webhook/SSE/WebSocket channel.

SRS anticipates this: FR-6.3 "event-triggered execution (land Phase 3)", FR-12.2 "in-app, email, Slack/Teams webhook (Phase 3)", FR-4.2 scheduled re-recon for continuous monitoring. **None are implemented.**

## 6. Existing Reporting/Notification Architecture

- Reports: `reports` (draft/final) + `report_versions` (md file pointers). Generation is **manual only**: `POST /reports/{id}/versions`, `finalize`, `pdf`, `diff`, templates `pentest_report`/`vulnerability_assessment`/`recon_summary` (`report_templates.py:291-295`). `AIReporterService` exists but is not wired to any automation.
- **No path** auto-generates a report when an autonomous run completes.
- **No notification channel**: no in-app `notifications` table (SRS §16 lists one; not implemented), no email/SMTP, no Slack/Teams webhook. No push on run completion — operators must poll `GET /autonomous-runs/{run_id}`.

## 7. Integration Gap Analysis

| Integration | Exists today | Missing / Broken |
|---|---|---|
| AutonomousRun → Workflows | No linkage at all | No FK/column; no action type for workflows; planned_action("workflow") dead-ends |
| AutonomousRun → Schedules | No | Schedules only fire workflows; no campaign/objective object for scheduled runs |
| AutonomousRun → Events/Triggers | No | No emitter, outbox, or subscription; audit log is write-only |
| AutonomousRun → Notifications/Webhooks | No | No channels (SRS FR-12.2 unimplemented) |
| AutonomousRun → CI/CD | No | No API keys/sessions for machine access (SRS lists, unimplemented) |
| AutonomousRun → Reporting | No | Auto-report on completion missing (SRS FR-4.2/12.x) |
| Security/Control-plane | Partial | Workflow path bypasses Scope Guard; run-scoped autonomous routes bind RBAC to caller-supplied `project_id` query param → cross-project control; schedule tick has no dedupe lock; workflow execution permission too lax |

Milestone alignment: **the "orchestration layer" called for by M7.5 is the SRS's scheduler-event + notification delivery + continuous-monitoring surface, anchored on the M7.4 engine.**

## 8. Proposed M7.5 Architecture

**Principle: M7.5 adds TRIGGER and REACTION surfaces AROUND the M7.4 engine — it never alters the engine.**

```
 [Manual API (today)]          [Schedule (campaign)]          [Event reaction]
        |                             |                             |
        +-------->  RUN COMMAND (project_id, kind, objective,
                     budget, approval_policy, target scope)
                             |
                             v
              Orchestration gate (application layer):
              - project ACTIVE? automation enabled for project?
              - resolve run's OWN project (never caller-supplied)
              - Scope Guard re-check at creation AND at each cycle
                             |
                             v
               AutonomousService / AutonomousOrchestrator   <-- M7.4 UNCHANGED
               (one-active-run partial unique, advisory lock,
                _in_flight, reconcile/recover_stale, retry<=1)
                             |
                             v
             TRANSACTIONAL OUTBOX (new, durable)  <-- written in same tx
             as run lifecycle transitions (created/executed/
             completed/observation/failed/cancelled)
                             |
          +------------------+------------------+
          v                  v                  v
   Notification rows   Webhook delivery    Auto report generation
   (idempotent,        (project-scoped,    (AI/standard template,
    per-project)        signed/secret,       on COMPLETED only)
                        per-project)
```

Key reuse decisions (per instruction: reuse, don't invent):
- **AutonomousRun is the sole execution primitive for scheduled/automated work.** No new "workflow run" autonomy. If desired, a workflow is a *one-shot deterministic pipeline* that may optionally call `ScanService` gated the same way — but that is M7.5 Phase 1 safety hardening, not a new engine.
- **`schedules` table is extended (additive columns), not replaced**, to carry a run-campaign payload for `kind=autonomous`.
- **Audit log stays the provenance layer** (append-only, best-effort) and the outbox is the delivery layer.
- **Workflow subsystem is HARDENED to parity with the M7.4 guarantee** (scope + validation + ownership + locking + audit) so automation cannot be a bypass — or, if preferred, its execution is disabled pending rework. Recommend harden (it is a shipped capability).

## 9. Proposed Domain Flow

1. **Create automation**: `Schedule` (or campaign record) with `kind ∈ {workflow, autonomous}`; for autonomous: `objective`, `max_actions`, `max_runtime_seconds`, `approval_policy`, `target_scope` (project targets), `require_approval: bool`. Authorization: OWNER/ADMIN; project must be `ACTIVE`.
2. **Tick**: `FOR UPDATE SKIP LOCKED` claim of due schedule → create run via the SAME `AutonomousService.create` path (one-active-run guard applies) → commit → dispatch is a plain run row, not a Celery message (runs are driven by `/cycle` calls — for automated runs a beat `tick_autonomous` advances each run). If a run already exists (non-terminal), tick **skips** (no duplicate).
3. **Steer**: automated runs execute CATEGORY_2 auto-policy actions exactly like manual runs; if the policy yields CATEGORY_1 ("awaiting approval"), the run parks and emits a notification/webhook — **no implicit approval ever**.
4. **Complete**: terminal transition writes outbox rows → deliverers: notification row per project member; webhook POST (idempotent, retried, secret-signed); report draft generated only for `COMPLETED`.
5. **Govern**: project-level automation on/off, per-schedule pause/resume/delete with audit, listing of "who/what scheduled what target", run oversight from the same run API.

## 10. Authorization/Security Flow

- **Fix first (phase order guarantees security):**
  1. **Run-scoped route binding** — `require_org/project_role` must resolve the run's actual owning project server-side (pattern: `require_project_role_for_target`, `deps.py:756-779`), never the caller-supplied `?project_id=` query param (current binding `deps.py:743-753` on `autonomous.py:95,110,...`). **Today a member with OWNER/ADMIN in *any* project can control runs of other projects** by passing their own project_id.
  2. **Workflow safety parity** — `WorkflowExecutor` must go through `ScanService.create` (+ `ScopeGuardService.validate_targets` at create) OR substitute `ExecutionEngine.run` (which re-validates at `engine.py:114`); plus `PluginManager.validate` config checks; plus `require_scan_launch_permission()`-class gate on workflow execute; plus audit per step.
- **At fire time, never at creation time only:** schedules re-derive scope validity when the tick fires (auth can lapse between CREATE and RUN — same rule as M7.4 execution-time revalidation).
- No global auth middleware (per-route `get_current_user`) — orchestrations **must** authenticate as a service identity or reuse the schedule's `created_by` with validated membership.
- No rate limiting anywhere today — automated runs need budgets (already `max_actions`) and a project-level automation cap.

## 11. Concurrency & Idempotency Strategy

Preserve (M7.4, must not regress): one-active-run-per-project partial unique index; per-run advisory xact lock; `_in_flight` fast-fail; retry cap = `AUTONOMOUS_MAX_RETRIES_PER_ACTION=1`; action-execution idempotency; scan-append lineage.

Add for orchestration:
- **Fire claim:** `SELECT ... FOR UPDATE SKIP LOCKED` on the due `schedules` row (or per-schedule advisory xact lock) — double-beat fire impossible.
- **Run-started marker:** schedule keeps `last_run_at` set in the same transaction that creates the run row; tick skips if a non-terminal run already exists for the schedule/project (0/1 run invariant reuses the partial unique index).
- **Outbox idempotency:** `(event_type, aggregate_id, version)` unique; consumers deliver exactly-once via conditional insert / unique target keys (`notification(project_id,user_id,event_id)`); webhook delivery keyed by outbox event id stored in the delivery-log column.
- **No async reprocessing of already-settled state**: reconcile/recover remain the only settlement paths; an outbox consumer never mutates run state.

## 12. Failure & Recovery Interaction

- Automated runs are recovered by the **same** `specter.recover_autonomous_runs` (60s) beat — no new recovery code; a stalled scheduled run is picked up, fail-closed per M7.4.
- Outbox consumers are retriable and bounded (max N, then dead-letter column + `ai.automation.delivery_failed` audit) — they never resurrect a run.
- If webhook delivery is down: notifications remain in DB and webhook rows go to `pending` and are retried by the next beat window; **run lifecycle never blocks on a notification**.
- Report generation failure on COMPLETED: report stays DRAFT + outbox redelivery; run is already terminal (report is a reaction, not a dependent).

## 13. Observability Requirements

Current signals: `/api/v1/health` (db, redis, plugins, normalizers), JSON `/api/v1/metrics` (in-process collector), structlog JSON, `last_heartbeat_at` column, audit rows. **Missing** (proposal only):
- Autonomous + automation metrics: run duration per outcome, cycle latency, retry rate, outbox depth, delivery failure rate, scheduled-fire lag.
- Worker/beat healthchecks in docker-compose (none today).
- W3C-style correlation id propagated API→Celery→outbox lessons (currently no `request_id`/`trace_id`).
- An operator "automation surface" read endpoint (schedules+run lineage+delivery state) — no UI build.

## 14. API Surface Proposal (no implementation)

- `POST /projects/{project_id}/schedules` — extend request with `kind: workflow|autonomous`, `payload` (objective/budget/policy/target scope), `max_runs`, `require_approval`. Back-compatible (kind defaults `workflow`).
- `POST /projects/{project_id}/automation/enable|disable`, `GET .../automation` — project-level master switch with audit.
- `GET /schedules/{id}/runs` — lineage of runs fired by a schedule.
- `GET /projects/{project_id}/notifications`, `POST .../notifications/{id}/read` — in-app notification read surface.
- `POST /projects/{project_id}/webhooks` CRUD + `POST /webhooks/{id}/test`.
- `POST /reports/{id}/versions` gains optional `trigger: manual|automated` provenance (no new endpoint).
- Fix existing run-scoped autonomous routes to bind permission to the run's owning project (behavioral fix, not new surface).

## 15. Database Changes Proposal (no implementation)

| Change | Table | Reason |
|---|---|---|
| ADD `kind` (default `workflow`), `payload` JSONB, `max_runs`, `run_count`, `last_status`, `updated_at` | `schedules` | Let schedules fire autonomous campaigns without a new table |
| ADD partial index for automation guard | `schedules` | fire-claim + run-count bookkeeping |
| NEW `webhooks` (project FK, url, secret_hash, events[], enabled, last_delivery) | — | delivery channel per SRS FR-12.2 |
| NEW `outbox`/`pending_events` (event_type, aggregate refs, unique `(type, agg, version)`, status, attempts, dead_letter) | — | durable trigger carrier replacing in-memory buffer |
| NEW `notifications` (project/user FK, content refs, read_at) | — | SRS §16 / FR-12.2 |
| NEW revision columns | `reports` | report provenance (trigger type) — optional |
| No change | `autonomous_runs`, `autonomous_run_actions`, `planned_actions`, `scans`, `workflows` | engine tables untouched |

One additive migration, net-new tables only; **no alteration or data migration of existing M7.1–M7.4 tables.**

## 16. M7.5 Phase Breakdown (small, independently verifiable)

All phases: own unit/integration/live-validation gates, `conventional` commit per phase, default STOP-after-report per phase; each phase rollback = revert single commit (schema additive-only).

| # | Phase | Objective | Files likely affected | DB | API | Security | Tests | Live validation | Rollback | Completion criteria |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | **Schedule & workflow hardening (guarantee parity)** | Make today's automation channels safe BEFORE amplifying them | `application/workflow_executor.py`, `scan_service.py` (inject into executor), `application/schedule_service.py`, `celery_app/tasks.py:260-312` (`list_due` → `FOR UPDATE SKIP LOCKED`), `routers/workflows.py` (permission class), repos `workflow_repository.py` | none | none (behavioral) | closes Scope-Guard/eval bypass; workflow exec permission; double-fire | unit: executor scope+validation, tick-lock, perm matrix; integration: 2-tick no-double-fire, out-of-scope step rejected | live: schedule a workflow w/ out-of-scope target → blocked; double-beat race | revert 1 commit | WorkflowExecutor path proves identical scope/config guarantees as ScanService; no double-fire under 2 concurrent ticks |
| 2 | **Control-plane isolation fix** | Run-scoped autonomous routes enforce the run's OWN project | `api/v1/deps.py` (+`require_project_role_for_run`), `routers/autonomous.py` | none | behavioral fix | closes cross-project control | unit: perm resolver; integration: cross-project deny 403 | live: user in project A cannot cycle project B run | revert 1 commit | Cross-tenant control attempts → 403; existing smoke suite green |
| 3 | **Scheduled autonomous campaigns** | Schedules can fire `kind=autonomous` runs under M7.4 guards | `domain/entities.py` (Schedule ext), `models/workflow.py`, `schedule_service.py`, new `autonomous_campaign.py` app service, `celery_app/tasks.py` (tick branch + per-run cycle stepper), schemas, migration | ADD columns/index on `schedules` | extend create request (kind/payload/max_runs) | fire-time scope re-check; budget; one-active-run invariant | unit: campaign mapping, budget, policy park; integration: 0/1 run invariant, re-fire skip while active | live: schedule run on project, verifies scan lineage + completed + audit | revert 1 commit (additive) | Schedule fires exactly one run window, honors budget and approval policy; no duplicate active runs |
| 4 | **Reactive notifications + webhooks + auto-report** | Terminal run events delivered via durable outbox | new `outbox` model/repo/service + `webhooks` + `notifications`; `autonomous_orchestrator.py`/`autonomous_service.py` (emit, not handle); `report_service.py` (trigger param); deliverers in `celery_app/tasks.py`; migration, schemas, routers | NEW outbox/webhooks/notifications | notifications + webhooks CRUD + test | outbound signed delivery; no findings in logs; per-project secret | unit: outbox uniqueness/redelivery; deliverers idempotent; dead-letter; integration: event→notification+webhook+report exactly once | live: run completes → webhook captured once, notification row, report draft auto | revert 1 commit (new tables only) | Terminal run produces exactly-once notification+webhook(+report on COMPLETED); no run-state coupling |
| 5 | **Orchestration observability + governance gate** | Operator visibility + project automation switch | `core/metrics.py` (+automation metrics), `tasks.py` (instrument), docker-compose worker/beat healthchecks, `routers/autonomous.py` or new `automation.py` (enable/disable + schedule lineage), audit events `ai.automation.*` | none (or `schedules.enabled_reason`) | enable/disable + lineage endpoints | master switch stops new fires only (never mutates active runs) | unit: metric emission, switch behavior; integration: disabled project no-fire | live: toggle off → tick skips; metrics visible | revert 1 commit | Healthchecks green; automation metrics present; disable halts future fires, active runs undisturbed |

Dependencies: 1→(3 needs safe workflow base OR 3 can be autonomous-only); 2 before 3/4 (control plane must be isolated before automation adds volume); 5 last.

## 17. Explicit Out-of-Scope List

- CIO/CI-CD integration (no API keys/sessions infra; SRS lists but out for M7.5).
- Email/Slack/Teams native senders (outbound webhook only; FR-12.2 email deferred).
- WebSocket/SSE push + any frontend build (frontend is a stub; operator UI is its own milestone).
- New autonomous intelligence, planner changes, new plugins, prompt modifications.
- Changes to M7.4 engine internals (state machine, recovery, concurrency primitives, retry policy, observation signature).
- Event→autonomy chains WITHOUT explicit human policy opt-in (no autonomous-triggers-autonomous).
- Multi-org/global automation, outbox consumption by non-SPECTER services, rate-limiting middleware (flagged, not scheduled).
- Skipping/migrating workflow tables or merging the two workflow subsystems (hardening only).

## 18. Risks & Mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| Workflow automation amplifies an existing scope bypass | HIGH | Phase 1 parity hardening ships FIRST; nothing automated runs unhardened |
| Cross-project run control via `?project_id=` | HIGH | Phase 2 server-side project resolution before any automated volume |
| Double-fires/tick races | MED | SKIP LOCKED fire-claim + run-row invariant (partial unique index) |
| Recurring run after auth revoked | HIGH | Execute-time scope revalidation every cycle (already the rule) + fire-time project ACTIVE check |
| Webhook exfiltration/abuse | MED | Per-project signed delivery, secret-hash storage, no secrets in logs, project-scoped read/write |
| Outbox replay/duplicates | MED | Unique `(type, agg, version)` + target-key idempotency |
| Automation DoS | MED | `max_actions`+budget (existing) + project automation cap (Phase 5) |
| Deliverer crash feedback loops | LOW | bounded retry + dead-letter + `ai.automation.delivery_failed` audit |

## 19. Test Strategy

- **Per phase:** unit tests on the new service/factory/outbox logic; integration tests against real Postgres (same pattern as `test_m74_phase4_recovery_integration.py` — seed real FKs, two sessions for locks); live API smoke extended incrementally.
- **Cross-cutting:** regression gate stays `837 passed / 0 failed / 0 skipped` at every phase; new tests counted explicitly per phase (target +15–25 unit, +3–5 integration per phase).
- **Security:** negative tests per phase — out-of-scope scheduled run blocked, cross-project control denied (403), double-tick no double-fire, duplicate outbox no double-delivery.
- **No test deletions/weakness; existing M7.1–M7.4 tests remain untouched and green.**

## 20. Definition of Done (M7.5)

1. All 5 phases shipped with additive-only schema changes; M7.1–M7.4 tables/engine byte-identical in behavior (regression suite green at each phase).
2. Workflow + schedule paths prove identical safety guarantees to the ScanService path.
3. Run-scoped API permission resolves the run's owning project; cross-project control returns 403 (integration + live proven).
4. Schedules fire autonomous campaigns with 0/1-run invariant; budgets honored; approval policy parks (notification, never implicit approval).
5. Terminal runs produce exactly-once outbox reactions (notification + webhook + optional auto-report), decoupled from run state.
6. Orchestration metrics present; worker/beat healthchecks enabled; project automation switch functional.
7. Documentation updated per phase (verification packages, this architecture doc revised as-built).

## 21. Recommended Implementation Order

**Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5.** Rationale: (1) close the two HIGH severity bypasses (workflow scope parity + control-plane isolation) before introducing any automated volume; (3) then add the amplification (schedules→runs) on the now-safe base; (4) then reactions on the durable carrier; (5) then observe/govern. Phase 0 (this report) is the only recon-only phase; Phases 1–5 are each independently verifiable and individually stoppable.

## Answers to the Ten Architectural Questions

- **A. What should M7.5 accomplish?** The smallest production-grade orchestration layer: (1) harden the two existing automation channels (workflows, schedules) to the M7.4 safety bar, (2) isolate the autonomous control plane (run-project binding), (3) extend scheduling to fire autonomous campaigns under existing 0/1-run + budget + approval guarantees, (4) deliver terminal-run reactions via a durable outbox (in-app notifications, project webhooks, auto-report), (5) add orchestration observability + project-level governor.
- **B. Which integrations?** Schedules — YES (core). Workflows — YES, hardening only (no workflow autonomy). Events/Triggers — YES as an internal durable outbox (not a pub/sub system). Notifications/Webhooks — YES. CI/CD — NO (out of scope). Reporting — YES (auto-draft on COMPLETED).
- **C. Existing vs missing:** Workflows (exist, unsafe); Schedules (exist, →workflows only, unguarded); Events/triggers (absent); Notifications/webhooks (absent); CI/CD (absent); Reporting triggers (absent).
- **D. Services to reuse:** `AutonomousService`/`AutonomousOrchestrator`/recovery as-is; `ScanService`+`ScopeGuardService`+`ExecutionEngine` as the only scan-launch doors; `schedules`/beat tick; audit repository; `ReportService`+template registry; `MetricsCollector`/structlog.
- **E. New domain concepts needed:** an automation payload shape (schedule→run campaign), a durable outbox event, a project webhook, an in-app notification, and a project automation governance switch. None replace existing concepts.
- **F. Minimum safe production architecture:** trigger → project-resolved, scope-revalidated, budgeted run creation → M7.4 engine untouched → transactional outbox → idempotent deliverers; project master switch + audit everywhere.
- **G. Must remain unchanged:** entire M7.1 executor; M7.2 planner/validator/classifier/`execute_approved`; M7.3 correlation/identity; M7.4 state machine, recovery, retry, cancellation, concurrency primitives; Phase 1–3 features; plugin set; Scope Guard and authorization semantics; existing tables.
- **H. Major security risks of orchestration:** amplifying the workflow scope bypass; cross-project control; stale-authorization refiring; webhook exfiltration; automation DoS; replay/duplicate deliveries.
- **I. Guarantees orchestration must preserve:** one-active-run per project; advisory lock + in-flight; retry≤1; action idempotency; observation termination; scope revalidation at execution time; exactly-once outbox delivery via unique keys.
- **J. Out of scope:** CI/CD, email/Slack senders, WebSocket/SSE/UI, new intelligence/plugins, engine internals, event→autonomy without human policy opt-in, multi-org automation, workflow-table redesign.

**Test baseline:** `837 passed / 0 failed / 0 skipped` (M7.4 Phase 4, live stack). No code, schema, API, or test was modified during this recon.