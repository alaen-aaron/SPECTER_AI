"""Unit tests for ContextMemoryService (Phase 4, SRS §8)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.application.context_memory_service import ContextMemoryService
from tests.fakes import FakeAIContextMemoryRepository


@pytest.mark.asyncio
async def test_add_memory():
    project_id = uuid4()
    repo = FakeAIContextMemoryRepository()
    svc = ContextMemoryService(memory_repo=repo)

    memory = await svc.add_memory(
        project_id,
        memory_type="finding_summary",
        content="SQL injection found on /login",
    )
    assert memory.project_id == project_id
    assert memory.memory_type == "finding_summary"
    assert memory.content == "SQL injection found on /login"
    assert memory.id is not None
    assert memory.created_at is not None


@pytest.mark.asyncio
async def test_add_memory_with_metadata():
    project_id = uuid4()
    repo = FakeAIContextMemoryRepository()
    svc = ContextMemoryService(memory_repo=repo)

    memory = await svc.add_memory(
        project_id,
        memory_type="report_draft",
        content="Executive summary draft v1",
        metadata={"version": 1, "author": "tester"},
    )
    assert memory.metadata == {"version": 1, "author": "tester"}


@pytest.mark.asyncio
async def test_list_for_project():
    project_id = uuid4()
    other_project = uuid4()
    repo = FakeAIContextMemoryRepository()
    svc = ContextMemoryService(memory_repo=repo)

    await svc.add_memory(project_id, "type_a", "content1")
    await svc.add_memory(project_id, "type_b", "content2")
    await svc.add_memory(other_project, "type_a", "content3")

    memories = await svc.list_for_project(project_id)
    assert len(memories) == 2


@pytest.mark.asyncio
async def test_list_by_type():
    project_id = uuid4()
    repo = FakeAIContextMemoryRepository()
    svc = ContextMemoryService(memory_repo=repo)

    await svc.add_memory(project_id, "finding_summary", "A")
    await svc.add_memory(project_id, "report_draft", "B")
    await svc.add_memory(project_id, "finding_summary", "C")

    summaries = await svc.list_by_type(project_id, "finding_summary")
    assert len(summaries) == 2

    drafts = await svc.list_by_type(project_id, "report_draft")
    assert len(drafts) == 1


@pytest.mark.asyncio
async def test_delete_memory():
    project_id = uuid4()
    repo = FakeAIContextMemoryRepository()
    svc = ContextMemoryService(memory_repo=repo)

    memory = await svc.add_memory(project_id, "type", "content")
    await svc.delete(memory.id)

    remaining = await svc.list_for_project(project_id)
    assert len(remaining) == 0


@pytest.mark.asyncio
async def test_delete_nonexistent_does_not_raise():
    repo = FakeAIContextMemoryRepository()
    svc = ContextMemoryService(memory_repo=repo)
    await svc.delete(uuid4())


@pytest.mark.asyncio
async def test_list_for_project_empty():
    svc = ContextMemoryService(memory_repo=FakeAIContextMemoryRepository())
    result = await svc.list_for_project(uuid4())
    assert result == []


@pytest.mark.asyncio
async def test_list_by_type_empty():
    svc = ContextMemoryService(memory_repo=FakeAIContextMemoryRepository())
    result = await svc.list_by_type(uuid4(), "nonexistent")
    assert result == []
