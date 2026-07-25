"""SQLAlchemy implementation of `PromptTemplateRepository` (SRS §8.2)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as SqlAsyncSession

from app.domain.entities import PromptTemplate
from app.infrastructure.db.models.ai_engine import PromptTemplateModel


def _to_entity(row: PromptTemplateModel) -> PromptTemplate:
    raw_vars = row.required_variables
    vars_list: list[str] = []
    if isinstance(raw_vars, list):
        vars_list = [str(v) for v in raw_vars]
    return PromptTemplate(
        id=row.id,
        name=row.name,
        version=row.version,
        purpose=row.purpose,
        template_text=row.template_text,
        required_variables=vars_list,
        expected_output_schema=row.expected_output_schema or {},
        is_active=row.is_active,
        created_at=row.created_at,
    )


class SqlAlchemyPromptTemplateRepository:
    def __init__(self, session: SqlAsyncSession) -> None:
        self._session = session

    async def create(self, template: PromptTemplate) -> None:
        model = PromptTemplateModel(
            id=template.id,
            name=template.name,
            version=template.version,
            purpose=template.purpose,
            template_text=template.template_text,
            required_variables=template.required_variables,
            expected_output_schema=template.expected_output_schema,
            is_active=template.is_active,
        )
        self._session.add(model)
        await self._session.flush()

    async def get(self, template_id: UUID) -> PromptTemplate | None:
        row = await self._session.get(PromptTemplateModel, template_id)
        return _to_entity(row) if row else None

    async def get_active_by_name(self, name: str) -> PromptTemplate | None:
        stmt = (
            select(PromptTemplateModel)
            .where(PromptTemplateModel.name == name)
            .where(PromptTemplateModel.is_active == True)  # noqa: E712
            .order_by(PromptTemplateModel.version.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        row = result.scalars().first()
        return _to_entity(row) if row else None

    async def list_all(self) -> list[PromptTemplate]:
        stmt = select(PromptTemplateModel).order_by(
            PromptTemplateModel.name, PromptTemplateModel.version.desc()
        )
        result = await self._session.execute(stmt)
        return [_to_entity(row) for row in result.scalars().all()]

    async def update(self, template: PromptTemplate) -> None:
        row = await self._session.get(PromptTemplateModel, template.id)
        if row is None:
            return
        row.template_text = template.template_text
        row.purpose = template.purpose
        row.required_variables = list(template.required_variables)
        row.expected_output_schema = template.expected_output_schema
        row.is_active = template.is_active
        await self._session.flush()

    async def delete(self, template_id: UUID) -> None:
        row = await self._session.get(PromptTemplateModel, template_id)
        if row is not None:
            await self._session.delete(row)
            await self._session.flush()
