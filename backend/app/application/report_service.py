"""Application service for Report lifecycle (Milestone 5)."""

from __future__ import annotations

import os
from collections import Counter
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.domain.entities import Report, ReportVersion
from app.domain.exceptions import (
    ReportAlreadyFinalizedError,
    ReportNotFoundError,
)
from app.domain.repositories import (
    FindingRepository,
    ReportRepository,
    ReportVersionRepository,
)
from app.domain.value_objects import ReportStatus


class ReportService:
    def __init__(
        self,
        report_repository: ReportRepository,
        report_version_repository: ReportVersionRepository,
        finding_repository: FindingRepository,
        artifacts_dir: str,
    ) -> None:
        self._report_repo = report_repository
        self._version_repo = report_version_repository
        self._finding_repo = finding_repository
        self._artifacts_dir = artifacts_dir

    async def create(self, project_id: UUID, title: str) -> Report:
        report = Report(
            id=uuid4(),
            project_id=project_id,
            title=title,
            status=ReportStatus.DRAFT,
            created_at=datetime.now(UTC),
        )
        await self._report_repo.add(report)
        return report

    async def get(self, report_id: UUID) -> Report:
        report = await self._report_repo.get(report_id)
        if report is None:
            raise ReportNotFoundError(report_id)
        return report

    async def list_for_project(self, project_id: UUID) -> list[Report]:
        return await self._report_repo.list_for_project(project_id)

    async def generate_version(
        self,
        report_id: UUID,
        project_id: UUID,
        generated_by: UUID,
        is_redacted: bool = False,
    ) -> ReportVersion:
        report = await self.get(report_id)
        if report.is_final:
            raise ReportAlreadyFinalizedError(report_id)

        findings = await self._finding_repo.list_for_project(project_id, limit=10000)

        severity_counts: Counter[str] = Counter()
        for f in findings:
            severity_counts[f.severity.value] += 1

        lines = [
            f"# {report.title}",
            "",
            "## Executive Summary",
            "",
            f"Total findings: {len(findings)}",
            "",
        ]
        for sev in ("critical", "high", "medium", "low", "info"):
            count = severity_counts.get(sev, 0)
            if count:
                lines.append(f"- **{sev.upper()}**: {count}")
        lines.append("")

        if findings:
            lines.append("## Findings Detail")
            lines.append("")
            for f in findings:
                lines.append(f"### {f.title}")
                lines.append(f"- Severity: {f.severity.value}")
                lines.append(f"- Status: {f.status.value}")
                if f.description:
                    lines.append(f"- Description: {f.description}")
                lines.append("")

        markdown_content = "\n".join(lines)

        latest = await self._version_repo.get_latest(report_id)
        next_version = (latest.version_number + 1) if latest else 1

        report_dir = os.path.join(self._artifacts_dir, "reports", str(report_id))
        os.makedirs(report_dir, exist_ok=True)
        file_path = os.path.join(report_dir, f"v{next_version}.md")
        with open(file_path, "w") as fh:
            fh.write(markdown_content)

        version = ReportVersion(
            id=uuid4(),
            report_id=report_id,
            version_number=next_version,
            file_pointer=file_path,
            is_redacted=is_redacted,
            generated_by=generated_by,
            generated_at=datetime.now(UTC),
            created_at=datetime.now(UTC),
        )
        await self._version_repo.add(version)
        return version

    async def finalize(self, report_id: UUID) -> Report:
        report = await self.get(report_id)
        if report.is_final:
            raise ReportAlreadyFinalizedError(report_id)
        await self._report_repo.update_status(report_id, ReportStatus.FINAL)
        report.status = ReportStatus.FINAL
        return report
