"""M7.5 Phase 4-A — OutboxService + payload builder unit tests.

Pure in-memory (no Postgres): proves the application contract before the
real repository and transaction integration are exercised against PG.

Guarantees under test:
  * each terminal transition maps to exactly the right event type/value;
  * `id`, `schema_version`, `occurred_at`, and the event's refs are
    populated by the service (whitelist, not introspection);
  * payloads are explicit/whitelisted — no raw entity/ORM object is ever
    serialized, and secrets cannot appear;
  * FAILED payloads truncate the diagnostic to a bounded length;
  * `add` is the ONLY repository call (the service owns no read path).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.application.event_payloads import (
    campaign_run_started_payload,
    campaign_run_terminal_payload,
)
from app.application.outbox_service import OutboxService
from app.domain.entities import AutonomousRun, OutboxEvent
from app.domain.value_objects import AutonomousRunStatus, OutboxEventType


class FakeOutboxEventRepository:
    """Records added events only; add() is a no-op w.r.t. any transaction."""

    def __init__(self) -> None:
        self.added: list[OutboxEvent] = []

    async def add(self, event: OutboxEvent) -> None:
        self.added.append(event)


def _make_run(
    *,
    status: AutonomousRunStatus = AutonomousRunStatus.COMPLETED,
    error_message: str | None = None,
    current_cycle: int = 2,
) -> AutonomousRun:
    now = datetime.now(UTC)
    return AutonomousRun(
        id=uuid4(),
        project_id=uuid4(),
        initiated_by=uuid4(),
        status=status,
        objective="enumerate externally reachable services",
        max_actions=5,
        max_runtime_seconds=600,
        current_cycle=current_cycle,
        actions_completed=3,
        completed_at=now if status is AutonomousRunStatus.COMPLETED else None,
        error_message=error_message,
        created_at=now,
    )


@pytest.mark.asyncio
async def test_started_event_maps_fields_and_type() -> None:
    repo = FakeOutboxEventRepository()
    service = OutboxService(repo, clock=_Clock(), id_factory=_FixedId([_FIXED_ID]))
    event = await service.record_campaign_run_started(
        run_id=_RUN_ID,
        project_id=_PROJECT_ID,
        organization_id=_ORG_ID,
        schedule_id=_SCHEDULE_ID,
        objective="enumerate externally reachable services",
        max_actions=5,
        max_runtime_seconds=600,
        initiated_by=_INITIATOR,
    )

    assert event.id == _FIXED_ID
    assert event.event_type == OutboxEventType.CAMPAIGN_RUN_STARTED.value
    assert event.schema_version == 1
    assert event.occurred_at == _FIXED_NOW
    assert event.organization_id == _ORG_ID
    assert event.project_id == _PROJECT_ID
    assert event.schedule_id == _SCHEDULE_ID
    assert event.autonomous_run_id == _RUN_ID
    assert event.payload == {
        "run_id": str(_RUN_ID),
        "project_id": str(_PROJECT_ID),
        "schedule_id": str(_SCHEDULE_ID),
        "objective": "enumerate externally reachable services",
        "max_actions": 5,
        "max_runtime_seconds": 600,
        "initiated_by": str(_INITIATOR),
    }
    assert repo.added == [event]


@pytest.mark.asyncio
async def test_terminal_events_map_to_exact_event_types() -> None:
    repo = FakeOutboxEventRepository()
    service = OutboxService(repo)

    await service.record_campaign_run_completed(
        run=_make_run(status=AutonomousRunStatus.COMPLETED), organization_id=_ORG_ID
    )
    await service.record_campaign_run_failed(
        run=_make_run(
            status=AutonomousRunStatus.FAILED,
            error_message="retry budget exhausted for action",
        ),
        organization_id=_ORG_ID,
    )
    await service.record_campaign_run_cancelled(
        run=_make_run(status=AutonomousRunStatus.CANCELLED), organization_id=_ORG_ID
    )

    assert [e.event_type for e in repo.added] == [
        OutboxEventType.CAMPAIGN_RUN_COMPLETED.value,
        OutboxEventType.CAMPAIGN_RUN_FAILED.value,
        OutboxEventType.CAMPAIGN_RUN_CANCELLED.value,
    ]
    assert all(e.payload["run_id"] == str(e.autonomous_run_id) for e in repo.added)


@pytest.mark.asyncio
async def test_terminal_payload_never_carries_secrets_or_dumps() -> None:
    repo = FakeOutboxEventRepository()
    service = OutboxService(repo)
    run = _make_run(status=AutonomousRunStatus.CANCELLED)
    # A secret placed in an entity field that is NOT part of the whitelist
    # must never reach the event payload.
    run.result_summary = {"internal_credential": "super-secret-token"}

    event = await service.record_campaign_run_cancelled(run=run, organization_id=_ORG_ID)

    assert "result_summary" not in event.payload
    assert "internal_credential" not in event.payload
    joined = str(event.payload)
    assert "super-secret-token" not in joined
    # Only whitelisted keys exist.
    assert set(event.payload) == {
        "run_id",
        "project_id",
        "status",
        "current_cycle",
        "actions_completed",
        "error_message",
    }


@pytest.mark.asyncio
async def test_failed_payload_truncates_diagnostic() -> None:
    long_error = "x" * 5000
    repo = FakeOutboxEventRepository()
    service = OutboxService(repo)
    run = _make_run(status=AutonomousRunStatus.FAILED, error_message=long_error)

    event = await service.record_campaign_run_failed(run=run, organization_id=_ORG_ID)

    assert event.payload["error_message"] == "x" * 500
    assert event.payload["status"] == AutonomousRunStatus.FAILED.value


@pytest.mark.asyncio
async def test_service_never_calls_read_or_commit() -> None:
    repo = FakeOutboxEventRepository()
    service = OutboxService(repo)
    event = await service.record_campaign_run_started(
        run_id=_RUN_ID,
        project_id=_PROJECT_ID,
        organization_id=_ORG_ID,
        schedule_id=_SCHEDULE_ID,
        objective="o",
        max_actions=1,
        max_runtime_seconds=60,
        initiated_by=_INITIATOR,
    )
    # OutboxService must only ever append — the caller commits. The fake
    # exposes exactly one call path: add(event).
    assert repo.added == [event]


def test_payload_builders_are_explicit_whitelists() -> None:
    started = campaign_run_started_payload(
        run_id=_RUN_ID,
        project_id=_PROJECT_ID,
        schedule_id=_SCHEDULE_ID,
        objective="o",
        max_actions=3,
        max_runtime_seconds=120,
        initiated_by=_INITIATOR,
    )
    assert set(started) == {
        "run_id",
        "project_id",
        "schedule_id",
        "objective",
        "max_actions",
        "max_runtime_seconds",
        "initiated_by",
    }

    terminal = campaign_run_terminal_payload(
        run_id=_RUN_ID,
        project_id=_PROJECT_ID,
        status=AutonomousRunStatus.FAILED.value,
        current_cycle=1,
        actions_completed=0,
        error_message="boom",
    )
    assert set(terminal) == {
        "run_id",
        "project_id",
        "status",
        "current_cycle",
        "actions_completed",
        "error_message",
    }
    assert terminal["error_message"] == "boom"
    assert campaign_run_terminal_payload(
        run_id=_RUN_ID,
        project_id=_PROJECT_ID,
        status=AutonomousRunStatus.COMPLETED.value,
        current_cycle=1,
        actions_completed=0,
    )["error_message"] is None


class _Clock:
    def utcnow(self) -> datetime:
        return _FIXED_NOW


class _FixedId:
    def __init__(self, values: list[UUID]) -> None:
        self._gen = iter(values)

    def __call__(self) -> UUID:
        return next(self._gen)


_FIXED_NOW = datetime(2026, 9, 18, 12, 0, 0, tzinfo=UTC)
_FIXED_ID = uuid4()
_RUN_ID = uuid4()
_PROJECT_ID = uuid4()
_ORG_ID = uuid4()
_SCHEDULE_ID = uuid4()
_INITIATOR = uuid4()