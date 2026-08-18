"""
Lightweight in-memory metrics collector.

Thread-safe counters, gauges, and histograms with no external
dependencies (no prometheus_client, no statsd).  Metrics are
stored in plain dicts — the process-wide singleton is designed
for short-lived containers where memory pressure from counters
is negligible compared to the value of runtime visibility.

Usage::

    from app.core.metrics import metrics

    metrics.inc_counter("scans_total", tags={"plugin": "nmap"})
    metrics.observe_histogram("scan_duration_seconds", 12.3, tags={"plugin": "nmap"})
    metrics.set_gauge("active_scans", 3)

    snapshot = metrics.snapshot()  # returns a JSON-serializable dict
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Any


class MetricsCollector:
    """Thread-safe in-process metrics collector."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, dict[str, list[float]]] = defaultdict(
            lambda: defaultdict(list)
        )
        self._started_at = time.time()

    # --- Counters ---

    def inc_counter(self, name: str, value: int = 1, *, tags: dict[str, str] | None = None) -> None:
        key = self._tag_key(tags)
        with self._lock:
            self._counters[name][key] += value

    # --- Gauges ---

    def set_gauge(self, name: str, value: float, *, tags: dict[str, str] | None = None) -> None:
        key = self._tag_key(tags)
        full_name = f"{name}#{key}" if key else name
        with self._lock:
            self._gauges[full_name] = value

    def inc_gauge(self, name: str, delta: float = 1, *, tags: dict[str, str] | None = None) -> None:
        key = self._tag_key(tags)
        full_name = f"{name}#{key}" if key else name
        with self._lock:
            self._gauges[full_name] = self._gauges.get(full_name, 0.0) + delta

    # --- Histograms ---

    def observe_histogram(
        self, name: str, value: float, *, tags: dict[str, str] | None = None
    ) -> None:
        key = self._tag_key(tags)
        with self._lock:
            self._histograms[name][key].append(value)

    # --- Snapshot ---

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-serializable snapshot of all metrics."""
        with self._lock:
            uptime = time.time() - self._started_at
            result: dict[str, Any] = {
                "uptime_seconds": round(uptime, 2),
                "counters": {},
                "gauges": dict(self._gauges),
                "histograms": {},
            }
            for name, tag_map in self._counters.items():
                result["counters"][name] = dict(tag_map)
            for name, tag_map in self._histograms.items():
                hist_data: dict[str, Any] = {}
                for tag_key, values in tag_map.items():
                    sorted_vals = sorted(values)
                    count = len(sorted_vals)
                    hist_data[tag_key] = {
                        "count": count,
                        "sum": round(sum(sorted_vals), 4),
                        "min": round(sorted_vals[0], 4) if sorted_vals else 0,
                        "max": round(sorted_vals[-1], 4) if sorted_vals else 0,
                        "mean": round(sum(sorted_vals) / count, 4) if count else 0,
                        "p50": round(sorted_vals[count // 2], 4) if count else 0,
                        "p95": round(sorted_vals[int(count * 0.95)], 4) if count else 0,
                        "p99": round(sorted_vals[int(count * 0.99)], 4) if count else 0,
                    }
                result["histograms"][name] = hist_data
            return result

    def reset(self) -> None:
        """Reset all metrics (useful in tests)."""
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()
            self._started_at = time.time()

    @staticmethod
    def _tag_key(tags: dict[str, str] | None) -> str:
        if not tags:
            return ""
        return ",".join(f"{k}={v}" for k, v in sorted(tags.items()))


# Process-wide singleton.
metrics = MetricsCollector()
