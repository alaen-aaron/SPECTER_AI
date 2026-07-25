"""
Prompt Library service (SRS §8.2).

Versioned prompt templates stored in the repo, each with purpose,
required context variables, expected output schema, and a test fixture
for regression testing prompts like code.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.domain.entities import PromptTemplate
from app.domain.exceptions import PromptTemplateNotFoundError
from app.domain.repositories import PromptTemplateRepository


class PromptLibraryService:
    """
    Manages versioned prompt templates.

    Per SRS §8.2: versioned prompt templates stored in the repo with
    purpose, required context variables, expected output schema, and
    a test fixture for regression testing.
    """

    def __init__(self, template_repo: PromptTemplateRepository) -> None:
        self._repo = template_repo

    async def create_template(
        self,
        name: str,
        purpose: str,
        template_text: str,
        required_variables: list[str] | None = None,
        expected_output_schema: dict[str, object] | None = None,
        version: int = 1,
    ) -> PromptTemplate:
        """Create a new prompt template."""
        template = PromptTemplate(
            id=uuid4(),
            name=name,
            version=version,
            purpose=purpose,
            template_text=template_text,
            required_variables=required_variables or [],
            expected_output_schema=expected_output_schema or {},
            is_active=True,
            created_at=datetime.now(UTC),
        )
        await self._repo.create(template)
        return template

    async def get_template(self, template_id: UUID) -> PromptTemplate:
        template = await self._repo.get(template_id)
        if template is None:
            raise PromptTemplateNotFoundError(template_id)
        return template

    async def get_active_by_name(self, name: str) -> PromptTemplate | None:
        """Get the latest active version of a template by name."""
        return await self._repo.get_active_by_name(name)

    async def list_all(self) -> list[PromptTemplate]:
        return await self._repo.list_all()

    async def update_template(
        self,
        template_id: UUID,
        template_text: str | None = None,
        purpose: str | None = None,
        is_active: bool | None = None,
    ) -> PromptTemplate:
        """Update an existing template."""
        template = await self._repo.get(template_id)
        if template is None:
            raise PromptTemplateNotFoundError(template_id)

        if template_text is not None:
            template.template_text = template_text
        if purpose is not None:
            template.purpose = purpose
        if is_active is not None:
            template.is_active = is_active

        await self._repo.update(template)
        return template

    async def delete_template(self, template_id: UUID) -> None:
        await self._repo.delete(template_id)

    async def render_template(
        self,
        template_id: UUID,
        variables: dict[str, str],
    ) -> str:
        """
        Render a template with the given variables.

        Raises if required variables are missing.
        """
        template = await self.get_template(template_id)

        missing = set(template.required_variables) - set(variables.keys())
        if missing:
            from app.domain.exceptions import DomainError

            raise DomainError(f"Missing required variables: {', '.join(sorted(missing))}")

        result = template.template_text
        for key, value in variables.items():
            result = result.replace(f"{{{{{key}}}}}", value)

        return result
