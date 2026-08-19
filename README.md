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

**Milestones 1–6 complete** on top of the frozen Milestone 1–2 foundation:
identity + RBAC, Scan Execution Engine & Plugin Framework, output
normalization + correlation, Knowledge Graph + graph intelligence, the
production plugin ecosystem + workflow/scheduling engine, and Report
Generation. The platform is validated end-to-end against a real live target
(OWASP Juice Shop) — see [Live pipeline validation](#live-pipeline-validation).

Verification status:

- **Backend tests:** 585 passed, 12 skipped (`cd backend && pytest`)
- **E2E API verification:** 75/75 checks pass (`backend/test_e2e_api.py`)
- **Live pipeline:** nmap → whatweb → httpx → nuclei against Juice Shop
  produced deduplicated assets, correlated findings, a knowledge-graph edge,
  and a finalized, downloadable report

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

Planned next: **M7** — autonomous orchestration on top of the validated
scan → evidence → report workflow (per-plugin container isolation, AI-driven
planning/execution, and advanced cross-tool correlation remain follow-ups).

## Stack

| Layer | Technology |
|---|---|
| Backend | FastAPI, SQLAlchemy 2.0 (async), Alembic, Pydantic v2, Celery (+ beat) |
| Frontend | React, TypeScript, Vite, TailwindCSS, React Query, React Router |
| Data | PostgreSQL, Redis, MinIO |
| Infra | Docker Compose |

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
- API health check: <http://localhost:9002/api/v1/health>
- API interactive docs: <http://localhost:9002/docs>
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
make shell-api          # shell into the api container
make shell-db           # psql shell into postgres
```

See `Makefile` for the complete list.

## Repository layout

```
specter-ai/
├── backend/            FastAPI app (Clean Architecture: api/application/domain/infrastructure)
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
(`infra_default` network), driving everything through the API — no host-side
tools:

```
scope-check → queued scan → Celery worker → real binary → normalizer
→ ToolResult persisted → deduplicated assets → correlated findings
→ knowledge-graph edge → evidence → finalized report (downloadable Markdown)
```

- **nmap / whatweb / httpx / nuclei** scans all completed (`exit_code=0`)
- Assets: host `172.18.0.9`, service `ppp?://172.18.0.9:3000/tcp`
- Findings: *Prometheus Metrics - Detect* (MEDIUM, nuclei), *Open port* (INFO, nmap)
- Graph: 3 nodes, 1 `hosts` edge · Report: generated + finalized + downloaded

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

## Testing

```bash
cd backend && pytest            # 585 passed, 12 skipped
cd backend && pytest tests/unit/            # unit tests
cd backend && pytest tests/integration/     # integration tests
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
