"""
Correlation Engine (Phase 2/3, M7.3).

Two responsibilities, both consuming ONLY structured normalized
payloads produced by our normalizers — never raw text, never LLM
output:

1. FINDINGS (M3): merge duplicate findings across tools using
   deterministic dedup keys; M7.3 extends web-vuln keys with the
   matched path so /metrics and /login stay distinct.

2. SURFACE (M7.3 Phase 3): resolve every observation onto the canonical
   identity layer from Phase 2 —

       Host → Service → Technology → Finding

   Web observations (HTTPX/WhatWeb/Nuclei matched_url) resolve to the
   SAME Service asset as nmap via ``identity_key`` (no substring
   matching), technologies become TECHNOLOGY assets + USES edges, and
   web findings link deterministically to their service with structured
   enrichment.

Everything here is deterministic and idempotent: repeated correlation
of the same ToolResults converges (DB/app uniqueness on identity keys,
observation pairs, dedup keys, graph node/edge identity).
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import structlog

from app.domain.asset_identity import (
    _slug,
    normalize_host,
    service_identity,
    split_url,
    technology_identity,
)
from app.domain.entities import (
    Asset,
    AssetObservation,
    Finding,
    ToolResult,
)
from app.domain.repositories import (
    AssetObservationRepository,
    AssetRepository,
    FindingRepository,
)
from app.domain.value_objects import (
    AssetType,
    FindingStatus,
    GraphEdgeType,
    Severity,
)

if TYPE_CHECKING:
    from app.application.graph_service import GraphService

logger = structlog.get_logger(__name__)
_stdlog = logging.getLogger(__name__)


def make_dedup_key(
    project_id: UUID,
    plugin: str,
    target: str,
    port: int | None = None,
    service: str | None = None,
    template_id: str | None = None,
    title: str | None = None,
    path: str | None = None,
) -> str:
    """
    Deterministic dedup key for findings.

    For nmap-based findings: hash(project + target + port + service).
    For nuclei/nikto findings: hash(project + target + template_id[+path]).
    Fallback: hash(project + plugin + title).

    M7.3 Phase 3: `path` is appended ONLY when a caller passes it
    explicitly (web findings whose matched path is material), so legacy
    keys remain byte-identical.
    """
    parts: list[str] = [str(project_id), plugin, target]
    if port is not None and service:
        parts.extend([str(port), service])
    elif template_id:
        parts.append(template_id)
        if path:
            parts.append(path)
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
    Processes normalized ToolResults into the unified security model.

    - Deduplicates across multiple scans of the same target.
    - Merges tool_result_ids onto existing findings.
    - Creates new findings for recognised patterns (insecure services
      from nmap, known CVEs from nuclei, etc.).
    - M7.3: resolves web observations onto canonical Host/Service
      identities, materialises observed TECHNOLOGY assets with USES
      edges, and links web findings deterministically to their service
      with structured enrichment.

    All dependencies except the finding repository are optional so the
    legacy constructor keeps working (tests, minimal deployments).
    """

    def __init__(
        self,
        finding_repository: FindingRepository,
        asset_repository: AssetRepository | None = None,
        observation_repository: AssetObservationRepository | None = None,
        graph_service: GraphService | None = None,
    ) -> None:
        self._findings = finding_repository
        self._assets = asset_repository
        self._observations = observation_repository
        self._graph = graph_service

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    async def correlate(
        self,
        project_id: UUID,
        tool_results: list[ToolResult],
    ) -> list[Finding]:
        """
        Process a batch of tool results and return all findings
        (both newly created and updated existing ones).

        Resolution order per ToolResult is deterministic:
        Host → Service → Technology → Finding. Cross-tool convergence
        does NOT depend on execution order because everything resolves
        through identity keys.
        """
        logger.info(
            "correlation_started",
            project_id=str(project_id),
            tool_result_count=len(tool_results),
        )
        created: list[Finding] = []

        for tr in tool_results:
            # --- M7.3 Phase 3 surface correlation (non-fatal) -----------
            if self._assets is not None:
                try:
                    await self._correlate_surface(project_id, tr)
                except Exception as exc:  # noqa: BLE001 — never fail a scan
                    logger.warning(
                        "surface_correlation_failed",
                        tool_result_id=str(tr.id),
                        plugin=tr.plugin,
                        error=str(exc),
                        error_type=type(exc).__name__,
                    )

            new_findings = await self._process_tool_result(project_id, tr)
            created.extend(new_findings)

        logger.info(
            "correlation_completed",
            project_id=str(project_id),
            findings_created=len(created),
        )
        return created

    # ------------------------------------------------------------------
    # M7.3 Phase 3: canonical surface correlation
    # ------------------------------------------------------------------

    async def _record_observation(
        self,
        project_id: UUID,
        asset: Asset,
        tool_result: ToolResult,
        details: dict[str, object],
    ) -> None:
        """Idempotent: skip if (tool_result, asset) observation already exists."""
        if self._observations is None:
            return
        if await self._observations.exists_for(tool_result.id, asset.id):
            return
        await self._observations.add(
            AssetObservation(
                id=uuid4(),
                project_id=project_id,
                asset_id=asset.id,
                tool_result_id=tool_result.id,
                scan_id=tool_result.scan_id,
                plugin=tool_result.plugin,
                observed_at=datetime.now(UTC),
                details=dict(details),
            )
        )

    async def _resolve_host(
        self,
        project_id: UUID,
        host: str,
        tool_result: ToolResult,
    ) -> Asset | None:
        """Resolve-or-create the canonical HOST asset for *host*."""
        assert self._assets is not None
        identity = normalize_host(host)
        if not identity:
            return None
        existing = await self._assets.get_by_identity(
            project_id, AssetType.HOST, identity
        )
        if existing is not None:
            await self._record_observation(
                project_id, existing, tool_result, {"role": "host"}
            )
            return existing
        now = datetime.now(UTC)
        asset = Asset(
            id=uuid4(),
            project_id=project_id,
            asset_type=AssetType.HOST,
            value=identity,
            first_seen=now,
            last_seen=now,
            source_scan_id=tool_result.scan_id,
            metadata={"source": "correlation"},
            created_at=now,
            identity_key=identity,
        )
        created = await self._assets.upsert(asset)
        if self._graph is not None:
            with contextlib.suppress(Exception):
                await self._graph.upsert_asset_node(
                    project_id,
                    created.id,
                    created.value,
                    asset_type=created.asset_type.value,
                )
        await self._record_observation(
            project_id, created, tool_result, {"role": "host"}
        )
        return created

    async def _resolve_service(
        self,
        project_id: UUID,
        host: str,
        port: int,
        transport: str = "tcp",
        scheme: str | None = None,
        display_value: str | None = None,
        tool_result: ToolResult | None = None,
    ) -> Asset | None:
        """
        Resolve-or-create the canonical SERVICE asset. This is the
        convergence point: nmap's ``ppp?://h:p/tcp``, HTTPX's
        ``http://h:p`` and a Nuclei matched URL all collapse here.

        The scheme is NEVER part of the identity key — it is metadata
        only.  This ensures nmap (which doesn't know the scheme) and
        httpx (which does) always resolve to the same asset.
        """
        assert self._assets is not None
        identity = service_identity(host, port, transport)
        existing = await self._assets.get_by_identity(
            project_id, AssetType.SERVICE, identity
        )
        if existing is not None:
            if tool_result is not None:
                await self._record_observation(
                    project_id, existing, tool_result, {"role": "service"}
                )
            return existing
        now = datetime.now(UTC)
        asset = Asset(
            id=uuid4(),
            project_id=project_id,
            asset_type=AssetType.SERVICE,
            value=display_value or identity,
            first_seen=now,
            last_seen=now,
            source_scan_id=tool_result.scan_id if tool_result else None,
            metadata={
                "port": port,
                "protocol": transport,
                **({"scheme": scheme} if scheme else {}),
                "source": "correlation",
            },
            created_at=now,
            identity_key=identity,
        )
        created = await self._assets.upsert(asset)
        if self._graph is not None:
            with contextlib.suppress(Exception):
                await self._graph.upsert_asset_node(
                    project_id,
                    created.id,
                    created.value,
                    asset_type=created.asset_type.value,
                )
        if tool_result is not None:
            await self._record_observation(
                project_id, created, tool_result, {"role": "service"}
            )
        return created

    async def _link_service_to_host(
        self,
        project_id: UUID,
        service: Asset,
        host: Asset | None,
    ) -> None:
        """Project host —[HOSTS]→ service into the graph."""
        if self._graph is None or host is None:
            return
        try:
            svc_node = await self._graph.find_node_by_source(
                project_id, "assets", service.id
            )
            if svc_node is None:
                svc_node = await self._graph.upsert_asset_node(
                    project_id,
                    service.id,
                    service.value,
                    asset_type=service.asset_type.value,
                )
            host_node = await self._graph.find_node_by_source(
                project_id, "assets", host.id
            )
            if host_node is not None and svc_node is not None:
                await self._graph.add_edge(
                    project_id, host_node.id, svc_node.id, GraphEdgeType.HOSTS
                )
        except Exception as exc:  # noqa: BLE001 — graph is best-effort here
            logger.warning("hosts_edge_failed", error=str(exc))

    async def _resolve_technology(
        self,
        project_id: UUID,
        name: str,
        service_asset: Asset,
        versions: list[str] | None = None,
        tool_result: ToolResult | None = None,
    ) -> Asset | None:
        """Resolve-or-create TECHNOLOGY scoped to the service context."""
        assert self._assets is not None
        slug = _slug(name)
        if not slug:
            return None
        identity = technology_identity(name, service_asset.identity_key or "")
        existing = await self._assets.get_by_identity(
            project_id, AssetType.TECHNOLOGY, identity
        )
        if existing is not None:
            if tool_result is not None:
                await self._record_observation(
                    project_id, existing, tool_result, {"role": "technology"}
                )
            return existing
        now = datetime.now(UTC)
        asset = Asset(
            id=uuid4(),
            project_id=project_id,
            asset_type=AssetType.TECHNOLOGY,
            value=name.strip(),
            first_seen=now,
            last_seen=now,
            source_scan_id=tool_result.scan_id if tool_result else None,
            metadata={
                "slug": slug,
                **({"versions": versions} if versions else {}),
                "service_identity": service_asset.identity_key,
                "source": "correlation",
            },
            created_at=now,
            identity_key=identity,
        )
        created = await self._assets.upsert(asset)
        if tool_result is not None:
            await self._record_observation(
                project_id, created, tool_result, {"role": "technology"}
            )
        return created

    async def _project_technology_uses_edge(
        self,
        project_id: UUID,
        service_asset: Asset,
        tech_asset: Asset,
    ) -> None:
        """project service —[USES]→ technology (idempotent upsert)."""
        if self._graph is None:
            return
        try:
            svc_node = await self._graph.find_node_by_source(
                project_id, "assets", service_asset.id
            )
            tech_node = await self._graph.upsert_technology_node(
                project_id,
                tech_asset.id,
                tech_asset.value,
                slug=tech_asset.metadata.get("slug", ""),
                service_identity=tech_asset.identity_key,
            )
            if svc_node is None:
                svc_node = await self._graph.upsert_asset_node(
                    project_id,
                    service_asset.id,
                    service_asset.value,
                    asset_type=service_asset.asset_type.value,
                )
            await self._graph.add_edge(
                project_id, svc_node.id, tech_node.id, GraphEdgeType.USES
            )
        except Exception as exc:  # noqa: BLE001 — graph is best-effort here
            logger.warning("uses_edge_failed", error=str(exc))

    def _extract_web_targets(self, payload: dict[str, object]) -> list[str]:
        """URLs observed by httpx/whatweb/nuclei payloads."""
        urls: list[str] = []
        results = payload.get("results")
        if isinstance(results, list):
            for r in results:
                if isinstance(r, dict) and isinstance(r.get("url"), str):
                    urls.append(r["url"])
        vulns = payload.get("vulnerabilities")
        if isinstance(vulns, list):
            for v in vulns:
                if isinstance(v, dict) and isinstance(v.get("matched_url"), str):
                    urls.append(v["matched_url"])
        target = payload.get("target")
        if isinstance(target, str) and "://" in target:
            urls.append(target)
        return urls

    async def _correlate_surface(
        self, project_id: UUID, tr: ToolResult
    ) -> None:
        """
        Host -> Service -> Technology resolution for one ToolResult.

        Handles nmap (host+ports), httpx/whatweb (results[]) and nuclei
        (vulnerabilities[].matched_url).  Order-independent: every path
        resolves through identity keys.
        """
        assert self._assets is not None
        payload = tr.normalized_payload
        plugin = tr.plugin

        # --- nmap: host + explicit ports --------------------------------
        if plugin == "nmap":
            target = str(payload.get("target", ""))
            if not target:
                return
            host_asset = await self._resolve_host(project_id, target, tr)
            ports = payload.get("ports", [])
            if not isinstance(ports, list):
                return
            for p in ports:
                if not isinstance(p, dict) or p.get("state") != "open":
                    continue
                port, proto = p.get("port"), p.get("protocol", "tcp")
                if not isinstance(port, int):
                    continue
                svc = await self._resolve_service(
                    project_id,
                    target,
                    port,
                    transport=str(proto),
                    display_value=(
                        f"{p.get('service', 'unknown')}://{target}:{port}/{proto}"
                    ),
                    tool_result=tr,
                )
                if svc is not None:
                    await self._link_service_to_host(project_id, svc, host_asset)

        # --- httpx / whatweb / nuclei: structured web observations -----
        elif plugin in ("httpx", "whatweb", "nuclei"):
            for url in self._extract_web_targets(payload):
                scheme, host, port = split_url(url)
                if not scheme or not host or not port:
                    continue
                host_asset = await self._resolve_host(project_id, host, tr)
                svc = await self._resolve_service(
                    project_id,
                    host,
                    port,
                    transport="tcp",
                    scheme=scheme,
                    display_value=url,
                    tool_result=tr,
                )
                if svc is None:
                    continue
                await self._link_service_to_host(project_id, svc, host_asset)

                # Technologies from real observations only.
                tech_entries: list[tuple[str, list[str]]] = []
                results = payload.get("results")
                if plugin in ("httpx", "whatweb") and isinstance(results, list):
                    for r in results:
                        if (
                            isinstance(r, dict)
                            and r.get("url") == url
                            and isinstance(r.get("technologies"), list)
                        ):
                            for t in r["technologies"]:
                                if isinstance(t, dict) and t.get("name"):
                                    versions = t.get("versions") or []
                                    tech_entries.append(
                                        (
                                            str(t["name"]),
                                            [str(v) for v in versions]
                                            if isinstance(versions, list)
                                            else [],
                                        )
                                    )
                                elif isinstance(t, str):
                                    tech_entries.append((t, []))
                seen_tech: set[str] = set()
                for tech_name, versions in tech_entries:
                    slug_key = _slug(tech_name)
                    if not slug_key or slug_key in seen_tech:
                        continue
                    seen_tech.add(slug_key)
                    tech = await self._resolve_technology(
                        project_id, tech_name, svc, versions, tool_result=tr
                    )
                    if tech is not None:
                        await self._project_technology_uses_edge(
                            project_id, svc, tech
                        )

    # ------------------------------------------------------------------
    # Findings handlers
    # ------------------------------------------------------------------

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

            # M7.3: extract matched_url for path-sensitive dedup + enrichment
            matched_url = vuln.get("matched_url")
            matched_url_str = str(matched_url) if isinstance(matched_url, str) else ""
            matched_path = ""
            if matched_url_str:
                try:
                    from urllib.parse import urlsplit as _urlsplit

                    _parts = _urlsplit(matched_url_str)
                    matched_path = _parts.path or ""
                except Exception:  # noqa: BLE001
                    matched_path = ""

            # Path-sensitive dedup: /metrics and /login are different vulns
            dedup_key = make_dedup_key(
                project_id,
                plugin,
                target,
                template_id=template_id,
                title=title,
                path=matched_path if matched_path and matched_path != "/" else None,
            )
            existing = await self._findings.get_by_dedup_key(project_id, dedup_key)
            if existing is not None:
                if tool_result.id not in existing.tool_result_ids:
                    existing.tool_result_ids.append(tool_result.id)
                continue

            # --- M7.3: deterministic finding-to-service linking --------
            asset_id: UUID | None = None
            enrichment: dict[str, object] = {}
            service_identity_val = ""
            if matched_url_str and self._assets is not None:
                scheme, host, port = split_url(matched_url_str)
                if scheme and host and port:
                    identity = service_identity(host, port, "tcp")
                    svc_asset = await self._assets.get_by_identity(
                        project_id, AssetType.SERVICE, identity
                    )
                    if svc_asset is not None:
                        asset_id = svc_asset.id
                        service_identity_val = identity

                    # Collect technologies from resolved service
                    techs: list[str] = []
                    if asset_id is not None:
                        svc_obs = (
                            await self._observations.list_for_asset(asset_id)
                            if self._observations is not None
                            else []
                        )
                        for obs in svc_obs:
                            details = obs.details or {}
                            if details.get("role") == "technology":
                                techs.append(str(details.get("value", "")))
                        # Also read tech assets linked to service
                        if self._assets is not None and svc_asset is not None:
                            all_assets = await self._assets.list_for_project(
                                project_id, AssetType.TECHNOLOGY, limit=500
                            )
                            svc_id_str = str(svc_asset.identity_key or "")
                            for a in all_assets:
                                if (
                                    (a.metadata or {}).get("service_identity") == svc_id_str
                                    and a.value
                                    and a.value not in techs
                                ):
                                    techs.append(a.value)

                    confidence = "exact" if service_identity_val else "none"
                    enrichment = {
                        "url": matched_url_str,
                        "matched_path": matched_path,
                        "technologies": techs,
                        "service_identity": service_identity_val,
                        "confidence": confidence,
                    }

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
                asset_id=asset_id,
                enrichment=enrichment if enrichment else None,
            )
            await self._findings.add(finding)
            created.append(finding)

        return created
