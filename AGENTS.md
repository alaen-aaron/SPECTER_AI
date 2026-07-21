# AGENTS.md

## Project

SPECTER_AI — AI-assisted offensive security orchestration platform. Milestone 3 (Scan Execution Engine & Plugin Framework). Not a scanner itself; controls existing open-source tools (nmap, ping) over subprocess.

## Commands

```bash
# Full stack (Docker Compose)
make up                # start postgres, redis, minio, api, worker, beat, frontend
make down              # stop and remove containers
make verify            # full environment/stack health check (scripts/verify.sh → scripts/doctor.py)

# Lint / format / test
make lint              # ruff + mypy (backend), eslint (frontend)
make format            # black + ruff --fix (backend), prettier (frontend)
make test              # backend pytest suite only

# Targeted
make lint-backend      # cd backend && ruff check . && mypy app
make lint-frontend     # cd frontend && npm run lint
make test-backend      # cd backend && pytest
make migrate           # apply alembic migrations (requires stack running)
make makemigration m="msg"  # autogenerate migration
```

**Local dev without Docker (backend):**
```bash
cd backend && pip install -e ".[dev]" && uvicorn app.main:app --reload
# Still needs Postgres/Redis: docker compose -f infra/docker-compose.yml up postgres redis
```

**Local dev without Docker (frontend):**
```bash
cd frontend && npm install && npm run dev
```

**Pre-commit hooks:** run `pre-commit install` once per clone. Hooks: ruff, black, mypy (backend), eslint, prettier (frontend).

## Architecture

Backend follows Clean Architecture (`backend/app/`):

| Layer | Path | Rules |
|---|---|---|
| `api/` | FastAPI routers + request/response schemas | No business logic |
| `application/` | Use-case services (ScanService, ScopeGuardService, etc.) | No framework imports from domain |
| `domain/` | Entities, value objects, repository interfaces, exceptions | **Zero framework imports** (no SQLAlchemy, no FastAPI, no Celery) |
| `infrastructure/` | SQLAlchemy repos, Celery tasks, execution engine, storage | Implements domain interfaces |
| `plugins/` | Plugin base class, registry, built-in plugins (echo, ping, nmap) | Subprocess-based, list-args only |

**Critical dependency rule:** `domain/` must never import from `infrastructure/`, `api/`, or `application/`. `application/` imports `domain/` interfaces but never Celery, SQLAlchemy, or plugin classes directly.

## Testing

```bash
cd backend && pytest          # run all tests
cd backend && pytest tests/unit/     # unit tests only
cd backend && pytest tests/integration/  # integration tests
cd backend && pytest tests/test_health.py -k test_name  # single test
```

- pytest with `asyncio_mode = "auto"` — async tests run without `@pytest.mark.asyncio`
- Fixtures in `backend/tests/conftest.py`: `client` (HTTPX async ASGI client), `_clear_settings_cache`
- `backend/tests/fakes.py` contains test doubles

## Key Gotchas

- **mypy is strict** (`strict = true` in pyproject.toml) with Pydantic plugin. Celery modules have `ignore_missing_imports = true`.
- **ruff line-length = 100**, target Python 3.12. isort configured with `known-first-party = ["app"]`.
- **Pre-commit mypy** only runs on `backend/app/` (not tests). It needs `pydantic==2.9.2` as additional dependency.
- **Scan artifacts** written to `SCAN_ARTIFACTS_DIR` (default `/tmp/specter-artifacts`), shared between api and worker containers via Docker volume.
- **Plugins use allow-list for nmap flags** (`-sV`, `-sC`, `-Pn`, `-T4` etc.), not blocklist. File-writing flags (`-oN`, `-oX`, `-oG`, `-oA`) and script execution (`--script`) are never permitted.
- **Cancellation is soft/cooperative** — flipping status, not killing subprocesses.
- **Scope Guard re-validates at execution time**, not just at scan creation — scans can queue while authorization changes.
- `docker-compose.yml` is at `infra/docker-compose.yml`, not root. All compose commands use `-f infra/docker-compose.yml`.
- `api`, `worker`, and `beat` share the same Dockerfile from `backend/`.
- Frontend runs Vite on `0.0.0.0:5173`. API on `0.0.0.0:8000`.
- **No `.github/workflows/` directory exists yet** despite README mentioning CI. Don't look for it.

## Conventions

- Conventional Commits for commit messages.
- Error responses use RFC 7807 Problem Details format. Error type mappings in `app/api/v1/error_handlers.py`.
- DB migrations via Alembic: `alembic upgrade head` to apply, `alembic revision --autogenerate -m "msg"` to create.
