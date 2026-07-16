"""
Aggregates all v1 API routers into one.

Milestone 1 registers only `health`. As Milestone 2+ add auth, projects,
targets, etc., each gets its own module under `api/v1/` and is included
here — this file should never contain route logic itself, only wiring.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import health

api_v1_router = APIRouter()
api_v1_router.include_router(health.router)
