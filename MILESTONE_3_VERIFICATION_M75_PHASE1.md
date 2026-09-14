# SPECTER_AI — M7.5 Phase 1 Verification Package

## 1. Overview

**Phase:** M7.5 Phase 1 — Workflow + Schedule Safety Hardening
**Date:** 2026-09-14
**Status:** Complete and verified. No commit/push/tag has been made.

### Scope

Workflow-triggered scans now ride the canonical M7.1 path (`ScopeGuardService` + `ScanService.create` + `ExecutionEngine.run`); scheduling has durable fire-lock (`FOR UPDATE SKIP LOCKED`), expiry enforcement, and real cron semantics; schedule + workflow mutation require a scan-capable role via RBAC; existing failures in the automated scheduler tick are handled safely (no wedge, no silent swallowing).

### Key guarantees delivered

| # | Guarantee | Proven by |
|---|-----------|-----------|
| 1 | Workflow executes through the canonical scan path | `test_workflow_executor.py` (6 unit tests) + `test_m75_phase1_live.py::test_live_workflow_execute_canonical_path` |
| 2 | Fail-closed targets: unregistered/unresolvable targets fail the step, never produce `target_ids=[]` | `test_workflow_executor.py::test_execute_failclosed_no_plugin_config_target` + `test_execute_failclosed_unregistered_target` |
| 3 | Revalidation at execution time: scope lapsing between dispatch and execution fails closed | `test_workflow_executor.py::test_execute_revalidates_scope_at_execution_time` |
| 4 | Retry exhaustion fails the step (max_retries respected) | `test_workflow_executor.py::test_execute_retries_and_exhausts` |
| 5 | `FOR UPDATE SKIP LOCKED` fire-lock: concurrent beats → exactly one winner; rollback leaves row due | `test_m75_phase1_firelock_integration.py` (4 Postgres integration tests) |
| 6 | Expired schedules invisible to claimer | `test_m75_phase1_firelock_integration.py::test_claim_excludes_expired_schedules` |
| 7 | Claim respects batch limit | `test_m75_phase1_firelock_integration.py::test_claim_limits_batch_size` |
| 8 | Past `expires_at` at create time deactivates immediately (auditable, never fires) | `test_schedule_service.py::test_create_with_expired_deadline_is_inactive` |
| 9 | Resume on already-expired schedule stays dead | `test_schedule_service.py::test_resume_expired_schedule_stays_dead` |
| 10 | `WorkflowNotExecutableError` / `WorkflowNotFoundError` disable the schedule + audit | `test_schedule_service.py` (claim_due rollback/safety tests in `test_tick`) |
| 11 | `require_workflow_execution_permission` resolves project server-side (no caller-supplied `?project_id=`) | Deps code review + `test_m75_phase1_live.py::test_live_rbac_workflow_execute_and_schedule` |
| 12 | `require_scan_launch_permission` (scan-capable roles) enforced on workflow execution + schedule create/pause/resume/delete | Same live test (403 for org member `member` role) |
| 13 | Real cron parser: Vixie 5-field, dow 0=7=Sunday, dom/dow OR rule, invalid/never-matching rejected | `test_cron.py` (10 unit tests) |

---

## 2. Files changed

### Domain layer (`app/domain/`)

| File | What changed |
|------|-------------|
| `cron.py` | **NEW.** Pure Vixie 5-field cron parser: `InvalidCronExpressionError`, `CronExpression` dataclass, `parse_cron_expression`, `next_run`. No third-party cron library. |
| `entities.py` | `Schedule.expires_at: datetime | None`, `Schedule.updated_at: datetime`, `Schedule.is_expired(at) -> bool` method (not property). |
| `repositories.py` | `ScheduleRepository.claim_due(now, limit) -> list[Schedule]` abstract method. |
| `exceptions.py` | `WorkflowStepTargetError` added. |

### Application layer (`app/application/`)

| File | What changed |
|------|-------------|
| `workflow_executor.py` | **Rewritten.** Constructor takes `(scan_service, execution_engine, target_repository, execution_repository, step_repository, audit_log_repository)`. `_run_step` calls `ScanService.create` with resolved `target_ids` + `NullScanTaskDispatcher`, then same-process `execution_engine.run(scan.id)`. Fail-closed `_resolve_targets` (plugin_config keys `target`/`hostname`/`url` must resolve to registered project `Target` rows or `WorkflowStepTargetError`). Removed unused `CorrelationService` import. |
| `schedule_service.py` | **Rewritten.** Real cron validation, defaults (`HOURLY: "0 * * * *"`, `DAILY: "0 0 * * *"`, `WEEKLY: "0 0 * * 0"`), `_enforce_expiry` on create/resume/mark_run, `InvalidScheduleConfigError` on bad/never-matching cron. |

### API layer (`app/api/v1/`)

