# SPECTER_AI — M7.5 Phase 2 Verification Package

## 1. Overview

**Phase:** M7.5 Phase 2 — Autonomous Run Project Isolation
**Date:** 2026-09-14
**Status:** Complete and verified. No commit/push/tag has been made.

### Scope

Eliminates a cross-project control-plane authorization vulnerability in the M7.4 autonomous orchestration API. Run-scoped autonomous routes (`GET /autonomous-runs/{run_id}`, `POST .../cancel|start-planning|plan-complete|cycle|approve|execution-complete|observation-complete|heartbeat`, `GET .../actions`, and `POST /autonomous-actions/{action_id}/approve|reject`) previously authorized the caller against the **caller-supplied** `?project_id=` query parameter. A user authorized in Project A could therefore manipulate a Project B run by supplying B's project_id — or could pass their *own legitimately-membered* project's id to operate on a *different* project's run.

After the fix, the **run resource itself is authoritative**: authenticated caller → load `AutonomousRun(run_id)` → derive `run.project_id` → authorize against that project. Actions are authorized only through their parent run (never independently). The `?project_id=` parameter remains legitimate *only* on the path-scoped create/list routes, where the path `project_id` IS the resource.

### Key guarantees delivered

| # | Guarantee | Proven by |
|---|-----------|-----------|
| 1 | `require_project_role_for_run()` resolves the run's owning project server-side; `?project_id=` is never consulted | Deps code review + `test_m75_phase2_run_isolation_api.py` (21 API tests) |
| 2 | Run-scoped GET works with **no** `?project_id=` query param | `test_alice_can_get_own_run_without_project_id_param` |
| 3 | `?project_id=` permutations cannot alter an authorized user's result | `test_alice_get_ignores_wrong_project_id_param` (200 kept); live check |
| 4 | Cross-project access denied even when the attacker passes *their own* project_id (the old exploit shape) | `test_bob_cannot_get_alice_run_even_with_alice_project_id` + live attack probes |
| 5 | Control ops (cancel / cycle / approve / start-planning / plan-complete / execution-complete / observation-complete / heartbeat) all gated on `run.project_id` + Owner/Admin | `test_control_ops_denied_to_other_project_owner` |
| 6 | READ_ONLY members can read runs but never control them | `test_read_only_member_can_read_but_not_control` |
| 7 | Orphan actions are refused: `action.project_id != run.project_id` → 400 (defense-in-depth) | `test_mismatched_action_project_id_rejected` |
| 8 | Nonexistent run → 400 domain-error; nonexistent action → 404 planned-action-not-found | `test_nonexistent_run_returns_domain_error`, `test_nonexistent_action_returns_404` |
| 9 | Create/list remain legitimately path-project-scoped | `test_create_still_respects_path_project_id`, `test_list_still_respects_path_project_id`, `test_owner_can_list_own_project_runs` |
| 10 | Live cross-project isolation on the running stack | 9/9 live checks incl. 3 real attack probes |

---

## 2. The vulnerability (before)

All run-scoped autonomous routes used the stock `require_project_role()` dependency, whose `project_id` parameter is **not** a path segment on these routes — FastAPI therefore binds it from the **query string**:

```python
# BEFORE (vulnerable)
async def cancel_autonomous_run(
    run_id: UUID,
    _member: ... = Depends(require_project_role(ProjectRole.OWNER, ProjectRole.ADMIN)),
    service: AutonomousService = Depends(get_autonomous_service),
) -> AutonomousRunResponse: ...
```

`require_project_role()` checks membership of `project_id` (query param) — the caller can choose *any* project they belong to. Attack:

1. Mallory is a member of Project A (her legit project).
2. She targets Victim's run `run_V` which lives in Project B.
3. `curl -X POST /api/v1/autonomous-runs/{run_V}/cancel?project_id=<A>` → membership check passes (Mallory ∈ A), so the Owner/Admin role gate passes → `service.cancel(run_V)` executes on Project B's run.

The run the caller actually operates on (`run_id`) and the project the caller is authorized in (`project_id` query param) could be completely unrelated.

---

## 3. The fix / target architecture (after)

```
Authenticated caller
  → load AutonomousRun(run_id)                     [service get]
  → derive run.project_id                          [resource-authoritative]
  → authorize against derived project              [require_member + optional role gate]
  → perform operation
```

