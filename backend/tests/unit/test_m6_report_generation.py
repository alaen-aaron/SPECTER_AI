"""
Milestone 6 — Report Generation Tests.

Tests for:
  - Report templates (pentest_report, vulnerability_assessment, recon_summary)
  - Template registry (list_templates, get_template)
  - Redacted report generation
  - PDF export (HTML fallback)
  - Report diffing
  - Enriched report content (assets, scans, graph summary)
  - Template selection in generate_version
"""

from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.application.report_service import ReportService
from app.application.report_templates import (
    get_template,
    list_templates,
    pentest_report,
    recon_summary,
    vulnerability_assessment,
)
from app.domain.entities import Asset, Evidence, Finding, Scan
from app.domain.value_objects import (
    AssetType,
    EvidenceType,
    FindingStatus,
    ScanStatus,
    Severity,
)
from tests.fakes import (
    FakeAssetRepository,
    FakeEvidenceRepository,
    FakeFindingRepository,
    FakeGraphRepository,
    FakeReportRepository,
    FakeReportVersionRepository,
    FakeScanRepository,
    FakeTargetRepository,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_finding(
    project_id: UUID,
    title: str = "Test finding",
    severity: Severity = Severity.MEDIUM,
    description: str = "",
) -> Finding:
    return Finding(
        id=uuid4(),
        project_id=project_id,
        title=title,
        severity=severity,
        status=FindingStatus.OPEN,
        description=description,
        dedup_key="",
        created_at=datetime.now(UTC),
    )


def _make_asset(
    project_id: UUID,
    value: str = "10.0.0.1",
    asset_type: AssetType = AssetType.HOST,
) -> Asset:
    return Asset(
        id=uuid4(),
        project_id=project_id,
        asset_type=asset_type,
        value=value,
        first_seen=datetime.now(UTC),
        last_seen=datetime.now(UTC),
    )


def _make_scan(
    project_id: UUID,
    plugin: str = "nmap",
    target: str = "10.0.0.1",
    status: ScanStatus = ScanStatus.COMPLETED,
) -> Scan:
    return Scan(
        id=uuid4(),
        project_id=project_id,
        initiated_by=uuid4(),
        plugin=plugin,
        status=status,
        target_ids=[],
        plugin_config={"target": target},
        created_at=datetime.now(UTC),
    )


def _make_evidence(
    finding_id: UUID,
    filename: str = "nuclei-output.jsonl",
    content_hash: str = "a" * 64,
) -> Evidence:
    return Evidence(
        id=uuid4(),
        finding_id=finding_id,
        evidence_type=EvidenceType.RAW_LOG,
        storage_pointer=f"/tmp/specter-artifacts/d8/{content_hash}.log",
        content_hash=content_hash,
        collected_by=uuid4(),
        collected_at=datetime.now(UTC),
        filename=filename,
        file_size=1024,
    )


def _make_service(
    tmp_path: str | None = None,
) -> tuple[
    ReportService,
    FakeReportRepository,
    FakeReportVersionRepository,
    FakeFindingRepository,
    FakeAssetRepository,
    FakeScanRepository,
    FakeGraphRepository,
]:
    report_repo = FakeReportRepository()
    version_repo = FakeReportVersionRepository()
    finding_repo = FakeFindingRepository()
    asset_repo = FakeAssetRepository()
    scan_repo = FakeScanRepository()
    graph_repo = FakeGraphRepository()
    artifacts_dir = tmp_path or tempfile.mkdtemp()
    service = ReportService(
        report_repository=report_repo,
        report_version_repository=version_repo,
        finding_repository=finding_repo,
        artifacts_dir=artifacts_dir,
        asset_repository=asset_repo,
        scan_repository=scan_repo,
        target_repository=FakeTargetRepository(),
        graph_repository=graph_repo,
    )
    return service, report_repo, version_repo, finding_repo, asset_repo, scan_repo, graph_repo


# ---------------------------------------------------------------------------
# Template unit tests
# ---------------------------------------------------------------------------


class TestReportTemplates:
    def test_list_templates_returns_three(self) -> None:
        templates = list_templates()
        assert len(templates) == 3
        assert "pentest_report" in templates
        assert "vulnerability_assessment" in templates
        assert "recon_summary" in templates

    def test_get_template_returns_callable(self) -> None:
        fn = get_template("pentest_report")
        assert callable(fn)

    def test_get_template_raises_for_unknown(self) -> None:
        with pytest.raises(KeyError):
            get_template("nonexistent")

    def test_pentest_report_empty(self) -> None:
        result = pentest_report(
            title="Empty Report",
            findings=[],
            assets=[],
            scans=[],
        )
        assert "# Empty Report" in result
        assert "0 security findings" in result
        assert "No assets discovered" in result

    def test_pentest_report_with_findings(self) -> None:
        findings = [
            {"title": "XSS", "severity": "high", "status": "open", "description": "Reflected XSS"},
            {"title": "Info Leak", "severity": "info", "status": "open", "description": ""},
        ]
        result = pentest_report(title="Pentest", findings=findings, assets=[], scans=[])
        assert "HIGH" in result
        assert "INFO" in result
        assert "XSS" in result
        assert "Info Leak" in result
        assert "Detailed Findings" in result

    def test_pentest_report_with_assets(self) -> None:
        assets = [
            {"value": "10.0.0.1", "asset_type": "host", "metadata": {}},
            {"value": "http://10.0.0.1:80/tcp", "asset_type": "service", "metadata": {}},
        ]
        result = pentest_report(title="Report", findings=[], assets=assets, scans=[])
        assert "2" in result  # total assets
        assert "host: 1" in result
        assert "service: 1" in result

    def test_pentest_report_with_scans(self) -> None:
        scans = [
            {"plugin": "nmap", "status": "completed", "target": "10.0.0.1"},
        ]
        result = pentest_report(title="Report", findings=[], assets=[], scans=scans)
        assert "Methodology" in result
        assert "nmap" in result
        assert "10.0.0.1" in result

    def test_pentest_report_with_graph_summary(self) -> None:
        graph = {"total_nodes": 5, "total_edges": 3, "nodes_by_type": {"asset": 3, "finding": 2}}
        result = pentest_report(
            title="Report", findings=[], assets=[], scans=[], graph_summary=graph
        )
        assert "Attack Surface" in result
        assert "5" in result  # total nodes
        assert "3" in result  # total edges

    def test_pentest_report_renders_evidence(self) -> None:
        findings = [
            {
                "title": "Prometheus Metrics - Detect",
                "severity": "medium",
                "status": "open",
                "description": "Prometheus metrics page was detected.",
                "evidence": [
                    {
                        "evidence_type": "raw_log",
                        "filename": "nuclei-prometheus-metrics.jsonl",
                        "content_hash": "a" * 64,
                        "storage_pointer": f"/tmp/specter-artifacts/d8/{'a' * 64}.log",
                        "file_size": 29575,
                        "collected_at": "2026-08-19T13:57:13Z",
                    }
                ],
            },
        ]
        result = pentest_report(title="Pentest", findings=findings, assets=[], scans=[])
        assert "**Evidence:**" in result
        assert "nuclei-prometheus-metrics.jsonl" in result
        assert "SHA-256" in result
        assert "a" * 64 in result
        assert "29575 bytes" in result
        assert "/tmp/specter-artifacts/d8/" in result

    def test_pentest_report_omits_evidence_section_when_empty(self) -> None:
        findings = [
            {"title": "XSS", "severity": "high", "status": "open", "description": ""},
        ]
        result = pentest_report(title="Pentest", findings=findings, assets=[], scans=[])
        assert "**Evidence:**" not in result

    def test_vulnerability_assessment_focuses_critical_high(self) -> None:
        findings = [
            {
                "title": "RCE",
                "severity": "critical",
                "status": "open",
                "description": "Remote code exec",
            },
            {"title": "Low issue", "severity": "low", "status": "open", "description": ""},
        ]
        result = vulnerability_assessment(title="VA", findings=findings, assets=[], scans=[])
        assert "CRITICAL" in result
        assert "RCE" in result
        assert "Critical and High" in result

    def test_vulnerability_assessment_no_critical_high(self) -> None:
        findings = [
            {"title": "Info", "severity": "info", "status": "open", "description": ""},
        ]
        result = vulnerability_assessment(title="VA", findings=findings, assets=[], scans=[])
        assert "No critical or high" in result

    def test_recon_summary_hosts_and_services(self) -> None:
        assets = [
            {"value": "10.0.0.1", "asset_type": "host", "metadata": {}},
            {"value": "ssh://10.0.0.1:22/tcp", "asset_type": "service", "metadata": {}},
        ]
        result = recon_summary(title="Recon", findings=[], assets=assets, scans=[])
        assert "Hosts" in result
        assert "Services" in result
        assert "10.0.0.1" in result


# ---------------------------------------------------------------------------
# Enriched report generation tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_version_includes_assets_and_scans():
    """generate_version fetches assets and scans to enrich the report."""
    with tempfile.TemporaryDirectory() as tmp:
        service, _, _, finding_repo, asset_repo, scan_repo, _ = _make_service(tmp)
        project_id = uuid4()
        report = await service.create(project_id, title="Enriched Report")

        await asset_repo.add(_make_asset(project_id, "10.0.0.1"))
        await asset_repo.add(
            _make_asset(project_id, "ssh://10.0.0.1:22/tcp", AssetType.SERVICE)
        )
        await scan_repo.create(_make_scan(project_id))
        await finding_repo.add(
            _make_finding(project_id, "Open SSH", Severity.INFO)
        )

        version = await service.generate_version(
            report.id, project_id, generated_by=uuid4()
        )

        with open(version.file_pointer) as fh:
            content = fh.read()
        # Template shows asset counts by type
        assert "host: 1" in content
        assert "service: 1" in content
        assert "2" in content  # total assets
        assert "nmap" in content  # scan plugin
        assert "Open SSH" in content


@pytest.mark.asyncio
async def test_generate_version_with_template():
    """generate_version accepts a template_name parameter."""
    with tempfile.TemporaryDirectory() as tmp:
        service, _, _, finding_repo, _, _, _ = _make_service(tmp)
        project_id = uuid4()
        report = await service.create(project_id, title="Template Test")

        await finding_repo.add(
            _make_finding(project_id, "Critical Vuln", Severity.CRITICAL)
        )

        version = await service.generate_version(
            report.id, project_id, generated_by=uuid4(),
            template_name="vulnerability_assessment",
        )

        with open(version.file_pointer) as fh:
            content = fh.read()
        assert "Vulnerability Assessment" in content
        assert "CRITICAL" in content


@pytest.mark.asyncio
async def test_generate_version_includes_evidence():
    """generate_version renders evidence metadata attached to findings."""
    with tempfile.TemporaryDirectory() as tmp:
        service, _, _, finding_repo, _, _, _ = _make_service(tmp)
        evidence_repo = FakeEvidenceRepository()
        evidence_repo.set_findings(finding_repo)
        service._evidence_repo = evidence_repo
        project_id = uuid4()
        report = await service.create(project_id, title="Evidence Report")

        finding = _make_finding(project_id, "Prometheus Metrics - Detect", Severity.MEDIUM)
        await finding_repo.add(finding)
        await evidence_repo.add(_make_evidence(finding.id))

        version = await service.generate_version(
            report.id, project_id, generated_by=uuid4()
        )

        with open(version.file_pointer) as fh:
            content = fh.read()
        assert "Prometheus Metrics - Detect" in content
        assert "**Evidence:**" in content
        assert "nuclei-output.jsonl" in content
        assert "a" * 64 in content
        assert "SHA-256" in content


@pytest.mark.asyncio
async def test_generate_version_redacted_keeps_evidence_metadata():
    """Redacted reports still surface evidence metadata (no IPs are stored in it)."""
    with tempfile.TemporaryDirectory() as tmp:
        service, _, _, finding_repo, _, _, _ = _make_service(tmp)
        evidence_repo = FakeEvidenceRepository()
        evidence_repo.set_findings(finding_repo)
        service._evidence_repo = evidence_repo
        project_id = uuid4()
        report = await service.create(project_id, title="Redacted Evidence Report")

        finding = _make_finding(project_id, "Prometheus Metrics - Detect", Severity.MEDIUM)
        await finding_repo.add(finding)
        await evidence_repo.add(_make_evidence(finding.id))

        version = await service.generate_version(
            report.id, project_id, generated_by=uuid4(),
            is_redacted=True,
        )

        with open(version.file_pointer) as fh:
            content = fh.read()
        assert "**Evidence:**" in content
        assert "nuclei-output.jsonl" in content


@pytest.mark.asyncio
async def test_generate_version_redacted():
    """generate_version with is_redacted=True redacts asset values."""
    with tempfile.TemporaryDirectory() as tmp:
        service, _, _, _, asset_repo, _, _ = _make_service(tmp)
        project_id = uuid4()
        report = await service.create(project_id, title="Redacted Report")

        await asset_repo.add(_make_asset(project_id, "10.0.0.1"))

        version = await service.generate_version(
            report.id, project_id, generated_by=uuid4(),
            is_redacted=True,
            template_name="recon_summary",
        )

        with open(version.file_pointer) as fh:
            content = fh.read()
        # Redacted template replaces asset values with placeholder
        assert "10.0.0.1" not in content
        assert "REDACTED_ASSET" in content


# ---------------------------------------------------------------------------
# PDF export tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_pdf_creates_html_fallback():
    """export_pdf produces an HTML file when weasyprint is unavailable."""
    with tempfile.TemporaryDirectory() as tmp:
        service, _, _, _, _, _, _ = _make_service(tmp)
        project_id = uuid4()
        report = await service.create(project_id, title="PDF Test")
        await service.generate_version(
            report.id, project_id, generated_by=uuid4()
        )

        pdf_path = await service.export_pdf(
            report.id, project_id, generated_by=uuid4()
        )
        assert os.path.isfile(pdf_path)
        assert pdf_path.endswith(".html") or pdf_path.endswith(".pdf")

        if pdf_path.endswith(".html"):
            with open(pdf_path) as fh:
                content = fh.read()
            assert "PDF Test" in content
            assert "<html" in content


# ---------------------------------------------------------------------------
# Diff tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_diff_versions_detects_additions():
    """diff_versions detects findings added between versions."""
    with tempfile.TemporaryDirectory() as tmp:
        service, _, _, finding_repo, _, _, _ = _make_service(tmp)
        project_id = uuid4()
        report = await service.create(project_id, title="Diff Test")

        # Version 1: one finding
        await finding_repo.add(
            _make_finding(project_id, "Finding A", Severity.HIGH)
        )
        v1 = await service.generate_version(
            report.id, project_id, generated_by=uuid4()
        )

        # Version 2: two findings (A + B)
        await finding_repo.add(
            _make_finding(project_id, "Finding B", Severity.MEDIUM)
        )
        v2 = await service.generate_version(
            report.id, project_id, generated_by=uuid4()
        )

        diff = await service.diff_versions(v1.id, v2.id)
        # Findings are numbered in templates (e.g. "1. Finding B")
        assert any("Finding B" in f for f in diff["findings_added"])
        assert diff["total_findings_b"] == 2


@pytest.mark.asyncio
async def test_diff_versions_detects_removals():
    """diff_versions detects findings removed between versions."""
    with tempfile.TemporaryDirectory() as tmp:
        service, _, _, finding_repo, _, _, _ = _make_service(tmp)
        project_id = uuid4()
        report = await service.create(project_id, title="Diff Test")

        # Version 1: two findings
        f1 = _make_finding(project_id, "Finding A", Severity.HIGH)
        f2 = _make_finding(project_id, "Finding B", Severity.MEDIUM)
        await finding_repo.add(f1)
        await finding_repo.add(f2)
        v1 = await service.generate_version(
            report.id, project_id, generated_by=uuid4()
        )

        # Version 2: only finding A (B removed from repo)
        from tests.fakes import FakeFindingRepository

        new_finding_repo = FakeFindingRepository()
        await new_finding_repo.add(f1)
        service._finding_repo = new_finding_repo

        v2 = await service.generate_version(
            report.id, project_id, generated_by=uuid4()
        )

        diff = await service.diff_versions(v1.id, v2.id)
        assert any("Finding B" in f for f in diff["findings_removed"])


@pytest.mark.asyncio
async def test_diff_versions_content_hash():
    """diff_versions returns content hashes for both versions."""
    with tempfile.TemporaryDirectory() as tmp:
        service, _, _, _, _, _, _ = _make_service(tmp)
        project_id = uuid4()
        report = await service.create(project_id, title="Hash Test")

        v1 = await service.generate_version(
            report.id, project_id, generated_by=uuid4()
        )
        v2 = await service.generate_version(
            report.id, project_id, generated_by=uuid4()
        )

        diff = await service.diff_versions(v1.id, v2.id)
        assert len(diff["content_hash_a"]) == 16
        assert len(diff["content_hash_b"]) == 16
        # Hashes differ because timestamps differ between version generations


# ---------------------------------------------------------------------------
# Available templates endpoint test
# ---------------------------------------------------------------------------


def test_available_templates_returns_list():
    service, _, _, _, _, _, _ = _make_service()
    templates = service.available_templates()
    assert "pentest_report" in templates
    assert "vulnerability_assessment" in templates
    assert "recon_summary" in templates


# ---------------------------------------------------------------------------
# Redaction unit tests
# ---------------------------------------------------------------------------


class TestRedaction:
    def test_redact_findings_removes_ips(self) -> None:
        findings = [
            {"title": "Test", "severity": "high", "description": "Found vuln on 192.168.1.1"}
        ]
        result = ReportService._redact_findings(findings)
        assert "192.168.1.1" not in result[0]["description"]
        assert "REDACTED_IP" in result[0]["description"]

    def test_redact_findings_removes_hostnames(self) -> None:
        findings = [
            {
                "title": "Test",
                "severity": "high",
                "description": "Server admin.example.com vulnerable",
            }
        ]
        result = ReportService._redact_findings(findings)
        assert "admin.example.com" not in result[0]["description"]
        assert "REDACTED_HOST" in result[0]["description"]

    def test_redact_assets_replaces_values(self) -> None:
        assets = [
            {"value": "10.0.0.1", "asset_type": "host"},
            {"value": "ssh://10.0.0.1:22/tcp", "asset_type": "service"},
        ]
        result = ReportService._redact_assets(assets)
        for a in result:
            assert a["value"] == "[REDACTED_ASSET]"
