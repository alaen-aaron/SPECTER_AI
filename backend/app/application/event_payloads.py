"""Explicit, whitelisted outbox payload builders (M7.5 Phase 4-A).

Every event's ``payload`` is produced here — never by serializing an ORM
or domain object (``asdict``/``model_dump`` of a full entity is forbidden:
entities carry credentials, plugin configs, and internal wiring that must
never leave the platform). Each builder lists its keys explicitly so a
reviewer can see at a glance exactly what an external consumer may one
day receive.

Rule: only stable ids and concise, non-sensitive lifecycle metrics. Never
bearer tokens, passwords, API keys, webhook secrets, plugin configs,
raw auth headers, or infrastructure internals.
"""

from __future__ import annotations

from uuid import UUID


def campaign_run_started_payload(
    *,
    run_id: UUID,
    project_id: UUID,
    schedule_id: UUID | None,
    objective: str,
    max_actions: int,
    max_runtime_seconds: int,
    initiated_by: UUID | None,
) -> dict[str, object]:
    """Payload for ``campaign.run.started`` (fired by a schedule)."""
    return {
        "run_id": str(run_id),
        "project_id": str(project_id),
        "schedule_id": str(schedule_id) if schedule_id is not None else None,
        "objective": objective,
        "max_actions": max_actions,
        "max_runtime_seconds": max_runtime_seconds,
        "initiated_by": str(initiated_by) if initiated_by is not None else None,
    }


def campaign_run_terminal_payload(
    *,
    run_id: UUID,
    project_id: UUID,
    status: str,
    current_cycle: int,
    actions_completed: int,
    error_message: str | None = None,
) -> dict[str, object]:
    """Payload for ``campaign.run.completed`` / ``.failed`` / ``.cancelled``.

    ``error_message`` (FAILED only) is truncated to a bounded length —
    a diagnostic snippet, never a full exception traceback or log dump.
    """
    return {
        "run_id": str(run_id),
        "project_id": str(project_id),
        "status": status,
        "current_cycle": current_cycle,
        "actions_completed": actions_completed,
        "error_message": _truncate(error_message),
    }


def _truncate(value: str | None, limit: int = 500) -> str | None:
    if value is None:
        return None
    return value if len(value) <= limit else value[:limit]