Actions go through their parent run only:

```
Authenticated caller
  → load AutonomousRunAction(action_id)
  → verify action.run_id resolves AND action.project_id == run.project_id   [defense-in-depth]
  → authorize against run.project_id
  → perform operation
```

Two new dependency factories in `app/api/v1/deps.py`:

| Dependency | Behavior |
|------------|----------|
| `require_project_role_for_run(*allowed_roles)` | Loads the run via `AutonomousService.get(run_id)` (raises `AutonomousRunNotFoundError` → 400 for unknown ids), then applies the exact `require_project_role` semantics against `run.project_id` (project membership only, no org-admin bypass) and the optional role gate. Returns the `ProjectMember`. |
| `require_project_role_for_action(*allowed_roles)` | Loads the action via the action repository directly (raises `PlannedActionNotFoundError` → 404 when absent), loads the parent run, **rejects when `action.project_id != run.project_id`** (raises `AutonomousRunNotFoundError` → 400), then authorizes against `run.project_id`. |

Both mirror `require_project_role`'s semantics precisely: only project membership, no org-admin bypass — preserving M7.4 behavior. `_check_scan_launch_permission`'s org-admin bypass (used by M7.5 Phase 1) is deliberately **not** adopted here.

---

## 4. Changed & preserved semantics

### Changed (M7.5 Phase 2)

| Route | Before | After |
|-------|--------|-------|
| `GET /autonomous-runs/{run_id}` | `require_project_role()` on `?project_id=` | `require_project_role_for_run()` — param ignored |
| `POST /autonomous-runs/{run_id}/cancel` | `require_project_role(OWNER, ADMIN)` on `?project_id=` | `require_project_role_for_run(OWNER, ADMIN)` |
| `POST .../start-planning` | same | `require_project_role_for_run(OWNER, ADMIN)` |
| `POST .../plan-complete` | same | `require_project_role_for_run(OWNER, ADMIN)` |
| `POST .../cycle` | same | `require_project_role_for_run(OWNER, ADMIN)` |
| `POST .../approve` | same | `require_project_role_for_run(OWNER, ADMIN)` |
| `POST .../execution-complete` | same | `require_project_role_for_run(OWNER, ADMIN)` |
| `POST .../observation-complete` | same | `require_project_role_for_run(OWNER, ADMIN)` |
| `POST .../heartbeat` | `require_project_role()` on `?project_id=` | `require_project_role_for_run()` |
| `GET .../actions` | `require_project_role()` on `?project_id=` | `require_project_role_for_run()` |
| `POST /autonomous-actions/{action_id}/approve` | `require_project_role(OWNER, ADMIN)` on `?project_id=` | `require_project_role_for_action(OWNER, ADMIN)` |
| `POST /autonomous-actions/{action_id}/reject` | same | `require_project_role_for_action(OWNER, ADMIN)` |

### Preserved (behavior unchanged)

- **Create** `POST /projects/{project_id}/autonomous-runs` — path `project_id` scoping is legitimate (the resource is created *in* that project). Kept `require_project_role(OWNER, ADMIN)`.
- **List** `GET /projects/{project_id}/autonomous-runs` — path-scoped filter. Kept `require_project_role()`.
- Role semantics: `(OWNER, ADMIN)` for control ops, any member for get/heartbeat/list_actions. **No org-admin bypass** (unchanged from M7.4).
- Error contract: `NotAProjectMemberError` → 403 `not-a-project-member`; `InsufficientPermissionError` → 403 `insufficient-permission`; `PlannedActionNotFoundError` → 404 `planned-action-not-found`; `AutonomousRunNotFoundError` → 400 `domain-error`. No error-handler changes were needed.
- **M7.4 smoke tests keep passing unchanged** — they already pass `?project_id=` on run-scoped calls; after the fix FastAPI simply ignores the unknown query parameter on those routes.

---

## 5. Files changed

