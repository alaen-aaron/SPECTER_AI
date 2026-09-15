# SPECTER_AI — M7.5 Phase 3 Verification Package

## 1. Overview

**Phase:** M7.5 Phase 3 — Scheduled Autonomous Campaigns
**Date:** 2026-09-15
**Status:** Complete and verified. No commit/push/tag has been made.

### Scope

Adds the third trigger kind to the M7.5 scheduler: a `CAMPAIGN` schedule creates an **AutonomousRun** (M7.4) in the same durable fire-lock transaction the Phase-1 scheduler already uses for workflow schedules. A client can now schedule a bounded autonomous campaign (`objective`, `max_actions`, `max_runtime_seconds`) instead of only a workflow execution.

This phase delivers: `ScheduleKind.CAMPAIGN` + `CampaignScheduleConfig` on the schedule domain entity; `kind`/`campaign_config` columns with migration `d1e2f3a4b5c6`; schedule-creation validation mirroring `CreateAutonomousRunRequest` bounds; a new `CampaignSchedulerService.fire()` bridging the claimed schedule to `AutonomousService.create()` with full audit coverage (`scheduler.campaign_created`, `_skipped_active_run`, `_rejected`, `_fire_failed`); the Celery beat dispatch (`campaign_advance_task` calling the existing M7.4 orchestrator); campaign payload validation in the API; and resource-first authorization for schedule-scoped reads (fixing a cross-project `?project_id=` read hole inherited from Phase 2's schedule GET).

**Explicitly out of scope (deferred, not started):** webhooks, notifications, a durable outbox, email/Slack/WebSocket delivery, CI/CD, new plugins, new AI/planner behavior, execution-improvement work, automation metrics/governor, frontend work, workflow autonomy, arbitrary event triggers.

---

## 2. Delivery guarantees (why no new lock/queue is needed)

Exactly-once delivery of a **committed** campaign occurrence falls out of three already-proven guarantees; nothing new duplicates them:

| # | Guarantee | Mechanism | Proven by |
|---|-----------|-----------|-----------|
| 1 | One beat owns each occurrence | `claim_due` + `FOR UPDATE SKIP LOCKED` (Phase 1) | `test_m75_phase1_firelock_integration.py` + `test_concurrent_beat_claim_one_winner` |
| 2 | One active run per project, enforced by the DATABASE | partial unique index `uq_autonomous_runs_active_project` (M7.4) → `AutonomousRunActiveExistsError` | `test_active_run_backstop_skips_duplicate_fire` |
| 3 | A committed campaign is never re-fired | `mark_run` consumes/advances the occurrence in the SAME commit as run creation | `test_campaign_fire_commits_run_and_consumes_occurrence` |

A claim that dies with a rollback re-fires (at-least-once); a re-delivered fire that meets an already-running campaign becomes an audited SKIP, never a duplicate.

---

## 3. Architecture: campaign fire loop

```
Celery beat tick (_tick_schedules)
  └─ for each schedule kind:
       WORKFLOW ─► workflow dispatch (Phase 1)
       CAMPAIGN ─► _fire_campaign_schedule(schedule)          [tasks.py]
                    └─ CampaignSchedulerService.fire()          [application/campaign_scheduler_service.py]
                         ├─ ScopeGuard preflight: project exists + ACTIVE + active auth record
                         │    (targets=[] — target selection is the planner's job; the
                         │     existing M7.4 execution-time scope checks re-validate targets)
                         ├─ AutonomousService.create(...)       [M7.4 black box]
                         │    └─ raises AutonomousRunActiveExistsError → SKIP path
                         ├─ AuditLogEntry scheduler.campaign_created
                         └─ ScheduleService.mark_run(id)        [same transaction]
               └─ campaign_advance_task.apply_async(args=[run_id], task_id=str(run_id))
                    └─ orchestrator.cycle(run_id)               [existing M7.4 machinery]
```

`claim → preflight → create run → audit → mark_run` all run inside the beat's single DB transaction: a committed fire **is** a committed campaign. The M7.4 subsystem (state machine, planner, `ActionProposalValidator`, `FailureRecoveryService`, retry taxonomy, cancellation, advisory locking, action idempotency, observation/replanning) is untouched — it is driven exactly as the interactive entry point drives it.

---

## 4. Skip / reject semantics (the decision table)

| Condition | Outcome | Occurrence | Audit event |
|-----------|---------|------------|-------------|
| Happy path | **FIRED** — run created, dispatch enqueued | consumed (`mark_run`) | `scheduler.campaign_created` |
| `AutonomousRunActiveExistsError` (active run for project) | **SKIPPED_ACTIVE_RUN** | consumed | `scheduler.campaign_skipped_active_run` |
| `ProjectNotFoundError` / `ProjectNotActiveError` / `NoActiveAuthorizationError` | **REJECTED** | consumed | `scheduler.campaign_rejected` |
| Unexpected exception | propagates → caller rolls back | **NOT** consumed; schedule stays due (at-least-once re-fire) | — |
| Non-campaign schedule passed to `fire()` | `ValueError` (programming error) | n/a | n/a |

Business rejections **consume** the occurrence so a repeating schedule stays on track for its next slot and a ONCE schedule stops cleanly instead of wedging the beat loop every 30s. Unexpected exceptions roll back — a memory hiccup or DB outage must not silently eat a scheduled campaign.

---

## 5. Schema change & migration

Migration `d1e2f3a4b5c6` (`c0d1e2f3a4b5 → d1e2f3a4b5c6`, additive):

| Column | Type | Null | Default |
|--------|------|------|---------|
| `schedules.kind` | `String(20)` | NOT NULL | `'workflow'` (existing rows keep Phase-1 behavior) |
| `schedules.campaign_config` | `JSONB` | nullable | — |
| `schedules.workflow_id` | existing `UUID` | NOW nullable (campaign rows leave it NULL) | — |

Verified live against the running Postgres (`alembic_version = d1e2f3a4b5c6`; both columns present; existing rows retained their workflow semantics).

---

## 6. RBAC & the cross-project read fix

- **Create/modify** (`POST /projects/{project_id}/schedules`) uses `require_scan_launch_permission()` — the exact scan-launch gate: project members in a testing role (Owner/Admin/Lead Tester/Tester) **plus** org Owner/Admin (org-level oversight). Same rule as launching a scan, since a campaign executes scans.
- **Resource-first GET** (`GET /schedules/{schedule_id}`) uses the new `require_project_role_for_schedule()`: the schedule's owning project is loaded server-side from the path `schedule_id` and is authoritative — the `?project_id=` query param is **ignored**. This closes a Phase-2-inherited hole where the schedule GET authorized against the caller-supplied query param, so a member of Project A could read Project B's schedule by passing A's id (or their own id) in the query string.
- **List** (`GET /projects/{project_id}/schedules`) stays path-project-scoped.
- **No org-admin bypass on read**: `require_project_role_for_schedule` checks project membership only — the same principle Phase 2 established for runs.

---

## 7. Files changed

| File | Change |
|------|--------|
| `backend/app/domain/entities.py` | `CampaignScheduleConfig` (frozen value object, `to_dict`/`from_dict`); `Schedule.kind`, `Schedule.campaign_config`, `Schedule.workflow_id` nullable |
| `backend/app/domain/value_objects.py` | `ScheduleKind` enum (`workflow`/`campaign`) |
| `backend/app/application/schedule_service.py` | `create()` campaign validation (workflow_id must be NULL; payload required; bounds 1–50 actions / 60–7200s; `created_by` required as run initiator); existing `mark_run` reused unchanged for consumption |
| `backend/app/application/campaign_scheduler_service.py` | **NEW.** `CampaignSchedulerService.fire()` + `CampaignFireOutcome`/`CampaignFireResult` |
| `backend/app/infrastructure/celery_app/tasks.py` | `_tick_schedules` campaign branch, `_fire_campaign_schedule`, `campaign_advance_task`/`_campaign_advance` wiring into the existing orchestrator |
| `backend/app/infrastructure/db/models/workflow.py` | `ScheduleModel` + `kind`, `campaign_config` |
| `backend/app/infrastructure/db/repositories/workflow_repository.py` | Schedule CRUD maps `kind` + `campaign_config` JSONB round-trip |
| `backend/alembic/versions/d1e2f3a4b5c6_*.py` | **NEW migration** (additive) |
| `backend/app/api/v1/schemas/workflows.py` | `CreateScheduleRequest.kind`/`campaign`; `ScheduleResponse` with `_flatten_campaign_config` validator (slotted-dataclass-safe); `ScheduleListResponse.items: list[ScheduleResponse]` |
| `backend/app/api/v1/routers/schedules.py` | Campaign mapping on create; resource-first GET via `require_project_role_for_schedule()`; list returns campaign configs |
| `backend/app/api/v1/deps.py` | `get_schedule_service`, `require_schedule_permission`, `require_project_role_for_schedule` |

### Tests (new)

| File | Count | Layer |
|------|-------|-------|
| `backend/tests/unit/test_m75_phase3_campaign_schedule_service.py` | 11 | ScheduleService campaign validation |
| `backend/tests/unit/test_m75_phase3_campaign_fire.py` | 14 | Fire/skip/reject decision table |
| `backend/tests/api/test_m75_phase3_campaign_schedule_api.py` | 16 | API RBAC + validation + IDOR |
| `backend/tests/integration/test_m75_phase3_campaign_scheduler_integration.py` | 6 | Real-Postgres concurrency/backstop/consumption |

---

## 8. Test results — full regression

```
938 passed, 2 warnings in 125.72s (0:02:05)
```

Baseline (Phase 2): 891 passed / 0 failed / 2 warnings. Delta: **+47 tests** (11 + 14 + 16 + 6), zero regressions. The 2 warnings are the pre-existing asyncpg coroutine-leak ResourceWarnings, unchanged from baseline.

One environment note: the 11 live-stack tests fail with `Connection refused` if `docker compose up` isn't running (they hit `http://localhost:9002`); with the full stack up they pass (verified). This is environmental, not code — the files are untouched by Phase 3.

---

## 9. Unit tests — schedule service validation (11)

`test_m75_phase3_campaign_schedule_service.py` — `ScheduleService.create` for campaign kind: campaign accept (workflow_id NULL, kind=campaign, config set, `next_run_at` scheduled, ONCE fired via first beat poll); workflow + campaign conflict rejected; missing payload rejected; missing `created_by` rejected; budget bounds rejected (`max_actions` 0, 51 and `max_runtime_seconds` 59, 7201) and boundary values accepted (1, 50, 60, 7200); workflow schedule unchanged (`next_run_at`, workflow_id set, kind=workflow); a workflow_id that points at a removed workflow still validated at create.

---

## 10. Unit tests — campaign fire decision table (14)

`test_m75_phase3_campaign_fire.py` — `CampaignSchedulerService.fire()` with `_FakeScheduleService`, `_FakeScopeGuard`, `_FakeAutonomousService`, `_FixedClock`: happy path creates run + audits `scheduler.campaign_created` + marks run; SKIP on `AutonomousRunActiveExistsError` (audited, occurrence consumed, no dispatch); REJECT on `ProjectNotFoundError`, `ProjectNotActiveError`, `NoActiveAuthorizationError` (all three audited `scheduler.campaign_rejected` + consumed); non-campaign input raises `ValueError`; audit entries carry `target_type="schedule"`, `target_id`, `after_state` with project/run/objective/budget snapshot; the reject path never touches the autonomous service.

---

## 11. API tests — RBAC + validation + IDOR (16)

`test_m75_phase3_campaign_schedule_api.py` — real ASGI app, fakes injected via `dependency_overrides`:

| Group | Cover |
|-------|-------|
| Create RBAC | owner 201; other-org owner 403 `insufficient-permission`; org admin (no membership) **201** (scan-launch gate grants org oversight — deliberate); read-only member 403 `insufficient-permission` |
| Create validation | `workflow_id`+kind conflict → `invalid-schedule-config`; missing payload → same; out-of-bounds `max_actions` → 422; bare workflow create defaults kind=workflow |
| IDOR (resource-first GET) | owner GET w/o param 200; **bob + `?project_id=<alice_proj>` → 403** (credential sneak); other-org owner 403; org admin w/o membership 403 (read has NO org bypass); read-only member 200; positive control (bob's own project schedule 200); nonexistent → 404 `schedule-not-found` |
| List | campaign `campaign_config` round-trips through `ScheduleResponse` |

The suite also caught and pinned a real schema bug — see §13.

---

## 12. Integration tests — real Postgres (6)

`test_m75_phase3_campaign_scheduler_integration.py` (skipped when `DATABASE_URL` unreachable; run with `$env:DATABASE_URL = "postgresql+asyncpg://specter:specter@localhost:5432/specter"`):

| Test | Proves |
|------|--------|
| `test_campaign_fire_commits_run_and_consumes_occurrence` | claim → fire → run row exists → `mark_run` consumed; next claim finds nothing |
| `test_concurrent_beat_claim_one_winner` | two sessions, `FOR UPDATE SKIP LOCKED`: B sees row locked → empty claim; exactly one active run created |
| `test_active_run_backstop_skips_duplicate_fire` | pre-seeded active run + second claim → `SKIPPED_ACTIVE_RUN`; DB unique index kept exactly one active run |
| `test_rejected_project_consumes_occurrence` | DRAFT project (no active auth) → REJECTED, consumed, zero runs created |
| `test_expired_campaign_schedule_invisible` | expired campaign schedule never claimed |
| `test_rollback_leaves_schedule_due` | abort before commit → schedule still due next tick |

---

## 13. Bug found & fixed during verification

`ScheduleResponse._flatten_campaign_config` (Pydantic `model_validator(mode="before")`, `backend/app/api/v1/schemas/workflows.py`) failed for **slotted dataclasses**: `Schedule` uses `@dataclass(slots=True)`, which has no `__dict__`, so `snapshot = data.__dict__.copy() if hasattr(...) else {}` produced `{}` plus `campaign_config` — every other field was dropped and validation failed with 13 missing fields. Fixed to detect dataclass instances via `__dataclass_fields__` and build the snapshot from field names (`getattr`), keeping the `__dict__` fallback for plain objects. Covered by the full API suite (16 tests). Resolved to mypy-clean without importing `dataclasses` (avoids the `DataclassInstance` narrowing pitfall).

---

## 14. Static-analysis gates

```
ruff check .   baseline (Phase 2, stashed):  160 errors
               current (Phase 3):            162 errors   → +2
```

The +2 are exactly the two new `(str, Enum)` enums (`ScheduleKind`, `CampaignFireOutcome`) matching the repo-wide convention (29–30 pre-existing UP042 instances were already tolerated; the phase deliberately did **not** refactor a global style rule). The 3 pre-existing I001 (router.py, models/__init__.py, asset_repository.py) and 1 UP047 (plugins/base.py) are untouched.

```
mypy app    baseline: 38 errors / 11 files
            current:  38 errors / 11 files      → exact parity
```

All touched/app source files are mypy-clean (verified individually; the 38 are pre-existing debt in untouched files such as `engine.py`, `plugins/registry.py`, `graph.py`). Ruff and black pass on every touched file and every new test file.

---

## 15. Live validation results (stack: `infra/docker-compose.yml`, API `:9002`)

Migration + schema (live Postgres, `d1e2f3a4b5c6`):

| Check | Result |
|-------|--------|
| `alembic_version` | `d1e2f3a4b5c6` |
| `schedules` columns | `kind` + `campaign_config` present |

API end-to-end (owner `e2e.alice@example.com`):

| Check | Result |
|-------|--------|
| Create campaign schedule (kind=campaign, objective/budget) | 201; response echoes kind + `campaign_config` |
| List schedules | campaign schedule present with campaign_config round-tripped |
| Resource-first GET by id (no query param) | 200 |
| Payload with `campaign: null` | 400/422 `invalid-schedule-config` |
| Out-of-bounds `max_actions: 999` | 422 |

**Beat/task live fire** — a `once` campaign schedule was picked up by the running beat, `campaign_advance_task` ran, and autonomous runs were created with the campaign objective and reached `completed` through the real M7.4 orchestrator on the worker. This is the full canonical path: API create → beat claim → fire → run + audit + consume → dispatch → orchestrator cycle → terminal state.

---

## 16. Stop-condition compliance (as-defined before the phase)

| Stop condition | State |
|----------------|-------|
| schedule + run creation transactionally safe | Claim → fire → run → audit → mark_run commits atomically (integration verified) |
| duplicate campaigns impossible | `FOR UPDATE SKIP LOCKED` + partial unique index + `mark_run` same-commit (all three verified) |
| active-run invariant intact | `uq_autonomous_runs_active_project` is the sole authority; app check is advisory (skips on `AutonomousRunActiveExistsError`) |
| target authz never bypassed | Only a **project-level** preflight (exists/ACTIVE/auth record) at fire; real targets still go through the existing M7.4 execution-time Scope Guard |
| scope guard semantics unchanged | `ScopeGuardService.validate_targets` untouched; called as-is |
| M7.4 semantics untouched | no edits to state machine, planner, validator, recovery, retry taxonomy, cancellation, advisory locking, idempotency, observation/replanning |
| approval policy unchanged | campaign runs enter `created` and ride the existing approval gate exactly like interactive runs |
| destructive migration | None — `d1e2f3a4b5c6` is additive with a non-breaking default |
| only one execution path | Campaigns reuse the single existing orchestrator path (`orchestrator.cycle`) — no second execution pipeline |

---

## 17. Architecture / dependency rule compliance

| Layer | Deliverables | Clean Architecture |
|-------|--------------|--------------------|
| `domain/` | `ScheduleKind`, `CampaignScheduleConfig`, `Schedule` | zero framework imports |
| `application/` | `ScheduleService.create` validation, `CampaignSchedulerService` | no Celery/SQLAlchemy/FastAPI; imports domain interfaces only |
| `infrastructure/` | model + migration + repo mapping + Celery tasks | implements domain interfaces |
| `api/` | schemas, router mapping, deps | no business logic |

`domain/` imports nothing from infrastructure/api/application; `CampaignSchedulerService` depends on domain interfaces (`ScheduleRepository`, `AuditLogRepository`) and existing use-case services — it never touches Celery, SQLAlchemy, or plugin classes. The Celery task constructs it with repository implementations.

---

## 18. Decisions

| Decision | Rationale |
|----------|-----------|
| Reuse the Phase 1 fire-lock + `mark_run` | Exactly-once/at-least-once of *committed* flames out of three proven mechanisms — no new queue/outbox to debug |
| Business rejections consume the occurrence | A repeating campaign must not spin-loop every beat; a rejected ONCE should stop cleanly — and the rejection is auditable (`scheduler.campaign_rejected`) |
| Unexpected exceptions roll back | Only this preserves at-least-once: a transient failure must re-fire, not silently vanish |
| Project-level preflight only at fire time | The planner selects real targets inside M7.4, which re-validates each against Scope Guard at execution — duplicating target checks at fire would not add safety and could only reject early |
| `(str, Enum)` for the two new enums | Matches the 30-odd existing enums repo-wide; deliberately not a refactor in this phase |
| Org admins may create campaign schedules (no membership) | Same rule as launching a scan (the gate this route reuses); CREATE is a trigger, not a control-plane op — reads remain strict-membership |
| `ScheduleResponse` validated via `__dataclass_fields__` | Regression-fix for the slotted-dataclass snapshot loss; avoids `dataclasses.asdict` narrowing drift in mypy |

---

## 19. Non-goals explicitly deferred (unchanged from phase start)

- Webhooks / notifications / durable outbox / email / Slack / WebSocket
- CI/CD, new plugins, new AI or planner strategy, new executor behavior, correlation
- Automation metrics/governor; frontend work; workflow autonomy; arbitrary event triggers
- Repo-wide UP042 refactor; remaining mypy debt (held at baseline)

---

## 20. Manual smoke checklist

```bash
# DB-enabled integration tests
$env:DATABASE_URL = "postgresql+asyncpg://specter:specter@localhost:5432/specter"
python -m pytest tests/integration/test_m75_phase3_campaign_scheduler_integration.py -q

# Live API (stack must be up)
TOKEN=$(curl -s http://localhost:9002/api/v1/auth/login -H "Content-Type: application/json" \
  -d '{"email":"e2e.alice@example.com","password":"Owner-pass-2026!"}' \
  | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

# Campaign schedule
curl -s -X POST http://localhost:9002/api/v1/projects/{PID}/schedules \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"frequency":"once","kind":"campaign","campaign":{"objective":"enumerate externally reachable services","max_actions":5,"max_runtime_seconds":600}}'
# → 201, kind=campaign; beat then creates a run and the orchestrator completes it

# Resource-first read (id only; ?project_id= is ignored)
curl -s http://localhost:9002/api/v1/schedules/{SCHED_ID} -H "Authorization: Bearer $TOKEN"
```

---

## 21. Rollback

- Revert the migration: `alembic upgrade c0d1e2f3a4b5` (only affects schedules; data-safe; `kind`/`campaign_config` drops). No destructive table work.
- `git checkout` of the source files in §7 + delete the four new test files + `campaign_scheduler_service.py`.
- No new runtime dependencies; no queue/service added.

---

## 22. Environment notes

- Python 3.11.9 worker (repo targets 3.12; black emits the known py311-parse warning but formats correctly).
- Live stack: `docker compose -f infra/docker-compose.yml` (api/worker/beat/postgres/redis/minio/executor/frontend). API `localhost:9002`; DB via `$env:DATABASE_URL`.
- The `-m "requires_postgres"` marker is implemented via `pytest.mark.skipif` — run `tests/integration/` with `DATABASE_URL` set instead.
- Baseline measurements taken by `git stash -u`, running `ruff check .` + `mypy app`, then `git stash pop`.

---

## 23. Summary

M7.5 Phase 3 extends the scheduler with a second trigger kind — a `CAMPAIGN` schedule that hands a bound, audited autonomous campaign to the existing M7.4 machinery inside the Phase-1 durable fire-lock. Exactly-once delivery of committed occurrences reuses three proven mechanisms (SKIP-LOCKED claim, DB active-run unique index, same-transaction `mark_run`); business rejections consume with audit, unexpected failures re-fire. Verified by 47 new tests (11 unit + 14 unit + 16 API + 6 real-Postgres integration), a full regression of **938 passed / 0 failed** (baseline 891), ruff at baseline plus the two `(str, Enum)` enums matching repo convention, mypy at exact parity (38/11, 0 new), a live API smoke, and a live beat→run→orchestra completion on the real stack. **No commit/push/tag has been made.**