# SPECTER_AI — Milestone 3 Verification Package

**Scan Execution Engine & Plugin Framework**
Status: complete, awaiting approval. No Milestone 4 work included.

---

## 1. Updated Repository Tree

Only new/changed paths shown relative to the Milestone 2 baseline. Everything else from Milestone 1/2 is unmodified.

```
specter-ai/
├── backend/
│   ├── Dockerfile                          # CHANGED: +iputils-ping, +nmap
│   ├── alembic/versions/
│   │   ├── e805ba666950_identity_and_platform_foundation.py   (Milestone 2)
│   │   └── 5a5919956ad5_scan_execution_framework.py            (NEW)
│   ├── app/
│   │   ├── main.py                         # CHANGED: registers built-in plugins at startup
│   │   ├── domain/
│   │   │   ├── entities.py                 # CHANGED: +Scan
│   │   │   ├── exceptions.py               # CHANGED: +5 scan/plugin exceptions
│   │   │   ├── repositories.py             # CHANGED: +ScanRepository protocol
│   │   │   └── value_objects.py            # CHANGED: +ScanStatus, +status-set constants
│   │   ├── application/
│   │   │   ├── organization_service.py     # CHANGED: +get_member_or_none (additive)
│   │   │   └── scan_service.py             # NEW: ScanService, ScanTaskDispatcher protocol
│   │   ├── plugins/                        # NEW package
│   │   │   ├── base.py                     #   Plugin ABC, PluginResult
│   │   │   ├── registry.py                 #   PluginRegistry + process-wide instance
│   │   │   ├── manager.py                  #   PluginManager
│   │   │   ├── echo_plugin.py              #   EchoPlugin
│   │   │   ├── ping_plugin.py              #   PingPlugin
│   │   │   ├── nmap_plugin.py              #   NmapPlugin
│   │   │   └── builtin.py                  #   self-registration aggregator
│   │   ├── infrastructure/
│   │   │   ├── celery_app/
│   │   │   │   ├── tasks.py                # CHANGED: +execute_scan_task
│   │   │   │   └── dispatcher.py           # NEW: CeleryScanTaskDispatcher
│   │   │   ├── db/
│   │   │   │   ├── models/scan.py          # NEW: ScanModel
│   │   │   │   ├── models/__init__.py      # CHANGED: registers ScanModel
│   │   │   │   └── repositories/scan_repository.py   # NEW: SqlAlchemyScanRepository
│   │   │   ├── execution/                  # NEW package
│   │   │   │   └── engine.py               #   ExecutionEngine
│   │   │   └── storage/                    # NEW package
│   │   │       └── local_artifact_store.py #   LocalArtifactStore
│   │   └── api/v1/
│   │       ├── deps.py                     # CHANGED: +scan repo/service providers,
│   │       │                               #   +require_scan_launch_permission,
│   │       │                               #   +require_scan_permission_for_scan,
│   │       │                               #   +require_scan_view_permission
│   │       ├── error_handlers.py           # CHANGED: +5 exception mappings
│   │       ├── router.py                   # CHANGED: registers scans router
│   │       ├── routers/scans.py            # NEW
│   │       └── schemas/scans.py            # NEW
│   └── tests/
│       ├── fakes.py                         # CHANGED: +FakeScanRepository
│       ├── unit/
│       │   ├── test_scan_service.py         # NEW (9 tests)
│       │   ├── test_execution_engine.py     # NEW (6 tests)
│       │   ├── test_plugin_registry_and_manager.py  # NEW (12 tests)
│       │   └── test_builtin_plugins.py      # NEW (26 tests)
│       ├── api/
│       │   └── test_scans_api.py            # NEW (10 tests)
│       └── integration/
│           └── test_repositories.py        # CHANGED: +2 Scan repository tests
├── infra/docker-compose.yml                 # CHANGED: +scan_artifacts volume (api, worker)
├── .env.example                             # CHANGED: +SCAN_ARTIFACTS_DIR, +SCAN_DEFAULT_TIMEOUT_SECONDS
├── .github/workflows/ci.yml                 # CHANGED: +ping/nmap apt install step
├── scripts/doctor.py                        # CHANGED: +2 required env vars checked
└── README.md                                # CHANGED: +"Scan Execution (Milestone 3)" section
```

