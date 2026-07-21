# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps needed for asyncpg/psycopg build + healthcheck curl, plus
# the tools the Milestone 3 built-in plugins actually invoke via
# subprocess (ping, nmap). Installing these in the shared api/worker/beat
# image is a deliberate Milestone 3 scope choice — see the plugin
# subsystem's module docstring for why full per-plugin container
# isolation (SRS §7.3) is a later hardening step, not this milestone's.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        iputils-ping \
        nmap \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic
COPY tests ./tests

# Install the project itself plus dev extras (dev extras are harmless in
# the runtime image for this stage; a slimmer prod-only stage is a
# Phase 6 deployment-hardening concern per the SRS roadmap).
RUN pip install --upgrade pip && pip install -e ".[dev]"

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
