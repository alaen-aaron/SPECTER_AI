"""
Metrics endpoint (Milestone 5.5 Phase 4).

Exposes in-process metrics as JSON for Prometheus/Grafana scraping
or quick debugging.  No authentication required — this endpoint is
behind the same network boundary as the health check.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.metrics import metrics

router = APIRouter(tags=["observability"])


@router.get(
    "/metrics",
    summary="In-process metrics snapshot (counters, gauges, histograms)",
)
async def get_metrics() -> JSONResponse:
    """Return a JSON snapshot of all in-process metrics."""
    snapshot = metrics.snapshot()
    return JSONResponse(content=snapshot)
