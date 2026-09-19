"""Transactional outbox writer (M7.5 Phase 4-A).

Records durable lifecycle events WITHOUT owning the transaction: the
caller commits, so the event and the domain state change commit (or
roll back) atomically. This service never calls ``session.commit()``,
never dispatches Celery, never reads state, and never delivers anything
— it only appends a whitelisted, versioned event to the outbox.

Phase 4-A emits exactly the four approved ``campaign.run.*`` events at
the transition boundaries that already own their transactions:
fire (started in the same commit as the run's creation), advance
(completed/failed), and the cancel endpoint (cancelled). No speculative
event types.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.event_payloads import (
    campaign_run_started_payload,
    campaign_run_terminal_payload,
)
from app.domain.entities import AutonomousRun, OutboxEvent
from app.domain.repositories import OutboxEventRepository
from app.domain.value_objects import OutboxEventType

DEFAULT_OUTBOX_SCHEMA_VERSION = 1


class _Clock:
    def utcnow(self) -> datetime:
        return datetime.now(UTC)


class OutboxService:
    """Use-case service that appends event_outbox rows in the caller's txn."""

    def __init__(
        self,
        repository: OutboxEventRepository,
        *,
        clock: _Clock | None = None,
        id_factory: Callable[[], UUID] | None = None,
        schema_version: int = DEFAULT_OUTBOX_SCHEMA_VERSION,
    ) -> None:
        self._repository = repository
        self._clock = clock or _Clock()
        self._id = id_factory or uuid4
        self._schema_version = schema_version

    async def record_campaign_run_started(
        self,
        *,
        run_id: UUID,
        project_id: UUID,
        organization_id: UUID | None,
        schedule_id: UUID | None,
        objective: str,
        max_actions: int,
        max_runtime_seconds: int,
        initiated_by: UUID | None,
    ) -> OutboxEvent:
        payload = campaign_run_started_payload(
            run_id=run_id,
            project_id=project_id,
            schedule_id=schedule_id,
            objective=objective,
            max_actions=max_actions,
            max_runtime_seconds=max_runtime_seconds,
            initiated_by=initiated_by,
        )
        return await self._record(
            event_type=OutboxEventType.CAMPAIGN_RUN_STARTED,
            organization_id=organization_id,
            project_id=project_id,
            schedule_id=schedule_id,
            autonomous_run_id=run_id,
            payload=payload,
        )

    async def record_campaign_run_completed(
        self,
        *,
        run: AutonomousRun,
        organization_id: UUID | None,
        schedule_id: UUID | None = None,
    ) -> OutboxEvent:
        payload = campaign_run_terminal_payload(
            run_id=run.id,
            project_id=run.project_id,
            status=run.status.value,
            current_cycle=run.current_cycle,
            actions_completed=run.actions_completed,
        )
        return await self._record(
            event_type=OutboxEventType.CAMPAIGN_RUN_COMPLETED,
            organization_id=organization_id,
            project_id=run.project_id,
            schedule_id=schedule_id,
            autonomous_run_id=run.id,
            payload=payload,
        )

    async def record_campaign_run_failed(
        self,
        *,
        run: AutonomousRun,
        organization_id: UUID | None,
        schedule_id: UUID | None = None,
    ) -> OutboxEvent:
        payload = campaign_run_terminal_payload(
            run_id=run.id,
            project_id=run.project_id,
            status=run.status.value,
            current_cycle=run.current_cycle,
            actions_completed=run.actions_completed,
            error_message=run.error_message,
        )
        return await self._record(
            event_type=OutboxEventType.CAMPAIGN_RUN_FAILED,
            organization_id=organization_id,
            project_id=run.project_id,
            schedule_id=schedule_id,
            autonomous_run_id=run.id,
            payload=payload,
        )

    async def record_campaign_run_cancelled(
        self,
        *,
        run: AutonomousRun,
        organization_id: UUID | None,
        schedule_id: UUID | None = None,
    ) -> OutboxEvent:
        payload = campaign_run_terminal_payload(
            run_id=run.id,
            project_id=run.project_id,
            status=run.status.value,
            current_cycle=run.current_cycle,
            actions_completed=run.actions_completed,
        )
        return await self._record(
            event_type=OutboxEventType.CAMPAIGN_RUN_CANCELLED,
            organization_id=organization_id,
            project_id=run.project_id,
            schedule_id=schedule_id,
            autonomous_run_id=run.id,
            payload=payload,
        )

    async def _record(
        self,
        *,
        event_type: OutboxEventType,
        organization_id: UUID | None,
        project_id: UUID | None,
        schedule_id: UUID | None,
        autonomous_run_id: UUID | None,
        payload: dict[str, object],
    ) -> OutboxEvent:
        event = OutboxEvent(
            id=self._id(),
            event_type=event_type.value,
            schema_version=self._schema_version,
            occurred_at=self._clock.utcnow(),
            payload=payload,
            organization_id=organization_id,
            project_id=project_id,
            schedule_id=schedule_id,
            autonomous_run_id=autonomous_run_id,
        )
        await self._repository.add(event)
        return event