"""
Application service for Report lifecycle (Milestones 5 + 6).

M5: CRUD, versioned markdown generation, finalization.
M6: Template-based rendering, redacted reports, PDF export, diffing.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections import Counter
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from app.application.report_templates import get_template, list_templates
from app.domain.entities import Report, ReportVersion
from app.domain.exceptions import (
    ReportAlreadyFinalizedError,
    ReportNotFoundError,
)
from app.domain.repositories import (
    AssetRepository,
    EvidenceRepository,
    FindingRepository,
    GraphRepository,
    ReportRepository,
    ReportVersionRepository,
    ScanRepository,
    TargetRepository,
)
from app.domain.value_objects import ReportStatus

# IP/hostname redaction patterns
_IP_RE = re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")
_HOSTNAME_RE = re.compile(r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}\b")
_CREDENTIAL_PATTERNS = re.compile(
    r"(password|secret|token|api[_-]?key|credential)\s*[=:]\s*\S+",
    re.IGNORECASE,
)


class ReportService:
    def __init__(
        self,
        report_repository: ReportRepository,
        report_version_repository: ReportVersionRepository,
        finding_repository: FindingRepository,
        artifacts_dir: str,
        asset_repository: AssetRepository | None = None,
        scan_repository: ScanRepository | None = None,
        target_repository: TargetRepository | None = None,
        graph_repository: GraphRepository | None = None,
        evidence_repository: EvidenceRepository | None = None,
    ) -> None:
        self._report_repo = report_repository
        self._version_repo = report_version_repository
        self._finding_repo = finding_repository
        self._artifacts_dir = artifacts_dir
        self._asset_repo = asset_repository
        self._scan_repo = scan_repository
        self._target_repo = target_repository
        self._graph_repo = graph_repository
        self._evidence_repo = evidence_repository

    # --- M5: Core CRUD ---

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

    async def finalize(self, report_id: UUID) -> Report:
        report = await self.get(report_id)
        if report.is_final:
            raise ReportAlreadyFinalizedError(report_id)
        await self._report_repo.update_status(report_id, ReportStatus.FINAL)
        report.status = ReportStatus.FINAL
        return report

    # --- M5: Version generation (original) ---

    async def generate_version(
        self,
        report_id: UUID,
        project_id: UUID,
        generated_by: UUID,
        is_redacted: bool = False,
        template_name: str | None = None,
    ) -> ReportVersion:
        """Generate a new version of a report, optionally with a template."""
        report = await self.get(report_id)
        if report.is_final:
            raise ReportAlreadyFinalizedError(report_id)

        findings = await self._finding_repo.list_for_project(project_id, limit=10000)
        evidence_by_finding = await self._collect_evidence_by_finding(project_id)
        findings_data = [
            {
                "title": f.title,
                "severity": f.severity.value,
                "status": f.status.value,
                "description": f.description or "",
                "asset_id": str(f.asset_id) if f.asset_id else None,
                "evidence": evidence_by_finding.get(f.id, []),
            }
            for f in findings
        ]

        assets_data = await self._collect_assets(project_id)
        scans_data = await self._collect_scans(project_id)
        graph_summary = await self._collect_graph_summary(project_id)

        if is_redacted:
            findings_data = self._redact_findings(findings_data)
            assets_data = self._redact_assets(assets_data)

        template_name = template_name or "pentest_report"
        try:
            template_fn = get_template(template_name)
        except KeyError:
            template_fn = get_template("pentest_report")

        now_str = datetime.now(UTC).isoformat()
        markdown_content = template_fn(
            title=report.title,
            findings=findings_data,
            assets=assets_data,
            scans=scans_data,
            graph_summary=graph_summary,
            generated_at=now_str,
        )

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

    # --- M6: PDF export ---

    async def export_pdf(
        self,
        report_id: UUID,
        project_id: UUID,
        generated_by: UUID,
    ) -> str:
        """Generate a PDF version of the latest report version.

        Uses a simple HTML-to-PDF approach via the built-in
        ``reportlab``-free approach: renders Markdown → HTML → PDF
        using a lightweight wrapper.

        Returns the file path of the generated PDF.
        """
        report = await self.get(report_id)
        latest_version = await self._version_repo.get_latest(report_id)
        if latest_version is None:
            latest_version = await self.generate_version(
                report_id, project_id, generated_by
            )

        md_path = latest_version.file_pointer
        if not os.path.isfile(md_path):
            latest_version = await self.generate_version(
                report_id, project_id, generated_by
            )
            md_path = latest_version.file_pointer

        with open(md_path) as fh:
            md_content = fh.read()

        html_content = self._markdown_to_html(md_content, report.title)

        report_dir = os.path.dirname(md_path)
        pdf_path = os.path.join(report_dir, f"v{latest_version.version_number}.pdf")

        try:
            from weasyprint import HTML  # type: ignore[import-untyped]

            HTML(string=html_content).write_pdf(pdf_path)
        except ImportError:
            # Fallback: write HTML file when weasyprint is unavailable
            html_path = os.path.join(report_dir, f"v{latest_version.version_number}.html")
            with open(html_path, "w") as fh:
                fh.write(html_content)
            pdf_path = html_path

        return pdf_path

    # --- M6: Report diffing ---

    async def diff_versions(
        self,
        version_id_a: UUID,
        version_id_b: UUID,
    ) -> dict[str, Any]:
        """Compare two report versions and return structured diff data."""
        va = await self._version_repo.get(version_id_a)
        vb = await self._version_repo.get(version_id_b)
        if va is None or vb is None:
            raise ReportNotFoundError(version_id_a)

        content_a = self._read_version_content(va)
        content_b = self._read_version_content(vb)

        findings_a = self._extract_findings_from_md(content_a)
        findings_b = self._extract_findings_from_md(content_b)

        titles_a = {f["title"] for f in findings_a}
        titles_b = {f["title"] for f in findings_b}

        added = titles_b - titles_a
        removed = titles_a - titles_b
        common = titles_a & titles_b

        changed: list[str] = []
        b_by_title = {f["title"]: f for f in findings_b}
        a_by_title = {f["title"]: f for f in findings_a}
        for title in common:
            if a_by_title[title].get("severity") != b_by_title[title].get("severity"):
                changed.append(title)

        return {
            "version_a": va.version_number,
            "version_b": vb.version_number,
            "findings_added": sorted(added),
            "findings_removed": sorted(removed),
            "findings_severity_changed": sorted(changed),
            "total_findings_a": len(findings_a),
            "total_findings_b": len(findings_b),
            "content_hash_a": hashlib.sha256(content_a.encode()).hexdigest()[:16],
            "content_hash_b": hashlib.sha256(content_b.encode()).hexdigest()[:16],
        }

    # --- M6: List available templates ---

    def available_templates(self) -> list[str]:
        """Return names of available report templates."""
        return list_templates()

    # --- Private helpers ---

    async def _collect_evidence_by_finding(
        self, project_id: UUID
    ) -> dict[UUID, list[dict[str, Any]]]:
        """Group evidence metadata per finding for report rendering."""
        if self._evidence_repo is None:
            return {}
        evidence_list = await self._evidence_repo.list_for_project(project_id)
        grouped: dict[UUID, list[dict[str, Any]]] = {}
        for e in evidence_list:
            grouped.setdefault(e.finding_id, []).append(
                {
                    "id": str(e.id),
                    "evidence_type": e.evidence_type.value,
                    "filename": e.filename,
                    "content_hash": e.content_hash,
                    "storage_pointer": e.storage_pointer,
                    "collected_by": str(e.collected_by),
                    "collected_at": e.collected_at.isoformat(),
                    "file_size": e.file_size,
                }
            )
        return grouped

    async def _collect_assets(self, project_id: UUID) -> list[dict[str, Any]]:
        if self._asset_repo is None:
            return []
        assets = await self._asset_repo.list_for_project(project_id, limit=10000)
        return [
            {
                "value": a.value,
                "asset_type": a.asset_type.value,
                "metadata": a.metadata,
            }
            for a in assets
        ]

    async def _collect_scans(self, project_id: UUID) -> list[dict[str, Any]]:
        if self._scan_repo is None:
            return []
        scans = await self._scan_repo.list(project_id, limit=10000)
        return [
            {
                "plugin": s.plugin,
                "status": s.status.value,
                "target": s.plugin_config.get("target", ""),
            }
            for s in scans
        ]

    async def _collect_graph_summary(self, project_id: UUID) -> dict[str, Any] | None:
        if self._graph_repo is None:
            return None
        nodes = await self._graph_repo.list_nodes_for_project(project_id)
        edges = await self._graph_repo.list_edges_for_project(project_id)

        node_types = Counter(n.node_type.value for n in nodes)
        edge_types = Counter(e.relationship_type.value for e in edges)
        return {
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "nodes_by_type": dict(node_types),
            "edges_by_type": dict(edge_types),
        }

    @staticmethod
    def _redact_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Redact IPs and hostnames from finding data."""
        redacted = []
        for f in findings:
            entry = dict(f)
            if entry.get("description"):
                entry["description"] = _IP_RE.sub("[REDACTED_IP]", entry["description"])
                entry["description"] = _HOSTNAME_RE.sub(
                    "[REDACTED_HOST]", entry["description"]
                )
            redacted.append(entry)
        return redacted

    @staticmethod
    def _redact_assets(assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Replace asset values with redacted placeholders."""
        return [
            {**a, "value": "[REDACTED_ASSET]"}
            for a in assets
        ]

    @staticmethod
    def _read_version_content(version: ReportVersion) -> str:
        if os.path.isfile(version.file_pointer):
            with open(version.file_pointer) as fh:
                return fh.read()
        return ""

    @staticmethod
    def _extract_findings_from_md(content: str) -> list[dict[str, str]]:
        """Extract findings from the 'Detailed Findings' section of Markdown."""
        findings: list[dict[str, str]] = []
        lines = content.split("\n")
        in_detail_section = False
        for line in lines:
            if line.startswith("## Detailed Findings"):
                in_detail_section = True
                continue
            if in_detail_section and line.startswith("## "):
                break
            if in_detail_section and (line.startswith("### ") or line.startswith("#### ")):
                title = line.lstrip("#").strip()
                severity = "info"
                sev_match = re.search(
                    r"\[(CRITICAL|HIGH|MEDIUM|LOW|INFO)\]", title, re.IGNORECASE
                )
                if sev_match:
                    severity = sev_match.group(1).lower()
                else:
                    for s in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
                        if s in title.upper():
                            severity = s.lower()
                            break
                findings.append({"title": title, "severity": severity})
        return findings

    @staticmethod
    def _markdown_to_html(md_content: str, title: str) -> str:
        """Convert Markdown to a self-contained HTML document."""
        lines = md_content.split("\n")
        html_lines: list[str] = []
        in_table = False

        for line in lines:
            if line.startswith("# "):
                html_lines.append(f"<h1>{line[2:]}</h1>")
            elif line.startswith("## "):
                html_lines.append(f"<h2>{line[3:]}</h2>")
            elif line.startswith("### "):
                html_lines.append(f"<h3>{line[4:]}</h3>")
            elif line.startswith("- "):
                html_lines.append(f"<li>{line[2:]}</li>")
            elif line.startswith("| ") and "---" not in line:
                if not in_table:
                    html_lines.append("<table>")
                    in_table = True
                cells = [c.strip() for c in line.split("|")[1:-1]]
                row = "".join(f"<td>{c}</td>" for c in cells)
                html_lines.append(f"<tr>{row}</tr>")
            elif in_table and not line.startswith("|"):
                html_lines.append("</table>")
                in_table = False
                if line.strip():
                    html_lines.append(f"<p>{line}</p>")
            elif line.startswith("---"):
                html_lines.append("<hr>")
            elif line.strip():
                html_lines.append(f"<p>{line}</p>")

        if in_table:
            html_lines.append("</table>")

        body = "\n".join(html_lines)
        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  body {{ font-family: sans-serif; margin: 40px; line-height: 1.6; }}
  h1 {{ color: #1a1a2e; border-bottom: 2px solid #e94560; padding-bottom: 10px; }}
  h2 {{ color: #16213e; margin-top: 30px; }}
  h3 {{ color: #0f3460; }}
  table {{ border-collapse: collapse; margin: 15px 0; width: 100%; }}
  td {{ border: 1px solid #ddd; padding: 8px; }}
  tr:nth-child(even) {{ background: #f9f9f9; }}
  li {{ margin: 4px 0; }}
  hr {{ border: none; border-top: 1px solid #ddd; margin: 30px 0; }}
</style>
</head>
<body>
{body}
</body>
</html>"""
