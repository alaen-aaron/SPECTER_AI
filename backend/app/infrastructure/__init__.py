"""
Infrastructure layer.

Bounded context: concrete adapters implementing domain-layer interfaces —
SQLAlchemy repositories, Celery task definitions, LLM provider adapters,
object storage adapters, and the Knowledge Graph projector (added in
later milestones per the frozen SRS).

This is the only layer allowed to import third-party I/O libraries
(SQLAlchemy, Celery, boto3, httpx, etc.).
"""
