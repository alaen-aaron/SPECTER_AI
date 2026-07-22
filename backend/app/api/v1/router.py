"""
Aggregates all v1 API routers into one.

Each resource gets its own module under `api/v1/routers/`; this file
only wires them together — it must never contain route logic itself.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.routers import (
    assets,
    auth,
    authorization,
    evidence,
    findings,
    graph,
    health,
    organizations,
    projects,
    reports,
    scans,
    targets,
)

api_v1_router = APIRouter()
api_v1_router.include_router(health.router)
api_v1_router.include_router(auth.router)
api_v1_router.include_router(organizations.router)
api_v1_router.include_router(projects.router)
api_v1_router.include_router(targets.router)
api_v1_router.include_router(authorization.router)
api_v1_router.include_router(scans.router)
api_v1_router.include_router(assets.router)
api_v1_router.include_router(findings.router)
api_v1_router.include_router(evidence.router)
api_v1_router.include_router(reports.router)
api_v1_router.include_router(graph.router)
