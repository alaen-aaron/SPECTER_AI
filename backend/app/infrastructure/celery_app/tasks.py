"""
Placeholder Celery tasks.

`ping` exists solely to verify the worker/beat/broker wiring during
Milestone 1 (Project Bootstrap). Real tasks — plugin execution, AI
pipeline steps, report generation — are introduced starting in Phase 2
per the frozen SRS. Do not add business logic here.
"""

from __future__ import annotations

from app.infrastructure.celery_app.app import celery_app


@celery_app.task(name="specter.ping")
def ping() -> str:
    """Trivial task used to smoke-test the Celery worker in Milestone 1."""
    return "pong"
