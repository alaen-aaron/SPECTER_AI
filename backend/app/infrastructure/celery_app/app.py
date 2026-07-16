"""
Celery application instance.

This is the single shared Celery app used by both the `worker` and
`beat` Compose services (SRS §12.1) — they run the same image, pointed
at this same module, differentiated only by their container command
(`celery worker` vs `celery beat`). No task logic lives here; actual
scan/plugin/AI tasks are registered starting in Phase 2 per the frozen
SRS. Milestone 1 ships one placeholder task purely to prove the broker
wiring works end-to-end.
"""

from __future__ import annotations

from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "specter_ai",
    broker=str(settings.REDIS_URL),
    backend=str(settings.REDIS_URL),
    include=["app.infrastructure.celery_app.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
)