| File | Change |
|------|--------|
| `backend/app/api/v1/deps.py` | Added `require_project_role_for_run(*allowed_roles)` and `require_project_role_for_action(*allowed_roles)` in the M7.5 Phase 2 section; hoisted `AutonomousRunNotFoundError`, `PlannedActionNotFoundError` into the module import block. (+75) |
| `backend/app/api/v1/routers/autonomous.py` | All 12 run/action-scoped routes switched to the new resource-first deps; imports updated. Create/list untouched. (−16/+28) |
| `backend/tests/api/test_m75_phase2_run_isolation_api.py` | **NEW.** 21 API tests with in-memory service/repo fakes and dependency overrides (no DB). |

No schema change, no migration — Phase 2 is authorization-only.

---

## 6. Test results — full regression

```
891 passed, 2 warnings in 104.17s (0:01:44)
```

Baseline (Phase 1): 870 passed / 0 failed / 2 warnings. Delta: **+21 tests** (the Phase 2 isolation suite), zero regressions. The 2 warnings are the pre-existing asyncpg coroutine-leak ResourceWarnings, unchanged from baseline.

### Breakdown by group

| Group | Count | Notes |
|-------|-------|-------|
| `tests/unit/` + `tests/api/` (no-DB) | 866 passed | includes the new 21-test isolation suite |
| `tests/integration/` (real Postgres + live stack) | 72 passed | fire-lock, repositories, M7.4 + M7.5 Phase 1 live suites |

### Live API smoke (post-restart, new code)

```
tests/api/test_m75_phase2_run_isolation_api.py   21 passed
tests/integration/test_m74_api_smoke.py           6 passed (M7.4, no regression — unchanged file)
tests/integration/test_m75_phase1_live.py         3 passed (M7.5 Phase 1, no regression)
```

---

## 7. Test results — isolation suite detail

`tests/api/test_m75_phase2_run_isolation_api.py` — real ASGI app, fakes injected via `dependency_overrides` (identity, project/org services, autonomous service, action repo, orchestrator bomb). Dispatches the `?project_id=` credential-sneak attack table.

| Test | Expectation |
|------|-------------|
| `test_alice_can_get_own_run_without_project_id_param` | 200 with no query param at all |
| `test_alice_get_ignores_wrong_project_id_param` | 200 when passing `?project_id=<project_b>` |
| `test_bob_cannot_get_alice_run_even_with_alice_project_id` | 403 `not-a-project-member` |
| `test_bob_cannot_get_alice_run_without_params` | 403 |
| `test_other_org_owner_cannot_get_run` | 403 (org B owner) |
| `test_org_admin_without_project_membership_cannot_get_run` | 403 (no org bypass) |
| `test_alice_can_cancel_own_run` | 200 → `cancelled` |
| `test_bob_cannot_cancel_alice_run` | 403; run stays `created` |
| `test_control_ops_denied_to_other_project_owner` | 403 for start-planning, plan-complete, cycle, approve, execution-complete, observation-complete, heartbeat; run stays `created` |
| `test_read_only_member_can_read_but_not_control` | GET 200, cancel 403 `insufficient-permission` |
| `test_bob_cannot_control_his_own_project_via_error_setup` | positive control / sanity |
| `test_alice_can_approve_own_action` | 200 → `approved` |
| `test_bob_cannot_approve_alice_action_even_with_project_id` | 403; action stays `proposed` |
| `test_bob_cannot_reject_bob_run_via_alice_identity` | alice 403 on bob's action (approve + reject) |
| `test_other_org_owner_cannot_approve_action` | 403 |
| `test_mismatched_action_project_id_rejected` | 400 defense-in-depth (forged action) |
| `test_nonexistent_run_returns_domain_error` | 400 `domain-error` |
| `test_nonexistent_action_returns_404` | 404 `planned-action-not-found` |
| `test_create_still_respects_path_project_id` | 403 for non-member (create unchanged) |
| `test_list_still_respects_path_project_id` | 403 for non-member (list unchanged) |
| `test_owner_can_list_own_project_runs` | 200, only own project's runs returned |

---

## 8. Live validation results

Stack: `docker compose -f infra/docker-compose.yml` (api/postgres/redis/minio), API at `localhost:9002`. The api container was **restarted** to serve the new code (source is volume-mounted at `/app/app`; uvicorn runs without `--reload`).

Actors: **alice** = `e2e.alice@example.com` / `Owner-pass-2026!` (seeded, owns Project A); **bob** = freshly registered user owning his own org + Project B. Each created a run (`run_a`, `run_b`).

