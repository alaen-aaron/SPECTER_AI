"""
Context Memory service (SRS §8).

Per-project retrieval store for AI context continuity across multi-week
engagements. Stores prior findings, report text, and project state
summaries.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.domain.entities import AIContextMemory
from app.domain.repositories import AIContextMemoryRepository


class ContextMemoryService:
    """
    Manages per-project context memory for AI continuity.

    Per SRS §8: a per-project retrieval store holding prior findings/
    report text so the AI has continuity across a multi-week engagement
    without re-sending the whole history each call.
    """

    def __init__(self, memory_repo: AIContextMemoryRepository) -> None:
        self._repo = memory_repo

    async def add_memory(
        self,
        project_id: UUID,
        memory_type: str,
        content: str,
        metadata: dict[str, object] | None = None,
    ) -> AIContextMemory:
        """Store a new context memory entry."""
        memory = AIContextMemory(
            id=uuid4(),
            project_id=project_id,
            memory_type=memory_type,
            content=content,
            metadata=metadata or {},
            created_at=datetime.now(UTC),
        )
        await self._repo.add(memory)
        return memory

    async def list_for_project(self, project_id: UUID) -> list[AIContextMemory]:
        """List all context memories for a project."""
        return await self._repo.list_for_project(project_id)

    async def list_by_type(
        self, project_id: UUID, memory_type: str
    ) -> list[AIContextMemory]:
        """List context memories filtered by type."""
        return await self._repo.list_for_project_by_type(project_id, memory_type)

    async def delete(self, memory_id: UUID) -> None:
        """Delete a context memory entry."""
        await self._repo.delete(memory_id)
