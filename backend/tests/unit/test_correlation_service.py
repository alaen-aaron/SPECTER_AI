"""Unit tests for CorrelationService."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.application.correlation_service import CorrelationService, make_dedup_key
from app.domain.entities import ToolResult
from tests.fakes import FakeFindingRepository


def test_make_dedup_key_nmap():
    key = make_dedup_key(uuid4(), "nmap", "10.0.0.1", 22, "ssh")
    assert "nmap" in key
    assert key.startswith("correlated:")


def test_make_dedup_key_nuclei():
    key = make_dedup_key(uuid4(), "nuclei", "10.0.0.1", template_id="CVE-2021-1234")
    assert "nuclei" in key


def test_make_dedup_key_deterministic():
    pid = uuid4()
    k1 = make_dedup_key(pid, "nmap", "10.0.0.1", 80, "http")
    k2 = make_dedup_key(pid, "nmap", "10.0.0.1", 80, "http")
    assert k1 == k2


def test_make_dedup_key_different_targets():
    pid = uuid4()
    k1 = make_dedup_key(pid, "nmap", "10.0.0.1", 80, "http")
    k2 = make_dedup_key(pid, "nmap", "10.0.0.2", 80, "http")
    assert k1 != k2


@pytest.mark.asyncio
async def test_correlate_nmap_insecure_service():
    findings_repo = FakeFindingRepository()
    service = CorrelationService(findings_repo)

    pid = uuid4()
    tr = ToolResult(
        id=uuid4(),
        scan_id=uuid4(),
        plugin="nmap",
        target="10.0.0.1",
        normalized_payload={
            "target": "10.0.0.1",
            "ports": [
                {"port": 23, "state": "open", "service": "telnet", "protocol": "tcp", "version": ""}
            ],
        },
    )

    created = await service.correlate(pid, [tr])
    assert len(created) == 1
    assert "telnet" in created[0].title
    assert created[0].severity.value == "medium"


@pytest.mark.asyncio
async def test_correlate_nmap_skips_secure_services():
    findings_repo = FakeFindingRepository()
    service = CorrelationService(findings_repo)

    pid = uuid4()
    tr = ToolResult(
        id=uuid4(),
        scan_id=uuid4(),
        plugin="nmap",
        target="10.0.0.1",
        normalized_payload={
            "target": "10.0.0.1",
            "ports": [
                {
                    "port": 22, "state": "open", "service": "ssh",
                    "protocol": "tcp", "version": "OpenSSH",
                }
            ],
        },
    )

    created = await service.correlate(pid, [tr])
    assert len(created) == 0


@pytest.mark.asyncio
async def test_correlate_nmap_dedup():
    findings_repo = FakeFindingRepository()
    service = CorrelationService(findings_repo)

    pid = uuid4()
    tr1 = ToolResult(
        id=uuid4(),
        scan_id=uuid4(),
        plugin="nmap",
        target="10.0.0.1",
        normalized_payload={
            "target": "10.0.0.1",
            "ports": [
                {"port": 21, "state": "open", "service": "ftp", "protocol": "tcp", "version": ""}
            ],
        },
    )
    tr2 = ToolResult(
        id=uuid4(),
        scan_id=uuid4(),
        plugin="nmap",
        target="10.0.0.1",
        normalized_payload={
            "target": "10.0.0.1",
            "ports": [
                {"port": 21, "state": "open", "service": "ftp", "protocol": "tcp", "version": ""}
            ],
        },
    )

    created1 = await service.correlate(pid, [tr1])
    assert len(created1) == 1

    created2 = await service.correlate(pid, [tr2])
    assert len(created2) == 0  # deduplicated
    assert len(findings_repo._findings) == 1


@pytest.mark.asyncio
async def test_correlate_non_nmap_returns_empty():
    findings_repo = FakeFindingRepository()
    service = CorrelationService(findings_repo)

    pid = uuid4()
    tr = ToolResult(
        id=uuid4(),
        scan_id=uuid4(),
        plugin="echo",
        target="",
        normalized_payload={"message": "Hello"},
    )

    created = await service.correlate(pid, [tr])
    assert len(created) == 0


@pytest.mark.asyncio
async def test_correlate_nuclei_vulnerability():
    findings_repo = FakeFindingRepository()
    service = CorrelationService(findings_repo)

    pid = uuid4()
    tr = ToolResult(
        id=uuid4(),
        scan_id=uuid4(),
        plugin="nuclei",
        target="10.0.0.1",
        normalized_payload={
            "target": "10.0.0.1",
            "vulnerabilities": [
                {
                    "template_id": "CVE-2021-1234",
                    "title": "Test CVE",
                    "severity": "high",
                    "description": "A test vulnerability",
                }
            ],
        },
    )

    created = await service.correlate(pid, [tr])
    assert len(created) == 1
    assert created[0].severity.value == "high"
    assert "CVE" in created[0].title
