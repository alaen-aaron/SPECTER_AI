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

import structlog

from app.domain.entities import Finding, ToolResult
from app.domain.repositories import FindingRepository
from app.domain.value_objects import FindingStatus, Severity

logger = structlog.get_logger(__name__)


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
        logger.info(
            "correlation_started",
            project_id=str(project_id),
            tool_result_count=len(tool_results),
        )
        created: list[Finding] = []

        for tr in tool_results:
            new_findings = await self._process_tool_result(project_id, tr)
            created.extend(new_findings)

        logger.info(
            "correlation_completed",
            project_id=str(project_id),
            findings_created=len(created),
        )
        return created

    async def _process_tool_result(
        self,
        project_id: UUID,
        tool_result: ToolResult,
    ) -> list[Finding]:
        payload = tool_result.normalized_payload
        plugin = tool_result.plugin
        created: list[Finding] = []

        logger.info(
            "correlation_processing_tool_result",
            tool_result_id=str(tool_result.id),
            plugin=plugin,
            payload_keys=list(payload.keys()),
            has_ports=bool(payload.get("ports")),
        )

        if plugin == "nmap":
            created.extend(
                await self._correlate_nmap(project_id, tool_result, payload)
            )
        elif plugin in ("nuclei", "nikto"):
            created.extend(
                await self._correlate_web_vuln(project_id, tool_result, payload, plugin)
            )
        else:
            logger.info(
                "correlation_no_handler_for_plugin",
                plugin=plugin,
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

        logger.info(
            "nmap_correlation_start",
            target=target,
            ports_type=type(ports).__name__,
            ports_count=len(ports) if isinstance(ports, list) else 0,
        )

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

            insecure_severity = _INSECURE_SERVICES.get(service.lower())
            if insecure_severity is not None:
                title = f"Insecure service: {service} on {target}:{port}"
                severity = insecure_severity
                description = (
                    f"Service '{service}' detected on {target}:{port}"
                    + (f" ({version})" if (version := port_info.get("version", "")) else "")
                    + " — associated with known security risks."
                )
            else:
                title = f"Open port: {service} on {target}:{port}"
                severity = Severity.INFO
                version = port_info.get("version", "")
                description = (
                    f"Service '{service}' detected on {target}:{port}"
                    + (f" ({version})" if version else "")
                    + " — open port identified during network scan."
                )

            dedup_key = make_dedup_key(
                project_id, "nmap", target, int(port), service
            )
            existing = await self._findings.get_by_dedup_key(project_id, dedup_key)
            if existing is not None:
                logger.info(
                    "nmap_finding_deduplicated",
                    dedup_key=dedup_key,
                    existing_finding_id=str(existing.id),
                )
                if tool_result.id not in existing.tool_result_ids:
                    existing.tool_result_ids.append(tool_result.id)
                continue

            finding = Finding(
                id=uuid4(),
                project_id=project_id,
                title=title,
                severity=severity,
                status=FindingStatus.OPEN,
                description=description,
                dedup_key=dedup_key,
                tool_result_ids=[tool_result.id],
                created_at=datetime.now(UTC),
            )
            await self._findings.add(finding)
            created.append(finding)
            logger.info(
                "nmap_finding_created",
                finding_id=str(finding.id),
                title=title,
                severity=severity.value,
            )

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
