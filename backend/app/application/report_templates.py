"""
Report templates (Milestone 6).

Each template is a plain function that takes structured data and returns
a Markdown string.  The ReportService picks a template by name and
fills it with the project's findings, assets, scans, and graph summary.

Templates are deliberately stateless — no database access, no side effects.
They receive pre-fetched data and render Markdown.  This keeps them
testable in isolation.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any


def _severity_table(findings: list[dict[str, Any]]) -> str:
    """Render a Markdown table of findings grouped by severity."""
    counts: Counter[str] = Counter(f["severity"] for f in findings)
    lines = [
        "| Severity | Count |",
        "|----------|------:|",
    ]
    for sev in ("critical", "high", "medium", "low", "info"):
        count = counts.get(sev, 0)
        if count:
            lines.append(f"| {sev.upper()} | {count} |")
    return "\n".join(lines)


def _asset_summary(assets: list[dict[str, Any]]) -> str:
    """Render a Markdown summary of discovered assets."""
    if not assets:
        return "*No assets discovered.*"

    type_counts: Counter[str] = Counter(a["asset_type"] for a in assets)
    lines = [
        f"**Total assets:** {len(assets)}",
        "",
    ]
    for atype, count in type_counts.most_common():
        lines.append(f"- {atype}: {count}")
    return "\n".join(lines)


def _evidence_lines(evidence: list[dict[str, Any]] | None) -> list[str]:
    """Render evidence metadata lines for a finding."""
    if not evidence:
        return []
    lines = ["**Evidence:**", ""]
    for i, ev in enumerate(evidence, 1):
        fname = ev.get("filename") or "unnamed artifact"
        lines.append(f"- Artifact {i}: `{fname}`")
        lines.append(f"  - Type: {ev.get('evidence_type', 'unknown')}")
        if ev.get("file_size") is not None:
            lines.append(f"  - Size: {ev['file_size']} bytes")
        if ev.get("content_hash"):
            lines.append(f"  - SHA-256: `{ev['content_hash']}`")
        if ev.get("storage_pointer"):
            lines.append(f"  - Stored at: `{ev['storage_pointer']}`")
        if ev.get("collected_at"):
            lines.append(f"  - Collected at: {ev['collected_at']}")
    lines.append("")
    return lines


def pentest_report(
    title: str,
    findings: list[dict[str, Any]],
    assets: list[dict[str, Any]],
    scans: list[dict[str, Any]],
    graph_summary: dict[str, Any] | None = None,
    generated_at: str | None = None,
    **_extra: Any,
) -> str:
    """Full penetration test report with executive summary, methodology, findings."""
    ts = generated_at or datetime.now(UTC).isoformat()
    lines = [
        f"# {title}",
        "",
        f"*Generated: {ts}*",
        "",
        "---",
        "",
        "## Executive Summary",
        "",
        f"This report presents the findings of a penetration test assessment "
        f"encompassing {len(assets)} assets across {len(scans)} scan operations. "
        f"A total of {len(findings)} security findings were identified.",
        "",
    ]

    if findings:
        lines.append("### Findings Overview")
        lines.append("")
        lines.append(_severity_table(findings))
        lines.append("")

    if scans:
        lines.append("## Methodology")
        lines.append("")
        lines.append("The following scanning operations were performed:")
        lines.append("")
        for scan in scans:
            status = scan.get("status", "unknown")
            plugin = scan.get("plugin", "unknown")
            target = scan.get("target", "N/A")
            lines.append(f"- **{plugin}** targeting `{target}` — status: {status}")
        lines.append("")

    lines.append("## Scope")
    lines.append("")
    lines.append(_asset_summary(assets))
    lines.append("")

    if graph_summary:
        lines.append("## Attack Surface")
        lines.append("")
        lines.append(f"- Total nodes: {graph_summary.get('total_nodes', 0)}")
        lines.append(f"- Total edges: {graph_summary.get('total_edges', 0)}")
        nodes_by_type = graph_summary.get("nodes_by_type", {})
        if nodes_by_type:
            lines.append("- Node types:")
            for ntype, count in nodes_by_type.items():
                lines.append(f"  - {ntype}: {count}")
        lines.append("")

    if findings:
        lines.append("## Detailed Findings")
        lines.append("")
        for i, f in enumerate(findings, 1):
            sev = f.get("severity", "info").upper()
            lines.append(f"### {i}. {f['title']}")
            lines.append("")
            lines.append(f"- **Severity:** {sev}")
            lines.append(f"- **Status:** {f.get('status', 'open')}")
            if f.get("description"):
                lines.append(f"- **Description:** {f['description']}")
            if f.get("asset_value"):
                lines.append(f"- **Affected Asset:** `{f['asset_value']}`")
            evidence_lines = _evidence_lines(f.get("evidence"))
            if evidence_lines:
                lines.extend(evidence_lines)
            lines.append("")

    lines.append("---")
    lines.append(f"*Report generated by SPECTER_AI on {ts}*")
    return "\n".join(lines)


def vulnerability_assessment(
    title: str,
    findings: list[dict[str, Any]],
    assets: list[dict[str, Any]],
    scans: list[dict[str, Any]],
    graph_summary: dict[str, Any] | None = None,
    generated_at: str | None = None,
    **_extra: Any,
) -> str:
    """Focused vulnerability assessment — prioritizes findings by severity."""
    ts = generated_at or datetime.now(UTC).isoformat()
    lines = [
        f"# {title}",
        "",
        f"*Vulnerability Assessment — {ts}*",
        "",
        "---",
        "",
        "## Summary",
        "",
    ]

    if findings:
        lines.append(_severity_table(findings))
        lines.append("")

    lines.append("## Critical and High Findings")
    lines.append("")

    critical_high = [f for f in findings if f.get("severity") in ("critical", "high")]
    if critical_high:
        for f in critical_high:
            sev = f["severity"].upper()
            lines.append(f"### [{sev}] {f['title']}")
            lines.append("")
            if f.get("description"):
                lines.append(f"{f['description']}")
            if f.get("asset_value"):
                lines.append(f"- Asset: `{f['asset_value']}`")
            evidence_lines = _evidence_lines(f.get("evidence"))
            if evidence_lines:
                lines.extend(evidence_lines)
            lines.append("")
    else:
        lines.append("*No critical or high severity findings.*")
        lines.append("")

    medium_low = [f for f in findings if f.get("severity") in ("medium", "low")]
    if medium_low:
        lines.append("## Medium and Low Findings")
        lines.append("")
        for f in medium_low:
            sev = f["severity"].upper()
            lines.append(f"- **[{sev}]** {f['title']}")
        lines.append("")

    if assets:
        lines.append("## Assets Assessed")
        lines.append("")
        lines.append(_asset_summary(assets))
        lines.append("")

    lines.append("---")
    lines.append(f"*Generated by SPECTER_AI on {ts}*")
    return "\n".join(lines)


def recon_summary(
    title: str,
    findings: list[dict[str, Any]],
    assets: list[dict[str, Any]],
    scans: list[dict[str, Any]],
    graph_summary: dict[str, Any] | None = None,
    generated_at: str | None = None,
    **_extra: Any,
) -> str:
    """Reconnaissance-focused report — emphasizes assets and discovery."""
    ts = generated_at or datetime.now(UTC).isoformat()
    lines = [
        f"# {title}",
        "",
        f"*Reconnaissance Summary — {ts}*",
        "",
        "---",
        "",
        "## Discovered Assets",
        "",
    ]

    if assets:
        hosts = [a for a in assets if a.get("asset_type") == "host"]
        services = [a for a in assets if a.get("asset_type") == "service"]
        subdomains = [a for a in assets if a.get("asset_type") == "subdomain"]

        if hosts:
            lines.append("### Hosts")
            for h in hosts:
                lines.append(f"- `{h['value']}`")
            lines.append("")

        if services:
            lines.append("### Services")
            for s in services:
                lines.append(f"- `{s['value']}`")
            lines.append("")

        if subdomains:
            lines.append("### Subdomains")
            for s in subdomains:
                lines.append(f"- `{s['value']}`")
            lines.append("")

    lines.append("## Scan Operations")
    lines.append("")
    for scan in scans:
        plugin = scan.get("plugin", "?")
        target = scan.get("target", "?")
        scan_status = scan.get("status", "?")
        lines.append(f"- {plugin} -> `{target}` [{scan_status}]")
    lines.append("")

    if findings:
        lines.append("## Informational Findings")
        lines.append("")
        for f in findings:
            lines.append(f"- {f['title']} ({f.get('severity', 'info')})")
        lines.append("")

    lines.append("---")
    lines.append(f"*Generated by SPECTER_AI on {ts}*")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Template registry
# ---------------------------------------------------------------------------

TEMPLATES: dict[str, Any] = {
    "pentest_report": pentest_report,
    "vulnerability_assessment": vulnerability_assessment,
    "recon_summary": recon_summary,
}


def get_template(name: str) -> Any:
    """Return a template function by name, or raise KeyError."""
    return TEMPLATES[name]


def list_templates() -> list[str]:
    """Return all available template names."""
    return list(TEMPLATES.keys())
