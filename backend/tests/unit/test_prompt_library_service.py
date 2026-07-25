"""Unit tests for PromptLibraryService (Phase 4, SRS §8.2)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.application.prompt_library_service import PromptLibraryService
from app.domain.exceptions import (
    DomainError,
    PromptTemplateNotFoundError,
)
from tests.fakes import FakePromptTemplateRepository


@pytest.mark.asyncio
async def test_create_template():
    repo = FakePromptTemplateRepository()
    svc = PromptLibraryService(template_repo=repo)

    tmpl = await svc.create_template(
        name="explain_finding",
        purpose="Explain a security finding",
        template_text="Explain {{title}} which has severity {{severity}}.",
        required_variables=["title", "severity"],
    )
    assert tmpl.name == "explain_finding"
    assert tmpl.version == 1
    assert tmpl.is_active is True
    assert tmpl.required_variables == ["title", "severity"]


@pytest.mark.asyncio
async def test_get_template():
    repo = FakePromptTemplateRepository()
    svc = PromptLibraryService(template_repo=repo)
    tmpl = await svc.create_template(
        name="test", purpose="test", template_text="hello"
    )
    result = await svc.get_template(tmpl.id)
    assert result.name == "test"


@pytest.mark.asyncio
async def test_get_template_not_found():
    svc = PromptLibraryService(template_repo=FakePromptTemplateRepository())
    with pytest.raises(PromptTemplateNotFoundError):
        await svc.get_template(uuid4())


@pytest.mark.asyncio
async def test_get_active_by_name():
    repo = FakePromptTemplateRepository()
    svc = PromptLibraryService(template_repo=repo)

    await svc.create_template("summarize", "Summarize", "v1 text", version=1)
    await svc.create_template("summarize", "Summarize", "v2 text", version=2)

    result = await svc.get_active_by_name("summarize")
    assert result is not None
    assert result.version == 2


@pytest.mark.asyncio
async def test_get_active_by_name_none():
    svc = PromptLibraryService(template_repo=FakePromptTemplateRepository())
    result = await svc.get_active_by_name("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_list_all():
    repo = FakePromptTemplateRepository()
    svc = PromptLibraryService(template_repo=repo)

    await svc.create_template("a", "purpose a", "text a")
    await svc.create_template("b", "purpose b", "text b")

    all_tmpls = await svc.list_all()
    assert len(all_tmpls) == 2


@pytest.mark.asyncio
async def test_update_template_text():
    repo = FakePromptTemplateRepository()
    svc = PromptLibraryService(template_repo=repo)
    tmpl = await svc.create_template("t", "p", "old text")

    updated = await svc.update_template(tmpl.id, template_text="new text")
    assert updated.template_text == "new text"


@pytest.mark.asyncio
async def test_update_template_purpose():
    repo = FakePromptTemplateRepository()
    svc = PromptLibraryService(template_repo=repo)
    tmpl = await svc.create_template("t", "old purpose", "text")

    updated = await svc.update_template(tmpl.id, purpose="new purpose")
    assert updated.purpose == "new purpose"


@pytest.mark.asyncio
async def test_update_template_deactivate():
    repo = FakePromptTemplateRepository()
    svc = PromptLibraryService(template_repo=repo)
    tmpl = await svc.create_template("t", "p", "text")

    updated = await svc.update_template(tmpl.id, is_active=False)
    assert updated.is_active is False


@pytest.mark.asyncio
async def test_update_template_not_found():
    svc = PromptLibraryService(template_repo=FakePromptTemplateRepository())
    with pytest.raises(PromptTemplateNotFoundError):
        await svc.update_template(uuid4())


@pytest.mark.asyncio
async def test_delete_template():
    repo = FakePromptTemplateRepository()
    svc = PromptLibraryService(template_repo=repo)
    tmpl = await svc.create_template("t", "p", "text")

    await svc.delete_template(tmpl.id)
    with pytest.raises(PromptTemplateNotFoundError):
        await svc.get_template(tmpl.id)


@pytest.mark.asyncio
async def test_render_template():
    repo = FakePromptTemplateRepository()
    svc = PromptLibraryService(template_repo=repo)
    tmpl = await svc.create_template(
        name="explain",
        purpose="Explain",
        template_text="Explain {{title}} (severity: {{severity}}).",
        required_variables=["title", "severity"],
    )

    result = await svc.render_template(
        tmpl.id, {"title": "SQLi", "severity": "critical"}
    )
    assert result == "Explain SQLi (severity: critical)."


@pytest.mark.asyncio
async def test_render_template_missing_variables():
    repo = FakePromptTemplateRepository()
    svc = PromptLibraryService(template_repo=repo)
    tmpl = await svc.create_template(
        name="explain",
        purpose="Explain",
        template_text="Explain {{title}}.",
        required_variables=["title", "severity"],
    )

    with pytest.raises(DomainError, match="Missing required variables"):
        await svc.render_template(tmpl.id, {"title": "SQLi"})


@pytest.mark.asyncio
async def test_render_template_no_required_vars():
    repo = FakePromptTemplateRepository()
    svc = PromptLibraryService(template_repo=repo)
    tmpl = await svc.create_template(
        name="static", purpose="Static", template_text="No variables here."
    )

    result = await svc.render_template(tmpl.id, {})
    assert result == "No variables here."
