# SPECTER_AI

**Autonomous Offensive Security Platform** — an AI-assisted orchestration layer for
*authorized* security assessments (home labs, HTB/VulnHub/CTF, and internal
engagements with documented written permission). See `docs/SPECTER_AI_SRS.md`
for the full, frozen Software Requirements Specification.

> ⚠️ SPECTER_AI is a control plane over existing open-source security tools.
> It is not a scanner itself, and it must never be pointed at systems you are
> not explicitly authorized to test. Every scan is re-validated against
> Scope Guard at execution time, not just at launch.

## Status

**Milestones 1–6 complete** plus **M7.1 (per-plugin container isolation)** on
top of the frozen Milestone 1–2 foundation: identity + RBAC, Scan Execution
Engine & Plugin Framework, output normalization + correlation, Knowledge
Graph + graph intelligence, the production plugin ecosystem + workflow/
scheduling engine, Report Generation, and a new dedicated `executor` service
that runs every plugin inside a hardened, ephemeral container. The platform
is validated end-to-end against a real live target (OWASP Juice Shop) — see
[Live pipeline validation](#live-pipeline-validation).

Verification status (latest run, 2026-08-20):

- **Backend tests:** 621 passed, 0 failed (`cd backend && pytest`)
- **API smoke checks:** 60/60 endpoints pass with fresh bearer tokens
  (`api_smoke.ps1`)
- **E2E API verification:** 75/75 checks pass (`backend/test_e2e_api.py`)
- **Live pipeline:** worker → executor → isolated nmap container against
  Juice Shop produced deduplicated assets, a correlated finding
  (*Open port: ppp? on 172.18.0.10:3000*), and a knowledge-graph edge.
  The ephemeral container and its per-task bridge network were destroyed
  immediately after result extraction (verified: no `specter.managed`
  containers/networks remain).

Run `make verify` (or `scripts/verify.sh` / `scripts/verify.ps1` on Windows)
for a full environment/stack health report.

## What's implemented by milestone

| Milestone | Scope | Status |
|---|---|---|
| M1 | Auth (JWT + refresh rotation), orgs, RBAC, projects, targets, Scope Guard, audit log | ✅ |
| M2 | Scan service, Celery async execution, artifact storage (local + MinIO) | ✅ |
| M3 | Plugin framework, scope-guard re-validation at runtime, normalizers, finding/asset correlation + dedup | ✅ |
| M4 | Knowledge Graph (nodes/edges, blast radius, graph API), graph projector | ✅ |
| M4.5 | Graph intelligence: attack paths, impact/blast-radius, historical & executive intelligence | ✅ |
| M5 | 19 plugins, capability/metadata system, workflow templates + conditional engine, plugin API | ✅ |
| M5.5 | Workflow execution engine, schedules, AI/reporter integration, observability (metrics, health) | ✅ |
| M6 | Report generation: templates, versioning, redaction, finalize, download (Markdown) | ✅ |
| M7.1 | Per-plugin container isolation: dedicated `executor` service (owns Docker socket), hardened `specter-plugins` image, ephemeral non-root/read-only/cap-drop-ALL containers, target-only network policy in the container netns, result extraction + cleanup | ✅ |

Planned next: **M7.2+** — AI-driven autonomous planning/execution on top of the
validated scan → evidence → report workflow (advanced cross-tool correlation
remains a follow-up).

## Stack

| Layer | Technology |
|---|---|
| Backend | FastAPI, SQLAlchemy 2.0 (async), Alembic, Pydantic v2, Celery (+ beat) |
| Frontend | React, TypeScript, Vite, TailwindCSS, React Query, React Router |
| Data | PostgreSQL, Redis, MinIO |
| Isolation | Docker Engine (owned by the `executor` service only), `specter-plugins` hardened plugin image |
| Infra | Docker Compose |

## Quickstart

```bash
cp .env.example .env
make up
```

This builds and starts: `postgres`, `redis`, `minio`, `plugins-image`,
`executor`, `api`, `worker`, `beat`, and `frontend`. Once Postgres is
healthy, apply the database schema:

```bash
make migrate
```

Once running:

- Frontend: <http://localhost:5173>
- API health check: <http://localhost:9002/api/v1/health>
- API interactive docs: <http://localhost:9002/docs>
- Executor health: <http://localhost:9010/health>
- MinIO console: <http://localhost:9001> (user/pass: `specter` / `specter-secret`)

Stop everything with `make down`.

## Common commands

```bash
make up                 # start the full stack
make down               # stop and remove containers
make logs               # tail all service logs
make verify             # full environment/stack health check
make lint               # ruff + mypy (backend), eslint (frontend)
make format             # black + ruff --fix (backend), prettier (frontend)
make test               # backend pytest suite
make migrate            # apply alembic migrations
make makemigration m="msg"  # autogenerate a migration
make build-plugins      # build the hardened plugin image (specter-plugins:local)
make shell-api          # shell into the api container
make shell-executor     # shell into the executor container
make shell-db           # psql shell into postgres
```

See `Makefile` for the complete list.

## Repository layout

```
specter-ai/
├── backend/            FastAPI app (Clean Architecture: api/application/domain/infrastructure)
│   ├── executor/       M7.1: standalone service that alone owns the Docker socket;
│   │                   runs each plugin in a hardened ephemeral container
│   ├── Dockerfile.plugins  hardened plugin image (non-root uid 10001, read-only rootfs)
├── frontend/           React + Vite + TypeScript
├── infra/              docker-compose.yml
├── docs/               SRS and other design documents
└── scripts/            verify.sh / verify.ps1 / doctor.py
```

Backend internal layering follows Clean Architecture (SRS §10.1):

```
api/            → FastAPI routers, request/response schemas — no business logic
application/    → use-case services
domain/         → entities, value objects, repository interfaces — zero framework imports
infrastructure/ → SQLAlchemy repositories, Celery tasks, storage adapters, execution engine
plugins/        → plugin base class, registry, normalizers, 19 built-in plugins
```

**Critical dependency rule:** `domain/` never imports `infrastructure/`,
`api/`, or `application/`; `application/` imports only `domain/` interfaces —
never Celery, SQLAlchemy, or plugin classes directly.

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

## Plugins

Every plugin subclasses `app.plugins.base.Plugin` and self-registers onto the
process-wide `PluginRegistry` via `app.plugins.builtin`. All subprocess
invocations use list-form arguments (`shell=False`), an explicit timeout, and
allow-listed flags only.

| Category | Plugins |
|---|---|
| Reconnaissance | `subfinder`, `httpx`, `dnsx`, `naabu`, `katana` |
| Information Gathering | `whatweb`, `sslscan` |
| Enumeration | `gobuster` |
| Vulnerability | `nuclei`, `nikto`, `sqlmap`, `dalfox`, `wpscan`, `ffuf` |
| Secrets | `trufflehog`, `gitleaks` |
| Core | `echo`, `ping`, `nmap` |

Since M7.1, every plugin command is dispatched to the `executor` service
(`ExecutorHttpRunner` in the worker), which runs it in an ephemeral container
from the hardened `specter-plugins` image: user `10001:10001`, read-only
rootfs, `cap_drop=ALL`, CPU/memory limits, tmpfs for `/tmp`+`/output`, a hard
timeout, and a per-task bridge network. A target-only iptables policy is
installed inside the container's network namespace (allow loopback, DNS, and
the authorized target; drop everything else), and the container plus its
network are destroyed after result extraction. When no executor is reachable
(`EXECUTOR_ENABLED=false`), plugins fall back to direct subprocess execution.