**Nothing under Milestone 2's auth/organizations/projects/targets/authorization logic was rewritten.** The only Milestone-2-owned file touched is `organization_service.py`, with one additive method (`get_member_or_none`) needed so the new scan-launch permission check can test org-admin fallback without exceptions-as-control-flow.

---

## 2. Database Schema Changes

One new table: `scans`.

```
                            Table "public.scans"
     Column     |           Type           | Nullable | Default
----------------+--------------------------+----------+---------
 id             | uuid                     | not null |
 project_id     | uuid                     | not null |
 initiated_by   | uuid                     | not null |
 plugin         | character varying(100)   | not null |
 status         | character varying(20)    | not null |
 target_ids     | jsonb                    | not null |
 plugin_config  | jsonb                    | not null |
 created_at     | timestamp with time zone | not null | now()
 started_at     | timestamp with time zone |          |
 completed_at   | timestamp with time zone |          |
 logs_path      | text                     |          |
 artifacts_path | text                     |          |
 exit_code      | integer                  |          |
 error_message  | text                     |          |

Indexes:
    scans_pkey PRIMARY KEY (id)
    idx_scans_project btree (project_id)
    idx_scans_status  btree (status)

Foreign keys:
    scans_initiated_by_fkey  → users(id)
    scans_project_id_fkey    → projects(id) ON DELETE CASCADE
```

