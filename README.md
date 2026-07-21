# SPECTER_AI

**Autonomous Offensive Security Platform** — an AI-assisted orchestration layer for
*authorized* security assessments (home labs, HTB/VulnHub/CTF, and internal
engagements with documented written permission). See `docs/SPECTER_AI_SRS.md`
for the full, frozen Software Requirements Specification.

> ⚠️ SPECTER_AI is a control plane over existing open-source security tools.
> It is not a scanner itself, and it must never be pointed at systems you are
> not explicitly authorized to test.

## Status

**Milestone 3 — Scan Execution Engine & Plugin Framework**, built on top of
the frozen Milestone 1 bootstrap and Milestone 2 identity/platform
foundation (both unchanged except for the additive integration points
detailed in [Scan Execution (Milestone 3)](#scan-execution-milestone-3)
below). Implemented: a `Scan` aggregate with a full lifecycle (queued →
running → completed/failed/cancelled), a self-registering plugin
framework (`echo`, `ping`, `nmap` — all subprocess-based, list-args
only, no shell, mandatory timeouts), a `PluginManager` + `PluginRegistry`,
an `ExecutionEngine` that Scope-Guard-revalidates every scan immediately
before running it (not just at launch time), real Celery background
execution, and a Scan API gated by RBAC and Scope Guard with no bypass
path. The AI Planner, Knowledge Graph population, Workflow Engine, and
Reporting remain out of scope — those are later phases per the frozen SRS.

Run `make verify` (or `scripts/verify.sh` / `scripts/verify.ps1` on Windows)
for a full environment/stack health report.

## Stack

| Layer | Technology |
|---|---|
| Backend | FastAPI, SQLAlchemy 2.0 (async), Alembic, Pydantic v2, Celery |
| Frontend | React, TypeScript, Vite, TailwindCSS, React Query, React Router |
| Data | PostgreSQL, Redis, MinIO |
| Infra | Docker Compose, GitHub Actions |

## Quickstart

```bash
cp .env.example .env
make up
```

This builds and starts: `postgres`, `redis`, `minio`, `api`, `worker`, `beat`,
and `frontend`. Once Postgres is healthy, apply the database schema:

```bash
make migrate
```

Once running:

- Frontend: <http://localhost:5173>
- API health check: <http://localhost:8000/api/v1/health>
- API interactive docs: <http://localhost:8000/docs>
- MinIO console: <http://localhost:9001> (user/pass: `specter` / `specter-secret`)

Stop everything with `make down`.

## Common commands

```bash
make up               # start the full stack
make down              # stop and remove containers
make logs               # tail all service logs
make lint                # ruff + mypy (backend), eslint (frontend)
make format               # black + ruff --fix (backend), prettier (frontend)
make test                 # backend pytest suite
make shell-api             # shell into the api container
make shell-db                # psql shell into postgres
```

See `Makefile` for the complete list.

## Repository layout

```
specter-ai/
├── backend/            FastAPI app (Clean Architecture: api/application/domain/infrastructure)
├── frontend/           React + Vite + TypeScript
├── infra/              docker-compose.yml
├── .github/workflows/  CI pipeline
└── docs/               SRS and other design documents
```

Backend internal layering follows Clean Architecture (SRS §10.1):

```
api/            → FastAPI routers, request/response schemas — no business logic
application/    → use-case services
domain/         → entities, value objects, repository interfaces — zero framework imports
infrastructure/ → SQLAlchemy repositories, Celery tasks, LLM adapters, storage adapters
```

## Local development without Docker (backend)

```bash
cd backend
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

Requires a reachable Postgres/Redis — easiest to still run those two via
`docker compose -f infra/docker-compose.yml up postgres redis`.

## Local development without Docker (frontend)

```bash
cd frontend
npm install
npm run dev
```

## Contributing

- Conventional Commits for commit messages.
- Run `pre-commit install` once per clone to enable local hooks (ruff, black,
  mypy, eslint, prettier).
- All PRs go through the `CI` workflow (`.github/workflows/ci.yml`) before merge.

## Scan Execution (Milestone 3)

### Architecture

```
                     API (FastAPI router)
                            │
                            │  never executes tools directly
                            ▼
                     ScanService (application/)
                            │
              ┌─────────────┴─────────────┐
              ▼                           ▼
      ScopeGuardService            PluginManager.validate()
    (project active, auth           (fails fast on bad
     valid, target in scope)         plugin config)
              │
              ▼
      Scan row persisted (status=queued)
              │
              ▼
      ScanTaskDispatcher.dispatch(scan_id)   ◄── Protocol; application/
              │                                   never imports Celery
              ▼
      Celery task: execute_scan_task(scan_id)
              │
              ▼
      ExecutionEngine.run(scan_id)            (infrastructure/execution/)
              │
              │  re-validates Scope Guard HERE too — a scan can sit
              │  queued for a while; authorization can change in that
              │  window, so trusting the enqueue-time check alone
              │  would be a real bypass, not an optimization.
              ▼
      PluginManager.run(plugin, config, timeout)
              │
              ▼
      Plugin.execute()  →  subprocess.run([...], shell=False, timeout=N)
              │
              ▼
      PluginResult(success, stdout, stderr, exit_code, artifacts)
              │
              ▼
      LocalArtifactStore.write_logs()  +  ScanRepository.complete()/fail()
              │
              ▼
      AuditLogRepository.add()  (scan.started / scan.completed / scan.failed)
```

Every arrow above is a real dependency, not aspirational — `ScanService`
has no import of `subprocess`, `celery`, or any plugin class; `Plugin`
implementations have no import of SQLAlchemy or FastAPI.

### Plugin architecture

Every plugin is a subclass of `app.plugins.base.Plugin` with four methods:
`name()`, `description()`, `validate_config(config)`, `execute(config,
timeout_seconds)`. Plugins self-register onto a process-wide
`PluginRegistry` (`app/plugins/registry.py`) by being imported once, via
`app.plugins.builtin` — importing that module is the only thing that
needs to happen for a plugin to become available.

Built-in plugins (`app/plugins/`):

| Plugin | What it does | Config |
|---|---|---|
| `echo` | Returns a fixed string. No subprocess at all — proves the pipeline end-to-end. | none |
| `ping` | `ping -c 4 -W 2 <hostname>` | `{"hostname": "10.0.0.5"}` |
| `nmap` | `nmap [allow-listed flags] -p <ports> --host-timeout <ms> <target>` | `{"target": "...", "ports": "22,80", "arguments": ["-sV"]}` |

**Security properties, not just conventions:**
- Every subprocess call uses list-form arguments (`subprocess.run([...])`), never a shell string, and never `shell=True`.
- Every subprocess call passes an explicit `timeout`.
- `ping`'s `hostname` and `nmap`'s `target` are validated against the same IP/CIDR/domain parsers Milestone 2's Target model uses — before any subprocess is ever spawned.
- `nmap`'s `arguments` field is an **allow-list**, not a blocklist: only value-less scan-behavior flags (`-sV`, `-sC`, `-Pn`, `-T4`, ...) are permitted. File-writing flags (`-oN`/`-oX`/`-oG`/`-oA`), arbitrary script execution (`--script`), and file-based target input (`-iL`/`-iR`) are never on that list, so no combination of allowed flags can write a file or run arbitrary code — this is enforced by construction, not by trying to catch every dangerous flag individually.

**Scope note:** plugins currently run as validated subprocesses inside the
`worker` container, not yet in one ephemeral container per invocation
(the frozen SRS's full §7.3 isolation model). That remains a real,
larger follow-up — this milestone's spec asked for the plugin
interface and safe subprocess execution, not container orchestration
from the Celery worker.

### Execution lifecycle

```
queued → running → completed
                  → failed
queued           → cancelled   (soft — see below)
running          → cancelled   (soft — see below)
```

1. `POST /projects/{id}/scans` validates Scope Guard + plugin config, persists the `Scan` row as `queued`, and dispatches a Celery task. The API response returns immediately — nothing blocks on actual tool execution.
2. The Celery worker picks up the task, re-validates Scope Guard (defense in depth), flips the scan to `running`, and calls the plugin.
3. On success: `stdout`/`stderr` are written to `SCAN_ARTIFACTS_DIR/<scan_id>/{stdout,stderr}.log`, the scan becomes `completed`, and an audit entry is written.
4. On plugin failure or an unexpected exception: the scan becomes `failed` with `error_message` set — a scan is never left stuck in `running`.
5. `DELETE /scans/{id}` cancellation is **cooperative/soft** in this milestone: it flips a `queued`/`running` scan's status to `cancelled` and (if the worker hasn't started it yet) the engine skips execution entirely. It does **not** forcibly kill an already-running subprocess — that needs tracking the Celery task id and calling `revoke(terminate=True)` against a live broker, which is flagged as a near-term follow-up rather than silently left out.

### API documentation

| Method | Path | Auth | Notes |
|---|---|---|---|
| `POST` | `/api/v1/projects/{project_id}/scans` | Owner/Admin/Lead Tester/Tester, or Org Admin | Body: `{"plugin", "plugin_config", "target_ids"}` |
| `GET` | `/api/v1/projects/{project_id}/scans` | any project member | Lists all scans for the project |
| `GET` | `/api/v1/scans/{scan_id}` | any member of the scan's project | |
| `DELETE` | `/api/v1/scans/{scan_id}` | same as launch permission | Soft-cancel |

Every failure mode maps to a specific RFC 7807 `type` in the JSON body
(e.g. `.../errors/out-of-scope-target`, `.../errors/invalid-plugin-config`,
`.../errors/scan-not-found`) — see `app/api/v1/error_handlers.py` for the
full mapping.

### Examples

```bash
# Launch an nmap scan (target must already be in-scope per an active
# AuthorizationRecord, and the project must be Active)
curl -X POST http://localhost:8000/api/v1/projects/$PROJECT_ID/scans \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{
        "plugin": "nmap",
        "plugin_config": {"target": "10.10.10.5", "ports": "1-1000", "arguments": ["-sV", "-Pn"]},
        "target_ids": ["'"$TARGET_ID"'"]
      }'
# -> 201 { "id": "...", "status": "queued", ... }

curl http://localhost:8000/api/v1/scans/$SCAN_ID -H "Authorization: Bearer $TOKEN"
# -> once the worker finishes: "status": "completed", "exit_code": 0,
#    "logs_path": "/tmp/specter-artifacts/<scan_id>"
```

---



See `docs/SPECTER_AI_SRS.md` §18–19 for the full milestone/phase breakdown.
This repository currently implements **Phase 1, Milestone 1** only.
