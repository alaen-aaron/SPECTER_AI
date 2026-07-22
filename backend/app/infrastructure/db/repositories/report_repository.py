"""SQLAlchemy implementation of ReportRepository and ReportVersionRepository."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as SqlAsyncSession

from app.domain.entities import Report, ReportVersion
from app.domain.value_objects import ReportStatus
from app.infrastructure.db.models.report import ReportModel, ReportVersionModel


def _to_report_entity(row: ReportModel) -> Report:
    return Report(
        id=row.id,
        project_id=row.project_id,
        title=row.title,
        status=ReportStatus(row.status),
        created_at=row.created_at,
    )


def _to_version_entity(row: ReportVersionModel) -> ReportVersion:
    return ReportVersion(
        id=row.id,
        report_id=row.report_id,
        version_number=row.version_number,
        file_pointer=row.file_pointer,
        is_redacted=row.is_redacted,
        generated_by=row.generated_by,
        generated_at=row.generated_at,
        created_at=row.created_at,
    )


class SqlAlchemyReportRepository:
    def __init__(self, session: SqlAsyncSession) -> None:
        self._session = session

    async def add(self, report: Report) -> None:
        model = ReportModel(
            id=report.id,
            project_id=report.project_id,
            title=report.title,
            status=report.status.value,
        )
        self._session.add(model)
        await self._session.flush()

    async def get(self, report_id: UUID) -> Report | None:
        row = await self._session.get(ReportModel, report_id)
        return _to_report_entity(row) if row else None

    async def list_for_project(self, project_id: UUID) -> list[Report]:
        stmt = (
            select(ReportModel)
            .where(ReportModel.project_id == project_id)
            .order_by(ReportModel.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return [_to_report_entity(row) for row in result.scalars().all()]

    async def update_status(self, report_id: UUID, status: ReportStatus) -> None:
        from sqlalchemy import update

        stmt = (
            update(ReportModel)
            .where(ReportModel.id == report_id)
            .values(status=status.value)
        )
        await self._session.execute(stmt)
        await self._session.flush()


class SqlAlchemyReportVersionRepository:
    def __init__(self, session: SqlAsyncSession) -> None:
        self._session = session

    async def add(self, version: ReportVersion) -> None:
        model = ReportVersionModel(
            id=version.id,
            report_id=version.report_id,
            version_number=version.version_number,
            file_pointer=version.file_pointer,
            is_redacted=version.is_redacted,
            generated_by=version.generated_by,
            generated_at=version.generated_at,
        )
        self._session.add(model)
        await self._session.flush()

    async def get(self, version_id: UUID) -> ReportVersion | None:
        row = await self._session.get(ReportVersionModel, version_id)
        return _to_version_entity(row) if row else None

    async def list_for_report(self, report_id: UUID) -> list[ReportVersion]:
        stmt = (
            select(ReportVersionModel)
            .where(ReportVersionModel.report_id == report_id)
            .order_by(ReportVersionModel.version_number.asc())
        )
        result = await self._session.execute(stmt)
        return [_to_version_entity(row) for row in result.scalars().all()]

    async def get_latest(self, report_id: UUID) -> ReportVersion | None:
        stmt = (
            select(ReportVersionModel)
            .where(ReportVersionModel.report_id == report_id)
            .order_by(ReportVersionModel.version_number.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        return _to_version_entity(row) if row else None