Security properties, enforced by construction (not by catching bad inputs):

- `nmap`/`nuclei` etc. use **allow-lists** for flags — file-writing flags
  (`-oN`/`-oX`/`-oG`/`-oA`) and script execution (`--script`) are never permitted.
- Targets are validated against the same IP/CIDR/domain/URL parsers used by
  the `Target` model before any subprocess runs.
- Every subprocess has a mandatory timeout and never uses a shell string.

## Output normalization & correlation

Each scan's raw stdout is parsed by a `ToolOutputNormalizer` registered per
plugin into a structured `normalized_payload`:

| Normalizer | Plugin | Produces |
|---|---|---|
| `ping_normalizer` | ping | host reachability stats |
| `nmap_normalizer` | nmap | target, host_up, ports, counts |
| `nuclei_normalizer` | nuclei | target + `vulnerabilities[]` (template_id/title/severity) |

Correlation (`app/application/correlation_service.py`) turns normalized output
into deduplicated **assets** (host/service/technology) and **findings**
(dedup_key-based), then projects both into the **Knowledge Graph**
(asset nodes + `hosts`/`vulnerable_to` edges). Evidence can be attached to any
finding and is surfaced in generated reports.

## API surface (v1)

| Area | Endpoints |
|---|---|
| Auth | register, login, refresh, logout, logout-all, me |
| Organizations / projects / members | CRUD + RBAC |
| Targets / authorization / scope-check | in-scope enforcement |
| Scans | launch, list, get, soft-cancel |
| Findings | list, get, create, update status |
| Evidence | upload/list by finding, get, download |
| Graph | summary, nodes, edges, blast-radius, attack paths, rebuild |
| Plugins | catalog, metadata, capabilities, health |
| Reports | create, list, get, generate version, finalize, download, templates |
| Workflows / schedules | templates, run, executions, schedules (cron/once/hourly/daily/weekly) |
| Intelligence / AI | planner, analyzer, reporter, explainer, executive intelligence, metrics |

