"""
Concrete `ScanTaskDispatcher` (Milestone 3).

The only module in `application/` or `api/` allowed to know Celery
exists is this one, on the other side of the `ScanTaskDispatcher`
Protocol boundary — see `app/application/scan_service.py`.
"""

from __future__ import annotations

from uuid import UUID

from app.infrastructure.celery_app.tasks import execute_scan_task


class CeleryScanTaskDispatcher:
    """Satisfies `app.application.scan_service.ScanTaskDispatcher` structurally."""

    def dispatch(self, scan_id: UUID) -> None:
        execute_scan_task.delay(str(scan_id))
