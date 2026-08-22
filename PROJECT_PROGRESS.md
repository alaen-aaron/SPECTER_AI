# SPECTER_AI — Project Progress

## Current Status: M7.1 COMPLETE

**Last Updated:** 2026-08-20
**Test Suite:** 621 passed, 0 failed, 0 skipped · API smoke 60/60 · E2E `test_e2e_api.py` 75/75
**Architecture:** Clean Architecture (domain → application → infrastructure → API)
**Live target:** OWASP Juice Shop @ `172.18.0.10` (`infra_default` network)

---

## Milestones Completed

### M1: Core Foundation — COMPLETE
- Auth system (JWT, refresh tokens, logout)
- Multi-tenant organizations with RBAC
- Projects (Draft → Active → Archived lifecycle)
- Targets (IP, CIDR, Domain, URL)
- Scope Guard (temporal authorization records)
- Audit logging (immutable append-only)
- PostgreSQL + SQLAlchemy async + Alembic migrations
- FastAPI with RFC 7807 error responses

### M2: Scan Execution Foundation — COMPLETE
- Scan service (CRUD + cancel)
- Plugin manager + registry
- Scope Guard re-validation at execution time
- Celery async task execution
- Artifact storage (local + S3/MinIO)
- Scan API endpoints

### M3: Plugin Framework & Output Correlation — COMPLETE
- Plugin ABC with capability/metadata declarations
- 3 built-in plugins (echo, ping, nmap)
- Security model (allow-listed flags, no file-writing)
- ToolOutputNormalizer framework
- Nmap + Ping normalizers
- Finding/Asset correlation from normalized output
- Deduplication via dedup_key

### M4: Knowledge Graph Foundation — COMPLETE
- Graph entities (GraphNode, GraphEdge)
- GraphProjector (idempotent, transactional, deterministic)
- 8 edge types (hosts, runs, exposes, vulnerable_to, etc.)
- GraphRepository with recursive CTE blast_radius BFS
- Graph API (summary, blast-radius, rebuild)

### M4.5: Knowledge Graph Intelligence — COMPLETE
- Attack Path Service (shortest path, multiple paths, crown jewel)
- Impact Analysis Service (blast radius, risk classification)
- Historical Intelligence (asset delta, finding trends, recurring)
- Executive Intelligence (highest-risk, most-connected, attack chains)
- Graph-enriched Planner, Analyzer, Reporter, Explainer

### M5: Production Plugin Ecosystem & Autonomous Workflow Engine — COMPLETE
- **19 plugins** (3 original + 16 production)
  - Reconnaissance: subfinder, httpx, dnsx, naabu, katana
  - Information Gathering: whatweb, sslscan
  - Enumeration: gobuster
  - Vulnerability: nuclei, nikto, sqlmap, dalfox, wpscan, ffuf
  - Secrets: trufflehog, gitleaks
- **Plugin Capability System** (input/output types, compatibility)
- **Plugin Metadata** (version, author, category, tags, health checks)
- **Enhanced Registry** (category/tag filtering, compatibility chains)
- **Workflow Template System** (DAG, conditions, variable substitution)
- **5 Built-in Templates** (full_port_scan, web_app_scan, etc.)
- **Conditional Execution Engine** (declarative conditions)
- **Advanced Workflow Engine** (parallel, retries, resume, cancellation)
- **AI Workflow Integration** (template recommendations)
- **Plugin API** (12 endpoints)
- **Route Precedence Fix** (static routes before parameterized)

### M5.5: Workflow Execution, Schedules & Observability — COMPLETE
- Workflow execution engine wired into Celery (`execute_workflow` task)
- Schedules API: cron/once/hourly/daily/weekly + pause/resume/delete
- AI/reporter integration points for workflows
- Observability: `/metrics`, `/health`, structured scan-lifecycle logs

### M6: Report Generation — COMPLETE
- Report templates (`pentest_report`, `vulnerability_assessment`, `recon_summary`)
- Versioned generation + diffing between versions
- Redaction of sensitive data
- Finalize workflow (draft → final) + Markdown download
- Evidence surfaced inside generated reports

### M7.1: Per-Plugin Container Isolation (Executor Service) — COMPLETE
- New standalone `executor` service (`backend/executor/`) — **the only component
  with access to `docker.sock`**
- Worker dispatches validated plugin commands over HTTP
  (`ExecutorHttpRunner`, injectable transport) instead of running subprocesses
- Hardened `specter-plugins` image (`backend/Dockerfile.plugins`): non-root
  uid 10001, all tool binaries baked in
- Ephemeral per-task containers: read-only rootfs, `cap_drop=ALL`,
  CPU/memory limits, tmpfs `/tmp`+`/output`, hard timeout, destroyed after
  result extraction (container + per-task bridge network)
- Target-only network policy installed via `nsenter` + iptables inside the
  container netns (allow lo/established/embedded-DNS/targets, drop rest),
  fail-closed on policy errors
- `CommandRunner` protocol + contextvar in plugin base; all 17 subprocess
  plugins unchanged, automatic fallback to direct subprocess when
  `EXECUTOR_ENABLED=false`
- Compose: `plugins-image` + `executor` services (executor needs
  pid:host, NET_ADMIN/NET_RAW/SYS_ADMIN/SYS_PTRACE, seccomp:unconfined)
- Tests: 20 unit + 9 executor integration tests; live scan validated
  end-to-end (finding created from isolated nmap run)

---

## What's Left (Future Milestones)

### M7.2+: Autonomous Orchestration — NOT STARTED
- AI-driven planning → approval → execution loops on top of validated workflow
- Advanced cross-tool correlation