| File | What changed |
|------|-------------|
| `deps.py` | `require_workflow_execution_permission()`, `require_workflow_execution_permission_for_execution()`, `require_schedule_permission()` — all using `_check_scan_launch_permission` with `_SCAN_CAPABLE_PROJECT_ROLES`. |
| `routers/workflows.py` | `execute_workflow` + `cancel_execution` gated by new deps. Imports `OrganizationMember`. |
| `routers/schedules.py` | `create_schedule` gated by `require_scan_launch_permission()` (path `project_id`). `pause/resume/delete` gated by `require_schedule_permission()`. |
| `schemas/workflows.py` | `CreateScheduleRequest.expires_at`, `ScheduleResponse.expires_at` + `updated_at`. |
| `error_handlers.py` | `WorkflowStepTargetError -> (422, "workflow-step-target-error")`. |

### Infrastructure layer (`app/infrastructure/`)

| File | What changed |
|------|-------------|
| `db/models/workflow.py` | `ScheduleModel.expires_at: Mapped[tzdatetime | None]`, `ScheduleModel.updated_at: Mapped[tzdatetime]`. |
| `db/repositories/workflow_repository.py` | `_schedule_to_entity` maps `expires_at`/`updated_at`. `claim_due` uses `SELECT ... FOR UPDATE SKIP LOCKED` with `expires_at` filter. |
| `celery_app/tasks.py` | `logger = structlog.get_logger(__name__)`. `_execute_workflow` rewired to canonical path (builds `ScanService(NullScanTaskDispatcher)` + `ExecutionEngine` + `WorkflowExecutor`). `_tick_schedules` rewritten: `claim_due(limit=50)`, audit events, disable on `WorkflowNotExecutableError`/`WorkflowNotFoundError`, rollback + best-effort `scheduler.schedule_fire_failed`. |

### Schema migration

| File | What changed |
|------|-------------|
| `alembic/versions/c0d1e2f3a4b5_m75_phase1_schedule_safety.py` | `down_revision = "b5c6d7e8f9a0"`. Adds `expires_at` (nullable), `updated_at` (with `server_default=now()`). |

### Tests

| File | What changed |
|------|-------------|
| `tests/fakes.py` | `FakeScheduleRepository.claim_due` (claimed-set + expiry filter). `FakeExecutionEngine` with `failures`/`op_failures` injection using `ScanFailureKind.TRANSPORT`. |
| `tests/unit/test_cron.py` | **NEW.** 10 tests: parse minute/hour/dom/dow/month/combined fields, invalid expressions, dow 0=7 Sunday, dom/dow OR rule, month rollover, far-horizon. |
| `tests/unit/test_workflow_executor.py` | **Rewritten.** `_Harness` with real `ScopeGuardService` + `ScanService` + `PluginManager(echo)`. 6 tests: canonical path, condition skip, retry + exhaust, fail-closed (no target, unregistered target), revalidation. |
| `tests/unit/test_schedule_service.py` | Extended: invalid cron rejected, explicit cron used, expiry create/pause/resume/mark_run/claim_due. |
| `tests/integration/test_m75_phase1_firelock_integration.py` | **NEW.** 4 tests against real Postgres: concurrent claim (exactly-one-winner via SKIP LOCKED), rollback leaves due, expiry excluded, batch limit. |
| `tests/integration/test_m75_phase1_live.py` | **NEW.** 3 live API tests against `localhost:9002`: workflow execute → canonical path → terminal + scan row, schedule CRUD + past-expires_at inactive, RBAC (403 for stranger + org member `member` role). |

---

## 3. Migration

Applied successfully against live Postgres:

```
INFO  [alembic.runtime.migration] Running upgrade b5c6d7e8f9a0 -> c0d1e2f3a4b5, M7.5 Phase 1: workflow & schedule safety hardening
```

**Columns added to `schedules`:**
- `expires_at TIMESTAMP WITH TIME ZONE NULL`
- `updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()`

Verified in live Postgres (`information_schema.columns`) after `alembic upgrade head`. No unrelated tables touched.

---

## 4. Test results

### Full regression (870 passed / 0 failed / 2 warnings)

```
870 passed, 2 warnings in 100.48s (0:01:40)
```

Baseline was 837 passed. Delta: +33 new tests (10 cron + 8 workflow executor rewritten + 6 schedule service extended + 4 fire-lock integration + 3 live API + 2 misc).

### Breakdown by test group

| Group | Count | Notes |
|-------|-------|-------|
| `tests/unit/test_cron.py` | 10 | Pure cron parser, no DB |
| `tests/unit/test_workflow_executor.py` | 8 | FakeScheduleRepository, real ScopeGuardService, real ExecutionEngine via NullScanTaskDispatcher |
| `tests/unit/test_schedule_service.py` | 20+ | Extended with expiry, cron validation, claim_due |
| `tests/integration/test_m75_phase1_firelock_integration.py` | 4 | Real Postgres `FOR UPDATE SKIP LOCKED` |
| `tests/integration/test_m75_phase1_live.py` | 3 | Live API against `localhost:9002` |
| Pre-existing suite | 837 | Zero regressions |

### Live API smoke

```
tests/integration/test_m75_phase1_live.py  3 passed in 7.99s
tests/integration/test_m74_api_smoke.py    5 passed (M7.4, no regression)
tests/integration/test_m74_phase3_live.py  2 passed (M7.4, no regression)
```

---

## 5. Gates

