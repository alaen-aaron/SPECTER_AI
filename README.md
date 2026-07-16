# SPECTER_AI

**Autonomous Offensive Security Platform** — an AI-assisted orchestration layer for
*authorized* security assessments (home labs, HTB/VulnHub/CTF, and internal
engagements with documented written permission). See `docs/SPECTER_AI_SRS.md`
for the full, frozen Software Requirements Specification.

> ⚠️ SPECTER_AI is a control plane over existing open-source security tools.
> It is not a scanner itself, and it must never be pointed at systems you are
> not explicitly authorized to test.

## Status

**Milestone 1 — Project Bootstrap.** This is the repository skeleton only:
FastAPI app factory, async SQLAlchemy engine wiring, Celery app wiring,
structured logging, configuration system, a health endpoint, and a minimal
React shell that calls it. No auth, RBAC, database models, plugins, or AI
logic exist yet — those land in later milestones per the frozen SRS.

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
and `frontend`.

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

## Roadmap

See `docs/SPECTER_AI_SRS.md` §18–19 for the full milestone/phase breakdown.
This repository currently implements **Phase 1, Milestone 1** only.