| Check | Result |
|-------|--------|
| alice reads own run — **no query param** | 200 |
| alice read with `?project_id=bob_project` (permutation) | 200 |
| bob reads alice's run — no param | 403 `not-a-project-member` |
| **ATTACK** bob reads alice's run with `?project_id=bob_project` (old code: 200) | 403 |
| **ATTACK** bob cancels alice's run with `?project_id=bob_project` (old code: 200) | 403 |
| **ATTACK** bob cycles alice's run with `?project_id=bob_project` (old code: 200) | 403 |
| alice cancels bob's run with `?project_id=alice_project` (reverse direction) | 403 |
| run_a status untouched after all denial attempts | 200, `created` |
| alice performs legitimate control op (cancel own run) | 200, `cancelled` |

**9/9 live checks passed.** Runs cleaned up at the end.

---

## 9. Gates

| Gate | Result |
|------|--------|
| **ruff** | All touched files pass (`ruff check --fix` on deps.py, autonomous.py, new test file → 0 remaining). |
| **black** | deps.py unchanged; autonomous.py + test file reformatted clean. |
| **mypy** | Baseline preserved: 38 errors / 11 files (pre-existing debt tolerated); **0 errors in touched files** (`app/api/v1/deps.py`, `app/api/v1/routers/autonomous.py`). Ran with `--python-executable "...\Python311\python.exe"`. |
| **pytest** | 891 passed / 0 failed / 2 baseline warnings. |

---

## 10. Attack-surface coverage mapping

| Vector | Fix that closes it | Verified |
|--------|--------------------|----------|
| Caller passes victim project's id (`?project_id=B`) while authorized in A | Run resource overrides the query param entirely | API + live |
| Caller passes *their own* project's id (`?project_id=A`) to operate on B's run | `run.project_id` is B; membership of A is irrelevant | API + live (attack probes) |
| Caller drops the param / param absent | Param is no longer a route parameter — request succeeds or fails purely on `run.project_id` | API + live |
| Caller forges an action whose `project_id` mismatches the parent run | `action.project_id != run.project_id` → 400 before any authorization | API |
| Org admin (no project membership) escalates | No org-admin bypass; `require_member` against `run.project_id` | API |
| READ_ONLY member escalates on control ops | Owner/Admin role gate preserved on `run.project_id` | API |
| Unknown run id / action id blasted at endpoints | 400 / 404 respectively, no privilege signal leaked | API |

---

## 11. Architecture notes

### Resource-first dependency pattern

The new deps follow the `require_project_role_for_target` precedent (M7-era fix for `target_id`-keyed routes): load the resource, derive its owning project, then run the identical membership + role gate used everywhere else. No new RBAC layer — a run's permissions are always its project's permissions.

### Why no org-admin bypass here

