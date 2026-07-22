from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from app.domain.entities import Finding, ToolResult
from app.domain.exceptions import FindingNotFoundError
from app.domain.repositories import AssetRepository, FindingRepository
from app.domain.value_objects import FindingStatus, Severity

if TYPE_CHECKING:
    from app.application.graph_service import GraphService

_VULNERABLE_SERVICE_PATTERNS: dict[str, Severity] = {
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


def _severity_for_service(service: str) -> Severity | None:
    return _VULNERABLE_SERVICE_PATTERNS.get(service.lower())


def _make_dedup_key(project_id: UUID, target: str, port: int, service: str) -> str:
    return f"finding:{project_id}:nmap:{target}:{port}/{service}"


class FindingService:
    def __init__(
        self,
        finding_repository: FindingRepository,
        asset_repository: AssetRepository,
        graph_service: GraphService | None = None,
    ) -> None:
        self._findings = finding_repository
        self._assets = asset_repository
        self._graph = graph_service

    async def list_for_project(
        self,
        project_id: UUID,
        severity: Severity | None = None,
        limit: int = 20,
        cursor: datetime | None = None,
    ) -> list[Finding]:
        return await self._findings.list_for_project(
            project_id, severity, limit=limit, cursor=cursor
        )

    async def get(self, finding_id: UUID) -> Finding:
        finding = await self._findings.get(finding_id)
        if finding is None:
            raise FindingNotFoundError(finding_id)
        return finding

    async def create_from_tool_result(
        self,
        project_id: UUID,
        tool_result: ToolResult,
        asset_id: UUID | None = None,
    ) -> list[Finding]:
        payload = tool_result.normalized_payload
        if tool_result.plugin != "nmap":
            return []

        target = str(payload.get("target", ""))
        ports = payload.get("ports", [])
        now = datetime.now(UTC)
        created: list[Finding] = []

        if not isinstance(ports, list):
            return []

        for port_info in ports:
            if not isinstance(port_info, dict):
                continue
            if port_info.get("state") != "open":
                continue

            service = str(port_info.get("service", ""))
            port = port_info.get("port")
            if port is None:
                continue

            severity = _severity_for_service(service)
            if severity is None:
                continue

            dedup_key = _make_dedup_key(project_id, target, int(port), service)
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
                asset_id=asset_id,
                dedup_key=dedup_key,
                tool_result_ids=[tool_result.id],
                created_at=now,
            )
            await self._findings.add(finding)
            created.append(finding)

        if self._graph is not None and created:
            await self._project_findings_to_graph(project_id, created)

        return created

    async def _project_findings_to_graph(
        self, project_id: UUID, findings: list[Finding]
    ) -> None:
        """Create graph nodes for findings and wire VULNERABLE_TO edges to assets."""
        assert self._graph is not None

        for finding in findings:
            node = await self._graph.upsert_finding_node(
                project_id,
                finding.id,
                finding.title,
                severity=finding.severity.value,
            )
            if finding.asset_id is not None:
                asset_node = await self._graph.find_node_by_source(
                    project_id, "assets", finding.asset_id
                )
                if asset_node is not None:
                    await self._graph.add_edge(
                        project_id,
                        node.id,
                        asset_node.id,
                        "vulnerable_to",
                    )

    async def update_status(self, finding_id: UUID, status: FindingStatus) -> Finding:
        finding = await self.get(finding_id)
        await self._findings.update_status(finding_id, status)
        finding.status = status
        return finding