Every error maps to RFC 7807 Problem Details (`app/api/v1/error_handlers.py`).

## Live pipeline validation

Validated the full real-world workflow against OWASP Juice Shop
(`infra_default` network, current IP **172.18.0.10**), driving everything
through the API — no host-side tools. Since M7.1, every scan runs inside an
ephemeral, network-isolated plugin container managed by the `executor`:

```
scope-check → queued scan → Celery worker → executor (isolated container)
→ ToolResult persisted → deduplicated assets → correlated findings
→ knowledge-graph edge → evidence → finalized report (downloadable Markdown)
```

- **nmap / whatweb / httpx / nuclei** scans completed (`exit_code=0`); the
  M7.1 live scan `nmap -sV -Pn -p 3000 172.18.0.10` completed in ~13s and its
  plugin container was destroyed immediately after (executor logs show
  `executor_container_started` → `executor_container_removed`; no
  `specter.managed` containers or `specter-net` networks remain)
- Assets: host `172.18.0.10`, service `ppp?://172.18.0.10:3000/tcp`
- Findings: *Open port: ppp? on 172.18.0.10:3000* (INFO, nmap),
  *Prometheus Metrics - Detect* (MEDIUM, nuclei), *Open port: ppp?
  on 172.18.0.9:3000* (INFO, nmap)
- Graph: 6 nodes, 3 edges (`hosts`, `evidenced_by`) · Report: generated +
  finalized + downloaded

Genuine defects found and fixed during validation (with regression tests):

1. Graph edge projection passed raw strings instead of `GraphEdgeType` enums
   (`'str' object has no attribute 'value'`) — fixed in `asset_service.py`
   and `finding_service.py`.
2. The Python `httpx` package's console script overwrote the ProjectDiscovery
   Go binary in the Docker image — the image now re-copies the Go binary
   after `pip install`.
3. nuclei v3.3.7 removed `-json` (now `-jsonl`) — plugin + allow-list updated.
4. Missing nuclei normalizer meant nuclei findings could never materialize —
   added `nuclei_normalizer.py`.