### M8: Advanced AI Features — PARTIAL
- Prompt template library (CRUD) ✅ (M4.5/M5)
- Context memory (conversation persistence) ✅
- Risk scoring engine ✅ (heuristic) — ML-based scoring outstanding
- Natural language scan queries — NOT STARTED

### M9: Container Isolation — DONE via M7.1 ✅
- Per-plugin Docker containers ✅ (executor service)
- Resource limits (CPU, memory, network) ✅
- Container lifecycle management ✅

### M10: CI/CD & Deployment — NOT STARTED
- GitHub Actions workflows (no `.github/workflows/` yet despite README mention)
- Docker Compose production config
- Environment variable management
- Deployment scripts

### M11: Frontend UI — NOT STARTED
- React dashboard
- Real-time scan monitoring
- Plugin marketplace UI
- Report viewer/editor

### M12: Advanced Correlation — PARTIAL
- Cross-tool correlation (basic) ✅
- Temporal analysis — partial (historical intelligence)
- ML-based deduplication — NOT STARTED
- Threat intelligence enrichment — NOT STARTED

---

## Key Files Reference

| Area | Path |
|------|------|
| Domain entities | `backend/app/domain/entities.py` |
| Value objects | `backend/app/domain/value_objects.py` |
| Plugin system | `backend/app/plugins/` (19 plugins) |
| Runner abstraction | `backend/app/plugins/base.py` (`CommandRunner`, contextvar) |
| Executor HTTP runner | `backend/app/infrastructure/execution/executor_runner.py` |
| **Executor service** | `backend/executor/app/` (main, config, models, network_policy, container_runner) |
| Plugin image | `backend/Dockerfile.plugins` |
| Workflow engine | `backend/app/domain/workflow_engine.py` |
| Workflow templates | `backend/app/domain/builtin_templates.py` |
| Graph projector | `backend/app/infrastructure/graph/` |
| Intelligence services | `backend/app/application/` |
| API routes | `backend/app/api/v1/routers/` |
| Unit tests | `backend/tests/unit/` |
| Integration tests | `backend/tests/integration/` (incl. `test_executor_service_integration.py`) |
| E2E verification | `backend/test_e2e_api.py` (75 checks) |
| Migrations | `backend/alembic/versions/` |
| Docker config | `infra/docker-compose.yml` |

---

## Commands

```bash
# Full stack
make up                # start postgres, redis, minio, plugins-image, executor, api, worker, beat, frontend
make down              # stop and remove containers

# Images
make build-plugins     # build hardened plugin image (specter-plugins:local)

# Lint / test
make lint              # ruff + mypy + eslint
make test              # pytest (621 tests)
make format            # auto-fix formatting

# Database
make migrate           # apply alembic migrations
make makemigration m="msg"  # create new migration

# Shells
make shell-api         # api container
make shell-executor    # executor container
make shell-db          # psql
```

Executor env knobs (`.env`): `EXECUTOR_ENABLED=true`,
`EXECUTOR_URL=http://executor:8000`, `EXECUTOR_IMAGE=specter-plugins:local`,
`EXECUTOR_CPU_LIMIT=1.0`, `EXECUTOR_MEMORY_LIMIT=512m`.

---

## Live Validation Snapshot (2026-08-20)

| Resource | ID |
|---|---|
| Org / Project | `83fc2fdd-…` / `a4e621ef-72c3-4f03-9922-ef843a3d19f3` |
| Target `172.18.0.10` | `35bc1233-fc81-4396-8cf4-02fd3bfeae9b` |
| M7.1 authz record | `14a9e8cc-bd4e-40ec-8d37-327774f9cae2` |
| Live nmap scan (completed) | `e2b3b352-e69f-4c26-9ff7-17f20bee8627` |
| Finding "Open port ppp? 172.18.0.10:3000" | `14361251-44df-4fa8-beaa-11f19f612a93` |
| Assets host/service `.10` | `7c045bfd-…` / `c9e87983-…` |

Users: `e2e.alice@example.com` / `Owner-pass-2026!` (owner),
`alice1@example.com` / `Alice1-pass-2026!` (tester).
Stale targets `172.18.0.4`/`172.18.0.9` remain registered but are down.

---

## Known Issues

1. **Database Data Loss Risk**: Running `docker compose down -v` wipes all data. Always use `docker compose down` (without `-v`) to preserve data.
2. **Plugin Health Check**: Some plugins require external binaries (subfinder, nuclei, etc.). The hardened image bakes them in; health check reports unhealthy where a binary is missing.
3. **nmap host-timeout**: the plugin layers `--host-timeout = timeout-1`; wide scans (`1-1000` + `-sV`) can exceed the default 120s and get skipped — use `-T4`/`-F` or narrower port sets.
4. **Graph Projection**: Manual trigger via `POST /graph/rebuild`. Not yet auto-triggered from workflow completion.
5. **Executor privileges**: the executor container requires pid:host +
   SYS_PTRACE/NET_ADMIN/SYS_PTRACE + seccomp:unconfined to install iptables
   rules in plugin netns (verified empirically on Docker Desktop).
6. **Fast-exit commands**: commands that finish before the policy installs are
   logged as `exited-before-policy` and results collected without enforcement.

---

## Architecture Rules

- **Domain layer**: Zero framework imports (no SQLAlchemy, FastAPI, Celery)
- **Application layer**: Imports domain interfaces only
- **Infrastructure layer**: Implements domain interfaces
- **API layer**: Request/response schemas, no business logic
- **Plugins**: Subprocess/list-args based, allow-listed flags only, no shell
  execution; dispatched to the executor when enabled
- **Isolation**: Only the executor service touches docker.sock; plugin
  containers are non-root, read-only, cap-drop-ALL, target-only networking
- **SRS 8.4**: LLM output never auto-executed — requires human approval