`require_project_role`'s contract is "project membership + optional role", with no org bypass. M7.5 Phase 1's `_check_scan_launch_permission` *does* grant org-admins scan-launch rights, but the autonomous control plane must keep M7.4 semantics (a run's approvals/cancellations are project decisions). Adopting the Phase 1 shorthand would have silently widened the primitive. The full signature-neutral check is applied instead.

### Error surfaces unchanged

No new exceptions, no new handler mappings. Reused: `NotAProjectMemberError` (403), `InsufficientPermissionError` (403), `PlannedActionNotFoundError` (404), `AutonomousRunNotFoundError` (default → 400 `domain-error`). The mismatch case intentionally surfaces as `AutonomousRunNotFoundError` — same slug as a missing run, so a forged action gives no useful oracle to a scanner.

---

## 12. Decisions

| Decision | Rationale |
|----------|-----------|
| Run resource is the authz root; `?project_id=` dropped from run-scoped routes | The run is the durable, unforgeable object; the caller-supplied param was the vuln. FastAPI ignores unknown query params, so old clients degrade gracefully (param no-ops). |
| Actions authorized only through the parent run | An action's permissions ARE its run's permissions; independent authorization would recreate the same bypass a level down. |
| `action.project_id != run.project_id` → 400 (not 403) | Defense-in-depth + no oracle: same response as a nonexistent run. |
| Reuse `require_project_role` semantics verbatim (no org bypass) | Preserves M7.4 exactly; change is a *binding* fix, not a semantics change. |
| No schema / migration | Pure authorization-binding change; migration would buy nothing. |
| `get_autonomous_action_repository` injected directly into the action dep | The action load needs no state-machine side effects; `AutonomousService._get_action` is private and also enforces nothing beyond existence. |

---

## 13. Non-goals explicitly deferred

- Phase 3 of M7.5 (scheduled autonomous campaigns) — not started.
- Remaining mypy debt (baseline 38 errors / 11 files in `app/`) — pre-existing, gate held at "no new errors".
- Repo-wide ruff debt (UP042/N801 etc.) — untouched.
- Audit-log events for authorization denials on autonomous routes.
- Rate limiting / anti-fuzzing on the autonomous control plane.

---

## 14. Manual smoke checklist

```bash
# 1. Login (owner)
TOKEN=$(curl -s http://localhost:9002/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"e2e.alice@example.com","password":"Owner-pass-2026!"}' \
  | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

# 2. Create a run (path-scoped create — needs a project)
curl -s -X POST http://localhost:9002/api/v1/projects/{PID}/autonomous-runs \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"objective":"demo"}'          # → 201 created

# 3. Run-scoped GET works WITHOUT ?project_id=
curl -s http://localhost:9002/api/v1/autonomous-runs/{RUN_ID} \
  -H "Authorization: Bearer $TOKEN"  # → 200

# 4. A stranger (their own project id) is refused on the run
curl -s -X POST http://localhost:9002/api/v1/autonomous-runs/{RUN_ID}/cancel \
  -H "Authorization: Bearer $STRANGER_TOKEN" \
  -G --data-urlencode "project_id={STRANGER_OWN_PROJECT}"   # → 403 not-a-project-member

# 5. Owner can still cancel their own run
curl -s -X POST http://localhost:9002/api/v1/autonomous-runs/{RUN_ID}/cancel \
  -H "Authorization: Bearer $TOKEN"                          # → 200 cancelled
```

---

## 15. Rollback

Three files revert the phase entirely (no migration):
- Revert `app/api/v1/deps.py` (remove the two new deps + hoisted imports).
- Revert `app/api/v1/routers/autonomous.py` (swap the 12 routes back to `require_project_role`).
- Delete `tests/api/test_m75_phase2_run_isolation_api.py`.

No schema objects, no data, no new dependencies — a git checkout of `deps.py` + `autonomous.py` plus test removal is the full rollback.

---

## 16. Environment notes

- **Utilities:** Python 3.11.9 worker (repo targets 3.12; black emits the known py311-parse warning but formats correctly), ruff, black, mypy (strict, pydantic plugin).
- **DB integration runs use:** `$env:DATABASE_URL = "postgresql+asyncpg://specter:specter@localhost:5432/specter"`.
- **mypy gate command:** `python -m mypy app --python-executable "C:\Users\ALAEN JOSHVA\AppData\Local\Programs\Python\Python311\python.exe"`.
- **The `-m "requires_postgres"` selector does not work** (the marker is implemented via `pytest.mark.skipif`); run `pytest tests/integration/` with `DATABASE_URL` set instead.

---

## 17. Phase 1 guarantee regression check

| Phase 1 guarantee | Re-verified? |
|-------------------|--------------|
| Workflow executes through canonical scan path | `test_m75_phase1_live.py` (passed) |
| Fire-lock exactly-one-winner on real Postgres | `test_m75_phase1_firelock_integration.py` (in 72-test integration run) |
| RBAC server-side resolution (`require_workflow_execution_permission`, `require_scan_launch_permission`) | `test_m75_phase1_live.py` (passed) |
| Real cron parser | `test_cron.py` (in full-suite run) |
| Migration `c0d1e2f3a4b5` remains applied | stack untouched by Phase 2 (no new migration) |

---

## 18. Summary

M7.5 Phase 2 closes the autonomous-run cross-project authorization hole by making the run (and its owning project) the exclusive authorization root. All 12 run/action-scoped routes now authorize against `run.project_id` — never a caller-supplied `?project_id=`. The legacy query param is silently ignored on those routes, so existing M7.4 smoke clients keep working unchanged. Verified by 21 new API tests (0 regressions across 891 total), ruff/black clean, mypy at baseline with no new errors, and 9/9 live cross-project checks on the restarted stack — including the three real attack probes. **No commit/push/tag has been made.**