5. M7.1 executor runtime bugs (each caught by integration tests): Docker
   commands must be argv (not a shell string); `containers.create`+`start`
   so failed starts are cleaned up; `container.reload()` before reading
   `State.Pid`; `CAP_SYS_PTRACE` required for `nsenter` into the plugin
   container netns; `exited-before-policy` fast commands; tmpfs
   `get_archive` returns empty on Docker Desktop so stdout/stderr use
   `container.logs`; `net.ipv4.ping_group_range` sysctl so `ping` works
   under `cap_drop=ALL`.

## Current live environment (key IDs)

Fresh bearer tokens (2026-08-20) — login as `e2e.alice@example.com`
(`Owner-pass-2026!`) or `alice1@example.com` (`Alice1-pass-2026!`):

| Resource | ID / value |
|---|---|
| Organization | `83fc2fdd-4b33-4233-893c-d8c1f2bf16e5` |
| Project (Juice Shop Pentest) | `a4e621ef-72c3-4f03-9922-ef843a3d19f3` |
| Owner (e2e.alice) | `b4ee117c-7c1a-46c8-a463-049fb39ac6e9` |
| Tester (alice1) | `131fa99a-1cd2-4908-b715-e047c9abae0d` |
| Target `172.18.0.10` (live Juice Shop) | `35bc1233-fc81-4396-8cf4-02fd3bfeae9b` |
| Target `172.18.0.11` (smoke) | `caa595c5-8f04-40b6-a676-d0cdc01b7351` |
| Authorization (172.18.0.10, M7.1) | `14a9e8cc-bd4e-40ec-8d37-327774f9cae2` |
| Authorization (172.18.0.4/.9) | `1de16272-5f1c-425e-a405-58da3f4775a9` |
| Live nmap scan (completed, M7.1) | `e2b3b352-e69f-4c26-9ff7-17f20bee8627` |
| Finding: Open port ppp? 172.18.0.10:3000 | `14361251-44df-4fa8-beaa-11f19f612a93` |
| Finding: Prometheus Metrics - Detect | `d87f24f0-eb17-4fdb-ae00-c33e3c5e4efc` |
| Finding: Open port 172.18.0.9:3000 | `da413caf-3bff-489e-a820-73fde7e1edc2` |
| Asset: host 172.18.0.10 | `7c045bfd-6e13-4097-9b4b-f9a695aabf2f` |
| Asset: service ppp?://172.18.0.10:3000/tcp | `c9e87983-4b1c-49e4-ae93-e1a8c54e597c` |
| Graph node (finding → asset edge) | `051e79de-81e9-476a-a928-e2b8122fc6a6` |
| Workflow: Full Port Scan Workflow | `c5d80a3c-63e5-4c18-8427-592e88dffc10` |
| Report: Juice Shop Pentest Report (final) | `11e04f2e-4d20-4f05-b136-80e1e36f0d2c` |
| Report: Live Pipeline Validation (final) | `61878abe-d04d-4b0a-8352-1dfefb02e770` |

The stale targets `172.18.0.4` / `172.18.0.9` remain registered and in-scope
but are no longer live — Juice Shop currently runs at `172.18.0.10`.

## Testing

```bash
cd backend && pytest            # 621 passed, 0 failed
cd backend && pytest tests/unit/            # unit tests
cd backend && pytest tests/integration/     # integration tests (incl. executor service, 9 checks)
cd backend && python test_e2e_api.py        # 75/75 full API verification
```

Pre-commit hooks (ruff, black, mypy, eslint, prettier) run on every clone;
install once with `pre-commit install`.

## Contributing

- Conventional Commits for commit messages.
- Run `pre-commit install` once per clone to enable local hooks.
- Keep `domain/` free of framework imports — this is a hard CI gate, not a guideline.

---

See `docs/SPECTER_AI_SRS.md` §18–19 for the full milestone/phase breakdown.
