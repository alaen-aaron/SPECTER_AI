"""
Request observability middleware.

Records per-request metrics (count, latency, status code) into the
in-process MetricsCollector and emits a structured log line on every
request completion.  Zero external dependencies — works with any
ASGI server.
"""

from __future__ import annotations

import time
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.metrics import metrics


class ObservabilityMiddleware(BaseHTTPMiddleware):
    """ASGI middleware that records request metrics and structured logs."""

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        method = request.method
        path = request.url.path

        metrics.inc_counter("http_requests_total", tags={"method": method})
        metrics.inc_gauge("http_requests_inflight", 1)

        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            metrics.inc_counter("http_requests_total", tags={"method": method, "status": "5xx"})
            metrics.inc_gauge("http_requests_inflight", -1)
            raise
        finally:
            duration = time.perf_counter() - start

        status = str(response.status_code)
        bucket = f"{status[0]}xx"

        metrics.inc_counter("http_requests_total", tags={"method": method, "status": bucket})
        metrics.observe_histogram(
            "http_request_duration_seconds", duration, tags={"path": path}
        )
        current = metrics._gauges.get("http_requests_inflight", 1)  # type: ignore[attr-defined]
        metrics.set_gauge("http_requests_inflight", max(0, current - 1))

        return response
