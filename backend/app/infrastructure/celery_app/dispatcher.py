"""
Concrete `ScanTaskDispatcher` and `WorkflowTaskDispatcher` (Milestone 3 / Phase 2/3).

The only modules in `application/` or `api/` allowed to know Celery
exists are these, on the other side of the Protocol boundary —
see `app/application/scan_service.py` and `app/application/workflow_service.py`.
"""

from __future__ import annotations

from uuid import UUID

from app.infrastructure.celery_app.dispatch_after_commit import (
    bind_sender,
    queue_dispatch,
)
from app.infrastructure.celery_app.tasks import execute_scan_task, execute_workflow_task


class CeleryScanTaskDispatcher:
    """Satisfies `app.application.scan_service.ScanTaskDispatcher` structurally."""

    def dispatch(self, scan_id: UUID) -> None:
        execute_scan_task.delay(str(scan_id))


class AfterCommitScanTaskDispatcher:
    """Defers Celery dispatch until the request transaction has committed.

    M7.4 Phase 3: `dispatch()` only buffers the scan id (task-local); the
    request-scoped `get_db_session` dependency calls
    `dispatch_after_commit.drain_pending_dispatches()` after commit, so the
    worker can never observe a scan row that does not exist yet. Satisfies the
    same `ScanTaskDispatcher` Protocol as `CeleryScanTaskDispatcher`.
    """

    def __init__(self, inner: CeleryScanTaskDispatcher) -> None:
        self._inner = inner
        bind_sender(inner.dispatch)

    def dispatch(self, scan_id: UUID) -> None:
        queue_dispatch(scan_id)


class CeleryWorkflowTaskDispatcher:
    """Satisfies `app.application.workflow_service.WorkflowTaskDispatcher` structurally."""

    def dispatch_workflow(self, execution_id: UUID) -> None:
        execute_workflow_task.delay(str(execution_id))