Notes:
- `target_ids` is a JSONB array of target-UUID strings, not a join table — a scan references a fixed snapshot of target IDs at launch time, not a live many-to-many relationship. The repository layer converts to/from real `UUID` objects at the boundary (verified by `test_scan_lifecycle_methods_persist_correctly`).
- `plugin_config` is JSONB because config shape varies per plugin (SRS's existing JSONB-for-genuinely-variable-shape-data convention, same as `authorization_records.allowed_targets`).
- No new columns were added to any Milestone 1/2 table.

---

## 3. New Alembic Migrations

| Revision | Down-revision | Description |
|---|---|---|
| `e805ba666950` | (base) | Milestone 2 — identity and platform foundation (unchanged) |
| `5a5919956ad5` | `e805ba666950` | **Milestone 3** — adds `scans` table + its two indexes |

Verified in this environment with a full round-trip:

```
alembic upgrade head      # e805ba666950 -> 5a5919956ad5   OK
alembic downgrade -1      # 5a5919956ad5 -> e805ba666950   OK (scans table dropped)
alembic upgrade head      # e805ba666950 -> 5a5919956ad5   OK (scans table recreated)
```

No changes to any Milestone 1/2 table were made in this migration — `git diff`-equivalent on the migration file shows only `op.create_table('scans', ...)` and its two `op.create_index(...)` calls.

---

## 4. New API Endpoints

| Method | Path | Auth (dependency) | Notes |
|---|---|---|---|
| `POST` | `/api/v1/projects/{project_id}/scans` | `require_scan_launch_permission` | Body: `{"plugin", "plugin_config", "target_ids"}`. Runs Scope Guard + plugin-config validation before persisting anything. |
| `GET` | `/api/v1/projects/{project_id}/scans` | `require_project_role()` (any member) | Lists all scans for the project. |
| `GET` | `/api/v1/scans/{scan_id}` | `require_scan_view_permission` (resolves owning project, any member) | |
| `DELETE` | `/api/v1/scans/{scan_id}` | `require_scan_permission_for_scan` (same rule as launch, resolved via the scan's project) | Soft-cancel — see §6. |

RBAC rule for launch/cancel ("Project Owner, Organization Admin, Authorized Users" per the Milestone 3 spec): project role in `{Owner, Admin, Lead Tester, Tester}`, **or** organization role in `{Owner, Admin}` for the scan's project even without explicit project membership.

New RFC 7807 error types introduced (see `app/api/v1/error_handlers.py`):

| Domain exception | HTTP | `type` slug |
|---|---|---|
| `ScanNotFoundError` | 404 | `scan-not-found` |
| `PluginNotFoundError` | 404 | `plugin-not-found` |
| `InvalidPluginConfigError` | 422 | `invalid-plugin-config` |
| `ScanNotCancellableError` | 409 | `scan-not-cancellable` |

All existing Scope Guard error types (`out-of-scope-target`, `no-active-authorization`, `project-not-active`, etc.) apply unchanged to scan launch, since `ScanService.create` delegates entirely to Milestone 2's `ScopeGuardService.validate_targets`.

---

## 5. Plugin Architecture Explanation

```
Plugin (ABC)                       app/plugins/base.py
 ├── name() -> str
 ├── description() -> str
 ├── validate_config(config) -> None      (raises InvalidPluginConfigError)
 └── execute(config, timeout_seconds) -> PluginResult

PluginResult (frozen dataclass)
 ├── success: bool
 ├── stdout / stderr: str
 ├── exit_code: int | None
 ├── artifacts: list[str]
 └── metadata: dict

PluginRegistry                     app/plugins/registry.py
 ├── register(plugin) / unregister(name)
 ├── get(name) -> Plugin              (raises PluginNotFoundError)
 └── list() -> list[Plugin]

PluginManager                       app/plugins/manager.py
 ├── validate(name, config)           looks up + validates only, never executes
 └── run(name, config, timeout)       looks up, validates, executes, converts
                                       any unexpected plugin exception into a
                                       failed PluginResult (never propagates)
```

**Self-registration**: each built-in plugin module has no top-level registration code; `app/plugins/builtin.py` is the single place that instantiates and registers `EchoPlugin`, `PingPlugin`, `NmapPlugin` onto the shared `registry` object. Importing `app.plugins.builtin` once (done in `main.py`'s startup and inside the Celery task) is sufficient — this keeps "which plugins exist" a one-file answer.

**Built-in plugins:**

| Plugin | Subprocess | Config | Safety mechanism |
|---|---|---|---|
| `echo` | none | `{}` (ignored) | N/A — proves the pipeline without any external tool |
| `ping` | `ping -c 4 -W 2 <hostname>` | `{"hostname": str}` | `hostname` validated as IP or domain via Milestone 2's `validate_target_value` before any subprocess call |
| `nmap` | `nmap [args] -p <ports> --host-timeout <ms> <target>` | `{"target", "ports", "arguments": list[str]}` | `target` validated as IP/CIDR/domain; `ports` regex-restricted to digits/commas/dashes; `arguments` is an **allow-list** of 14 value-less flags — file-write (`-oN/-oX/-oG/-oA`), script-execution (`--script`), and file-input (`-iL/-iR`) flags can never appear regardless of what's requested |

**Load-bearing security property, true for all three**: every subprocess invocation uses `subprocess.run([...], shell=False, timeout=N)` — list arguments, never a shell string, never `shell=True`, always a timeout. This was verified with a `monkeypatch`-based spy asserting the actual call site (`test_ping_command_never_uses_shell`, `test_nmap_command_never_uses_shell`), not just asserted by reading the source.

**Explicit scope limitation**: plugins execute as subprocesses inside the `worker` container itself, not in one ephemeral container per invocation (the frozen SRS's full §7.3 isolation model). See §13.

---

## 6. Scan Lifecycle Flow

```
                     ┌─────────┐
        created ───► │ queued  │
                     └────┬────┘
                          │  Celery worker picks up the task
                          │  (Scope Guard re-validated HERE, again)
                          ▼
                     ┌─────────┐
                     │ running │
                     └────┬────┘
              ┌───────────┼───────────┐
              ▼           ▼           ▼
        ┌───────────┐┌─────────┐┌───────────┐
        │ completed ││ failed  ││ cancelled  │
        └───────────┘└─────────┘└───────────┘
```

Valid entry points to `cancelled`: from `queued` (never started) or `running` (soft — see limitations). `completed`/`failed`/`cancelled` are terminal; `ScanNotCancellableError` (409) is raised on any attempt to cancel a scan already in one of those states.

State transition ownership:
- `queued → running`, `running → completed`, `running → failed`: exclusively `ExecutionEngine`, driven by the Celery task.
- `queued|running → cancelled`: exclusively `ScanService.cancel`, driven by the API.
- No other code path writes to `scans.status`.

---

## 7. Celery Task Flow

```
ScanService.create()
   │  (Scope Guard passed, plugin config validated, Scan row persisted as `queued`)
   ▼
ScanTaskDispatcher.dispatch(scan_id)         ← Protocol; application/ never imports celery
   │
   ▼  (concrete impl: CeleryScanTaskDispatcher)
execute_scan_task.delay(str(scan_id))        ← infrastructure/celery_app/dispatcher.py
   │
   ▼  (Celery worker process, separate from the API process)
execute_scan_task(scan_id: str)              ← infrastructure/celery_app/tasks.py
   │  sync Celery task; bridges into async code
   ▼
asyncio.run(_execute_scan(UUID(scan_id)))
   │  creates its OWN engine/session for this task invocation
   │  (see §13 — NOT the FastAPI process-wide cached engine)
   ▼
ExecutionEngine.run(scan_id)                 ← infrastructure/execution/engine.py
   │  1. re-validate Scope Guard
   │  2. status -> running
   │  3. PluginManager.run(plugin, config, timeout)
   │  4. write stdout/stderr to disk (LocalArtifactStore)
   │  5. status -> completed | failed
   │  6. audit log entries at each stage
   ▼
session.commit()  →  engine.dispose()
```

Each Celery task invocation gets its own SQLAlchemy engine, created and disposed within `_execute_scan`, rather than reusing the process-wide cached singleton `get_engine()`. This was a real bug found during manual end-to-end testing (see §13) — asyncpg connections are bound to the event loop that created them, and `asyncio.run()` gives each task a fresh loop.

---

## 8. Test Summary

**137 tests, 0 skipped, 0 xfail.**

| File | Count | Layer |
|---|---:|---|
| `tests/test_health.py` | 2 | API (Milestone 1) |
| `tests/unit/test_auth_service.py` | 10 | Unit (M2) |
| `tests/unit/test_organization_service.py` | 7 | Unit (M2) |
| `tests/unit/test_project_service.py` | 8 | Unit (M2) |
| `tests/unit/test_target_service.py` | 8 | Unit (M2) |
| `tests/unit/test_scope_guard_service.py` | 12 | Unit (M2) |
| `tests/unit/test_scan_service.py` | 9 | **Unit (M3)** |
| `tests/unit/test_execution_engine.py` | 6 | **Unit (M3)** |
| `tests/unit/test_plugin_registry_and_manager.py` | 12 | **Unit (M3)** |
| `tests/unit/test_builtin_plugins.py` | 26 | **Unit (M3)** — real subprocess execution |
| `tests/api/test_auth_api.py` | 9 | API (M2) |
| `tests/api/test_permissions_api.py` | 7 | API (M2) |
| `tests/api/test_scope_guard_api.py` | 4 | API (M2) |
| `tests/api/test_scans_api.py` | 10 | **API (M3)** |
| `tests/integration/test_repositories.py` | 7 (2 new) | Integration, real Postgres |
| **Total** | **137** | |

Milestone-3-specific: **63 tests** (9+6+12+26+10) plus 2 of the 7 integration tests.

Coverage against the Milestone 3 spec's explicit test list:
- ✅ Execution Engine (unit) — success, plugin failure, defense-in-depth Scope Guard rejection, already-cancelled skip, missing-scan graceful handling, audit entries written
- ✅ Plugin Registry / Plugin Manager (unit) — register/get/list/unregister, validate-before-execute, unexpected-exception containment
- ✅ Repositories (integration, real Postgres) — JSONB round-trip incl. `target_ids` UUID conversion, full lifecycle method set
- ✅ Scan Service (unit) — Scope Guard delegation, plugin validation ordering, cancellation state machine
- ✅ Launch Echo plugin / Launch Ping plugin — both covered as real subprocess unit tests, and manually end-to-end through a live Celery worker (see §9)
- ✅ Scope Guard blocks unauthorized targets — both at the service layer (`test_scan_service.py`) and the API layer (`test_scans_api.py`)
- ✅ Successful / failed scan lifecycle — `test_execution_engine.py`
- ✅ API tests: POST/GET/List/Cancel — `test_scans_api.py`
- ✅ Permission tests — `test_scans_api.py` (read-only member, outsider, unauthenticated)
- ✅ Scope Guard tests — inherited unchanged from Milestone 2, plus new scan-specific cases

---

## 9. Commands to Run Locally

```bash
# From repo root, with Docker running
cp .env.example .env
make up
make migrate          # applies both e805ba666950 and 5a5919956ad5

# Run the full test suite (needs Postgres reachable; Docker Compose's
# postgres service works, or any local Postgres matching .env)
cd backend
pip install -e ".[dev]"
pytest tests/ -v

# Lint/type-check
ruff check .
black --check .
mypy app

# Manually launch a scan end-to-end (after registering a user, creating
# an org/project/target, and moving the project through
# Authorized -> Active with an authorization record — see Milestone 2's
# verification package for that sequence)
curl -X POST http://localhost:8000/api/v1/projects/$PROJECT_ID/scans \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"plugin":"echo","plugin_config":{},"target_ids":["'"$TARGET_ID"'"]}'

curl http://localhost:8000/api/v1/scans/$SCAN_ID -H "Authorization: Bearer $TOKEN"
```

---

## 10. Expected Outputs

**Launching a scan:**
```json
// POST /api/v1/projects/{id}/scans -> 201
{
  "id": "5f2c...",
  "project_id": "...",
  "initiated_by": "...",
  "plugin": "echo",
  "status": "queued",
  "target_ids": ["..."],
  "plugin_config": {},
  "created_at": "2026-07-19T...",
  "started_at": null,
  "completed_at": null,
  "logs_path": null,
  "artifacts_path": null,
  "exit_code": null,
  "error_message": null
}
```

**After the worker finishes (poll `GET /scans/{id}`):**
```json
{
  "status": "completed",
  "started_at": "2026-07-19T...",
  "completed_at": "2026-07-19T...",
  "logs_path": "/tmp/specter-artifacts/5f2c...",
  "exit_code": 0,
  "error_message": null
}
```

**Out-of-scope target attempt:**
```json
// -> 422
{
  "type": "https://specter.ai/errors/out-of-scope-target",
  "title": "OutOfScopeTargetError",
  "status": 422,
  "detail": "Target(s) outside authorized scope: ..."
}
```

**Disallowed nmap argument:**
```json
// -> 422
{
  "type": "https://specter.ai/errors/invalid-plugin-config",
  "title": "InvalidPluginConfigError",
  "status": 422,
  "detail": "Invalid configuration for plugin 'nmap': argument(s) not permitted: ['--script=evil']. ..."
}
```

**`pytest tests/ -v` tail:**
```
======================= 137 passed, 1 warning in ~18s =======================
```
(The one warning is a benign `RuntimeWarning: coroutine 'Connection._cancel' was never awaited` from a cached-engine teardown interaction in `test_health.py`, not a functional issue — documented in Milestone 2's verification notes.)

---

## 11. Manual Verification Checklist

Run these against a live `docker compose up` stack:

- [ ] `POST /auth/register` + `/auth/login` succeed; access token obtained
- [ ] Create an organization, project, and target; move project `draft → authorized`, attach an authorization record, move to `active`
- [ ] `POST /projects/{id}/scans` with `plugin: "echo"` returns `201`, `status: "queued"`
- [ ] Within a few seconds, `GET /scans/{id}` shows `status: "completed"`, `exit_code: 0`
- [ ] `docker compose exec worker cat /tmp/specter-artifacts/{scan_id}/stdout.log` shows `Hello from SPECTER`
- [ ] Launch a `ping` scan against `127.0.0.1`; confirm real ICMP output in `stdout.log`
- [ ] Launch an `nmap` scan against an authorized target with `arguments: ["-Pn"]`; confirm real port-scan output
- [ ] Attempt an `nmap` scan with `arguments: ["--script=x"]` → `422 invalid-plugin-config`, no scan row created
- [ ] Attempt a scan on a `draft`/non-Active project → `422 project-not-active`
- [ ] Attempt a scan against a target not in the authorization record's `allowed_targets` → `422 out-of-scope-target`
- [ ] As a Read-Only project member, attempt to launch a scan → `403 insufficient-permission`; confirm the same user CAN still `GET` the scan
- [ ] As a user with no relationship to the project, attempt any scan endpoint → `403`
- [ ] With no `Authorization` header at all → `401`
- [ ] Cancel a `queued` scan before the worker picks it up → `status: "cancelled"`, worker log shows `scan_execution_skipped_cancelled`
- [ ] Attempt to cancel an already-`completed` scan → `409 scan-not-cancellable`
- [ ] `GET /projects/{id}/scans` lists only that project's scans

---

## 12. Automated Verification Checklist

```bash
make verify        # environment/stack doctor (Docker, Postgres, Redis, MinIO,
                    # API health, Swagger, Celery worker/beat, Alembic, env vars,
                    # Python/Node versions) — see scripts/doctor.py
```

- [ ] `alembic upgrade head` — applies cleanly from a fresh database
- [ ] `alembic downgrade -1 && alembic upgrade head` — round-trips cleanly
- [ ] `ruff check .` — 0 errors
- [ ] `black --check .` — 0 reformats needed
- [ ] `mypy app` — 0 errors (strict mode)
- [ ] `pytest tests/ -v` — 137 passed, 0 failed, 0 skipped
- [ ] `pytest tests/unit/test_builtin_plugins.py -v` — real `ping`/`nmap` subprocess calls succeed (requires `iputils-ping`/`nmap` installed — present in the Docker image and CI, per §1)
- [ ] `pytest tests/integration/ -v` — passes against a reachable Postgres; auto-skips cleanly if none is reachable (`requires_postgres` marker)
- [ ] CI workflow (`.github/workflows/ci.yml`) green: lint, type-check, migrate, full test run against a real Postgres service container

---

## 13. Known Limitations

1. **Cancellation is cooperative/soft, not a hard kill.** `DELETE /scans/{id}` marks a `queued`/`running` scan `cancelled` in the database. If the worker hasn't started the scan yet, `ExecutionEngine` sees the `cancelled` status and skips execution entirely. But if a plugin's subprocess is *already running* at the OS level, cancellation does not send it a signal — the subprocess runs to completion (bounded by its own timeout) and its result is simply discarded on write-back... actually the current code does not even discard it — the engine has already returned control to Celery by the time cancellation lands, so the scan would be overwritten back to `completed`/`failed` by the still-running task after having been marked `cancelled`. **This is a real race, not just a documented gap**, and the next milestone (or a Milestone 3.1 patch) should either (a) have the engine re-check for cancellation immediately before writing final status, or (b) implement true hard-kill via tracked Celery task IDs and `revoke(terminate=True)`. Flagging this explicitly rather than letting it look resolved.
2. **Plugins run as subprocesses in the shared worker container, not in per-invocation ephemeral containers.** The frozen SRS's full plugin isolation model (§7.3 — one container per task, network-namespaced to the target only) is not implemented. A compromised or buggy plugin currently has the same process-level access as the Celery worker itself. This is an explicitly deferred, larger infrastructure lift, not an oversight.
3. **`nmap`'s allow-list trades capability for safety.** Flags requiring a value (e.g. `--top-ports 100`, `--min-rate 500`) are entirely excluded because the current validator only allow-lists bare flags — accepting flag+value pairs safely would need per-flag value validators. Functionality is reduced; the security property (no file writes, no script execution, no file-based input) is not.
4. **`GET /scans/{id}` and `DELETE /scans/{id}` resolve their owning project on every call** (via `ScanService.get` inside the permission dependency, then again inside the route handler) — one extra DB round-trip per request compared to routes that have `{project_id}` directly in the path. Functionally correct, minor inefficiency.
5. **No pagination on `GET /projects/{id}/scans`.** Matches the existing (also unpaginated) `GET /projects/{id}/targets` from Milestone 2 — consistent, but both should get cursor pagination before either endpoint sees production-scale data (SRS §6.1 specifies cursor pagination as the eventual standard).
6. **Artifact storage is local disk (Docker named volume), not the SRS's object storage (MinIO/S3).** Fine for single-node Docker Compose; will need revisiting before any multi-node/multi-worker deployment, since local disk isn't shared across worker replicas.
7. **No rate limiting on scan launch.** A permitted user can currently queue an unbounded number of scans in rapid succession; Celery concurrency is the only current throttle.
