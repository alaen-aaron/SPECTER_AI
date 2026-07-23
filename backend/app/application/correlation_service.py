"""
Correlation Engine (Phase 2/3).

Merges duplicate findings across multiple tool invocations using
deterministic dedup keys. Happens AFTER normalization — never before.
The Knowledge Graph consumes correlated findings only.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.domain.entities import Finding, ToolResult
from app.domain.repositories import FindingRepository
from app.domain.value_objects import FindingStatus, Severity


def make_dedup_key(
    project_id: UUID,
    plugin: str,
    target: str,
    port: int | None = None,
    service: str | None = None,
    template_id: str | None = None,
    title: str | None = None,
) -> str:
    """
    Deterministic dedup key for findings.

    For nmap-based findings: hash(project + target + port + service).
    For nuclei/nikto findings: hash(project + target + template_id).
    Fallback: hash(project + plugin + title).
    """
    parts: list[str] = [str(project_id), plugin, target]
    if port is not None and service:
        parts.extend([str(port), service])
    elif template_id:
        parts.append(template_id)
    elif title:
        parts.append(title)
    raw = ":".join(parts)
    digest = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return f"correlated:{plugin}:{digest}"


_INSECURE_SERVICES: dict[str, Severity] = {
    "ftp": Severity.LOW,
    "telnet": Severity.MEDIUM,
    "rlogin": Severity.HIGH,
    "rexec": Severity.HIGH,
    "rsh": Severity.HIGH,
    "vnc": Severity.MEDIUM,
    "ms-wbt-server": Severity.MEDIUM,
    "netbios-ssn": Severity.MEDIUM,
    "microsoft-ds": Severity.MEDIUM,
}


class CorrelationService:
    """
    Processes normalized ToolResults and creates or updates Findings.

    - Deduplicates across multiple scans of the same target.
    - Merges tool_result_ids onto existing findings.
    - Creates new findings for patterns we recognise (insecure services
      from nmap, known CVEs from nuclei, etc.).
    """

    def __init__(self, finding_repository: FindingRepository) -> None:
        self._findings = finding_repository

    async def correlate(
        self,
        project_id: UUID,
        tool_results: list[ToolResult],
    ) -> list[Finding]:
        """
        Process a batch of tool results and return all findings
        (both newly created and updated existing ones).
        """
        created: list[Finding] = []

        for tr in tool_results:
            new_findings = await self._process_tool_result(project_id, tr)
            created.extend(new_findings)

        return created

    async def _process_tool_result(
        self,
        project_id: UUID,
        tool_result: ToolResult,
    ) -> list[Finding]:
        payload = tool_result.normalized_payload
        plugin = tool_result.plugin
        created: list[Finding] = []

        if plugin == "nmap":
            created.extend(
                await self._correlate_nmap(project_id, tool_result, payload)
            )
        elif plugin in ("nuclei", "nikto"):
            created.extend(
                await self._correlate_web_vuln(project_id, tool_result, payload, plugin)
            )

        return created

    async def _correlate_nmap(
        self,
        project_id: UUID,
        tool_result: ToolResult,
        payload: dict[str, object],
    ) -> list[Finding]:
        target = str(payload.get("target", ""))
        ports = payload.get("ports", [])
        created: list[Finding] = []

        if not isinstance(ports, list):
            return created

        for port_info in ports:
            if not isinstance(port_info, dict):
                continue
            if port_info.get("state") != "open":
                continue

            service = str(port_info.get("service", ""))
            port = port_info.get("port")
            if port is None:
                continue

            severity = _INSECURE_SERVICES.get(service.lower())
            if severity is None:
                continue

            dedup_key = make_dedup_key(
                project_id, "nmap", target, int(port), service
            )
            existing = await self._findings.get_by_dedup_key(project_id, dedup_key)
            if existing is not None:
                if tool_result.id not in existing.tool_result_ids:
                    existing.tool_result_ids.append(tool_result.id)
                continue

            version = port_info.get("version", "")
            finding = Finding(
                id=uuid4(),
                project_id=project_id,
                title=f"Insecure service: {service} on {target}:{port}",
                severity=severity,
                status=FindingStatus.OPEN,
                description=(
                    f"Service '{service}' detected on {target}:{port}"
                    + (f" ({version})" if version else "")
                    + " — associated with known security risks."
                ),
                dedup_key=dedup_key,
                tool_result_ids=[tool_result.id],
                created_at=datetime.now(UTC),
            )
            await self._findings.add(finding)
            created.append(finding)

        return created

    async def _correlate_web_vuln(
        self,
        project_id: UUID,
        tool_result: ToolResult,
        payload: dict[str, object],
        plugin: str,
    ) -> list[Finding]:
        target = str(payload.get("target", ""))
        vulnerabilities = payload.get("vulnerabilities", [])
        created: list[Finding] = []

        if not isinstance(vulnerabilities, list):
            return created

        for vuln in vulnerabilities:
            if not isinstance(vuln, dict):
                continue

            template_id = str(vuln.get("template_id", ""))
            title = str(vuln.get("title", f"{plugin} finding"))
            severity_str = str(vuln.get("severity", "info")).lower()
            try:
                severity = Severity(severity_str)
            except ValueError:
                severity = Severity.INFO

            dedup_key = make_dedup_key(
                project_id, plugin, target, template_id=template_id, title=title
            )
            existing = await self._findings.get_by_dedup_key(project_id, dedup_key)
            if existing is not None:
                if tool_result.id not in existing.tool_result_ids:
                    existing.tool_result_ids.append(tool_result.id)
                continue

            finding = Finding(
                id=uuid4(),
                project_id=project_id,
                title=title,
                severity=severity,
                status=FindingStatus.OPEN,
                description=vuln.get("description", ""),
                dedup_key=dedup_key,
                tool_result_ids=[tool_result.id],
                created_at=datetime.now(UTC),
            )
            await self._findings.add(finding)
            created.append(finding)

        return created
