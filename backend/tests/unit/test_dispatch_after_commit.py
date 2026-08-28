"""
§18 race regression — dispatch-after-commit buffering.

`ScanService.create` used to hand the scan id to Celery (via
`CeleryScanTaskDispatcher.dispatch`) during the *request transaction*,
then commit the row. A worker poll could run the task before the
WAL commit is visible, immediately failing with
`scan_execution_missing_scan` and leaving the scan stuck `queued`.
`AfterCommitScanTaskDispatcher` buffers dispatches task-local and
releases them only in `get_db_session` *after* `session.commit()` has
returned (durability-first: a drain failure is logged, never rolled
back).
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.infrastructure.celery_app.dispatch_after_commit import (
    _pending_scan_ids,
    bind_sender,
    drain_pending_dispatches,
    queue_dispatch,
)


@pytest.fixture(autouse=True)
def _fresh_context():
    """Guarantee a clean ContextVar + sender state per test."""
    from app.infrastructure.celery_app import dispatch_after_commit as dac

    dac._pending_scan_ids.set(None)
    dac._sender = None
    yield
    dac._pending_scan_ids.set(None)
    dac._sender = None


def test_a_queue_without_sender_buffers_until_drain():
    scan_id = uuid4()
    queue_dispatch(scan_id)
    # Buffered regardless of sender binding (the "drop" happens at drain,
    # where a missing sender is logged and never fails the request).
    assert _pending_scan_ids.get() == {scan_id}


def test_b_sender_requeue_drains_in_sorted_order():
    calls: list[str] = []

    def send(scan_id: object) -> None:
        calls.append(str(scan_id))

    bind_sender(send)
    first, second = uuid4(), uuid4()
    queue_dispatch(first)
    queue_dispatch(second)

    delivered = drain_pending_dispatches()

    expect = sorted((str(first), str(second)))
    assert calls == expect
    assert [str(x) for x in delivered] == expect
    assert _pending_scan_ids.get() in (None, set())  # drained


def test_c_duplicate_scan_ids_are_buffered_once():
    calls: list[str] = []

    def send(scan_id: object) -> None:
        calls.append(str(scan_id))

    bind_sender(send)
    scan_id = uuid4()
    queue_dispatch(scan_id)
    queue_dispatch(scan_id)  # idempotent within one request

    drain_pending_dispatches()

    assert calls == [str(scan_id)]


def test_d_drain_after_rebind_uses_latest_sender():
    calls: list[str] = []
    bind_sender(lambda s: calls.append(str(s)))  # type: ignore[arg-type]
    scan_id = uuid4()
    queue_dispatch(scan_id)
    bind_sender(lambda s: calls.append(f"second:{s}"))  # type: ignore[arg-type]

    drain_pending_dispatches()

    assert calls == [f"second:{scan_id}"]


def test_e_rollback_path_never_drains():
    """A failed/rolled-back request must never dispatch queued tasks."""
    calls: list[str] = []
    bind_sender(lambda s: calls.append(str(s)))  # type: ignore[arg-type]
    scan_id = uuid4()
    queue_dispatch(scan_id)

    # Simulate a rollback: we intentionally do NOT call drain (the session
    # layer drains only after a successful commit), then the context resets.
    assert _pending_scan_ids.get() == {scan_id}
    assert calls == []