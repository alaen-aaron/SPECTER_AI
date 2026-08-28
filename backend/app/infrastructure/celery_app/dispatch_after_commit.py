"""Deferred Celery scan dispatch — dispatch AFTER the request commits (M7.4 Phase 3).

Milestone 3's `ScanService.create` hands a freshly-created `queued` Scan to
`task_dispatcher.dispatch(scan.id)` inside the request body. The request's
transaction (owned by `get_db_session`) only commits at the *end* of the
request, so `execute_scan_task.delay` can win the race against the commit: the
worker wakes up, calls `engine.get_scan`, finds no row yet, and logs
`scan_execution_missing_scan`. The scan is then stuck `queued` forever because
nothing retries it.

The durable fix is ordering — never dispatch before the commit — isolated to a
small infrastructure-only change with no M7.1/executor modifications:

1. `AfterCommitScanTaskDispatcher` (dispatcher.py) buffers dispatch requests
   into this module's task-local pending set instead of calling Celery.
2. `get_db_session` (db/session.py) commits the request transaction, then
   calls `drain_pending_dispatches()` — only now is Celery contacted.

Failure semantics stay durability-first: if a scan row achieved COMMIT, a
delivery failure must never roll the row back. A failed drain is logged loudly
(the scan remains `queued`, which is the pre-existing retry-less state) but the
request still succeeds.

Scope guards kept on purpose:

- The pending set is a `ContextVar`, so concurrent requests stay isolated and
  one request can never drain or clobber another's buffer.
- The set dedups by scan id: an action that was committed and dispatched once
  is never handed to Celery twice from the same request, and a ~retry request~
  does not double-deliver (post-drain the set is empty).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextvars import ContextVar
from uuid import UUID

log = logging.getLogger(__name__)

_pending_scan_ids: ContextVar[set[UUID] | None] = ContextVar(
    "specter_pending_scan_dispatches", default=None
)

_sender: Callable[[UUID], None] | None = None


def bind_sender(sender: Callable[[UUID], None]) -> None:
    """Register the process-wide delegate that actually delivers to Celery.

    Called exactly once per `AfterCommitScanTaskDispatcher` construction; the
    delegate is a plain `CeleryScanTaskDispatcher.dispatch`. The binding is
    process-global because `get_db_session` runs in every request and needs a
    stable way to flush the buffer without knowing about dependency wiring.
    """
    global _sender  # noqa: PLW0603 - intentionally a process-global delegate
    _sender = sender


def queue_dispatch(scan_id: UUID) -> None:
    """Buffer a scan id for delivery after the current request has committed."""
    pending = _pending_scan_ids.get()
    if pending is None:
        pending = set()
        _pending_scan_ids.set(pending)
    pending.add(scan_id)


def drain_pending_dispatches() -> list[UUID]:
    """Deliver every buffered scan id to Celery. Returns the delivered ids.

    Idempotent: the task-local buffer is cleared before delivery, so a
    retried/duplicate drain call cannot double-deliver. Empties to a no-op when
    nothing was buffered.
    """
    pending = _pending_scan_ids.get()
    if not pending:
        return []
    delivered = sorted(pending)
    _pending_scan_ids.set(set())

    if _sender is None:
        log.error(
            "scan_queue_dispatch_dropped sender_unbound ids=%s",
            [str(sid) for sid in delivered],
        )
        return delivered

    for scan_id in delivered:
        try:
            _sender(scan_id)
        except Exception:  # noqa: BLE001 - durability-first: never fail the request
            log.exception(
                "scan_queue_dispatch_failed scan_id=%s", str(scan_id)
            )
    return delivered