| Gate | Result |
|------|--------|
| **ruff** | All touched files pass. Pre-existing repo-wide debt (UP042, N801, etc.) untouched. |
| **black** | All touched files reformatted. |
| **mypy** | BASE 38 (11 files). No new errors introduced. Ran with `--python-executable "...\Python311\python.exe"`. |
| **pytest** | 870 passed / 0 failed. |

---

## 6. Architecture notes

### Canonical path for workflow execution

```
API POST /workflows/{id}/execute
  → WorkflowService.execute (creates WorkflowExecution QUEUED)
  → CeleryWorkflowTaskDispatcher.dispatch_workflow (enqueues `workflow_execute`)
  → worker: _execute_workflow
      → WorkflowExecutor._run_step (for each step)
          → ScanService.create (with NullScanTaskDispatcher — never broker-dispatched)
              → ScopeGuardService.validate (re-validates at execution time)
          → execution_engine.run(scan.id) (same-process)
              → PluginManager.execute (ping/nmap subprocess)
```

### Fire-lock (`claim_due`)

```sql
SELECT ... FROM schedules
WHERE is_active = true
  AND next_run_at <= now()
  AND (expires_at IS NULL OR expires_at > now())
ORDER BY next_run_at
LIMIT $limit
FOR UPDATE SKIP LOCKED
```

Row lock lives for the duration of the beat transaction. Commit advances `next_run_at` + deactivates ONCE. Rollback leaves row due (at-least-once, never wedged).

### Expiry semantics

- **Create:** `_enforce_expiry` sets `is_active=False` + `next_run_at=None` if the schedule's next run would land after `expires_at`. A past `expires_at` immediately deactivates.
- **Resume:** Expired schedules stay dead.
- **mark_run:** Recomputes `next_run_at` via cron, then `_enforce_expiry` checks against deadline.

---

## 7. Manual smoke checklist

```bash
# Stack health
make verify

# Login
TOKEN=$(curl -s http://localhost:9002/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"e2e.alice@example.com","password":"Owner-pass-2026!"}' \
  | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

# Create workflow + step (project-scoped routes need ?project_id=...)
curl -s -X POST http://localhost:9002/api/v1/workflows/{WORKFLOW_ID}/steps \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"plugin":"ping","name":"p","plugin_config":{"hostname":"127.0.0.1"}}' \
  -G --data-urlencode "project_id={PROJECT_ID}"

# Execute
curl -s -X POST http://localhost:9002/api/v1/workflows/{WORKFLOW_ID}/execute \
  -H "Authorization: Bearer $TOKEN"
# → 201 with status "queued"

# Poll execution until terminal
curl -s http://localhost:9002/api/v1/workflow-executions/{EXECUTION_ID} \
  -H "Authorization: Bearer $TOKEN" \
  -G --data-urlencode "project_id={PROJECT_ID}"
# → status: "completed"

# Create schedule with expiry
curl -s -X POST http://localhost:9002/api/v1/projects/{PROJECT_ID}/schedules \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"workflow_id":"...","frequency":"once","expires_at":"2027-01-01T00:00:00Z"}'
# → 201 with expires_at echoed

# Past expiry creates inactive schedule
curl -s -X POST http://localhost:9002/api/v1/projects/{PROJECT_ID}/schedules \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"workflow_id":"...","frequency":"once","expires_at":"2020-01-01T00:00:00Z"}'
# → 201 with is_active: false
```

---

## 8. Rollback

Single revert commit undoes Phase 1:
- Domain: remove `cron.py`, revert `entities.py` / `repositories.py` / `exceptions.py`
- Application: revert `workflow_executor.py`, `schedule_service.py`
- API: revert deps / routers / schemas / error_handlers
- Infrastructure: revert models / repositories / tasks
- Migration: `alembic downgrade -1` removes the two columns
- Tests: remove new files, revert extended files

---

## 9. Decisions

| Decision | Rationale |
|----------|-----------|
| Pure domain cron parser (no croniter) | croniter not in `pyproject.toml`; 5-field Vixie cron is trivial to implement correctly |
| `NullScanTaskDispatcher` in workflow executor | Workflow steps dispatch via Celery to worker; within the step, `ScanService.create` must NOT enqueue another broker task — the worker runs `execution_engine.run(scan.id)` directly |
| Fail-closed targets (no `target_ids=[]`) | A step whose `plugin_config` can't resolve to a registered `Target` is a configuration error; failing loudly prevents silent no-ops |
| Fire-lock `FOR UPDATE SKIP LOCKED` | Postgres-native; no Redis/distributed lock needed; works with the existing async session model |
| Past `expires_at` creates inactive schedule (not 422) | Allows audit visibility; the schedule is visible but dead; consistent with `mark_run`/`resume` expiry behavior |
| RBAC server-side resolution for workflow routes | Prevents caller-supplied `?project_id=` bypass; project resolved from `workflow_id` or `execution_id` |

---

## 10. Non-goals explicitly deferred

- Scheduled autonomous campaigns (Phase 3)
- Webhooks / notifications (Phase 4)
- Orchestration metrics / governors (Phase 5)
- Workflow autonomy
- Outbox pattern
- CI/CD integration
