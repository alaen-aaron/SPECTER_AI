"""
Unit tests for tool output normalizers (Milestone 4A).

PingNormalizer and NmapNormalizer parse raw stdout into structured
payloads; NormalizerRegistry maps plugin names to normalizer instances.
All tests are pure-logic — no subprocess or I/O involved.
"""

from __future__ import annotations

from app.plugins.normalizer_registry import NormalizerRegistry
from app.plugins.normalizers.nmap_normalizer import NmapNormalizer
from app.plugins.normalizers.ping_normalizer import PingNormalizer

# ---------------------------------------------------------------------------
# Sample outputs
# ---------------------------------------------------------------------------

_PING_SUCCESS = """\
PING 127.0.0.1 (127.0.0.1): 56 data bytes
64 bytes from 127.0.0.1: icmp_seq=0 ttl=64 time=0.067 ms
64 bytes from 127.0.0.1: icmp_seq=1 ttl=64 time=0.082 ms
64 bytes from 127.0.0.1: icmp_seq=2 ttl=64 time=0.075 ms

--- 127.0.0.1 ping statistics ---
3 packets transmitted, 3 packets received, 0.0% packet loss
round-trip min/avg/max/stddev = 0.067/0.075/0.082/0.006 ms
"""

_PING_UNREACHABLE = """\
PING 10.255.255.1 (10.255.255.1): 56 data bytes

--- 10.255.255.1 ping statistics ---
3 packets transmitted, 0 packets received, 100.0% packet loss
"""

_PING_PARTIAL = """\
PING 192.168.1.1 (192.168.1.1): 56 data bytes
64 bytes from 192.168.1.1: icmp_seq=0 ttl=64 time=1.234 ms

--- 192.168.1.1 ping statistics ---
2 packets transmitted, 1 packets received, 50.0% packet loss
"""

_NMAP_MULTI_PORT = """\
Nmap scan report for 10.10.10.5
Host is up (0.0012s latency).
22/tcp   open    ssh        OpenSSH 8.9p1 Ubuntu 3ubuntu0.4
80/tcp   open    http       nginx 1.18.0 (Ubuntu)
443/tcp  closed  https
8080/tcp filtered http-proxy
9999/tcp open    http       Apache httpd 2.4.52

Nmap done: 1 IP address (1 host up) scanned in 10.23 seconds
"""

_NMAP_HOST_DOWN = """\
Nmap scan report for 10.255.255.1
Host seems down. If it is really up, but blocking our ping probes, try -Pn
Nmap done: 1 IP address (0 hosts up) scanned in 2.15 seconds
"""

_NMAP_HOSTNAME_TARGET = """\
Nmap scan report for scanme.nmap.org (45.33.32.156)
Host is up (0.042s latency).
22/tcp open ssh OpenSSH 6.6.1p1

Nmap done: 1 IP address (1 host up) scanned in 5.10 seconds
"""


# ---------------------------------------------------------------------------
# PingNormalizer
# ---------------------------------------------------------------------------


def test_ping_normalizer_plugin_name() -> None:
    assert PingNormalizer().plugin_name == "ping"


def test_ping_normalizer_reachable_host() -> None:
    result = PingNormalizer().normalize(
        _PING_SUCCESS, "", {"hostname": "127.0.0.1"}
    )

    assert result["host"] == "127.0.0.1"
    assert result["reachable"] is True
    assert result["packets_sent"] == 3
    assert result["packets_received"] == 3
    assert result["packet_loss_pct"] == 0
    assert result["reply_count"] == 3
    assert result["rtt"]["min_ms"] == 0.067
    assert result["rtt"]["avg_ms"] == 0.075
    assert result["rtt"]["max_ms"] == 0.082
    assert result["rtt"]["mdev_ms"] == 0.006


def test_ping_normalizer_unreachable_host() -> None:
    result = PingNormalizer().normalize(
        _PING_UNREACHABLE, "", {"hostname": "10.255.255.1"}
    )

    assert result["host"] == "10.255.255.1"
    assert result["reachable"] is False
    assert result["packets_sent"] == 3
    assert result["packets_received"] == 0
    assert result["packet_loss_pct"] == 100
    assert result["reply_count"] == 0
    assert result["rtt"]["min_ms"] is None
    assert result["rtt"]["avg_ms"] is None
    assert result["rtt"]["max_ms"] is None
    assert result["rtt"]["mdev_ms"] is None


def test_ping_normalizer_empty_output() -> None:
    result = PingNormalizer().normalize("", "", {"hostname": ""})

    assert result["reachable"] is False
    assert result["packets_sent"] == 0
    assert result["packets_received"] == 0
    assert result["packet_loss_pct"] == 100
    assert result["reply_count"] == 0
    assert result["rtt"]["min_ms"] is None
    assert result["rtt"]["avg_ms"] is None
    assert result["rtt"]["max_ms"] is None
    assert result["rtt"]["mdev_ms"] is None


def test_ping_normalizer_partial_output() -> None:
    result = PingNormalizer().normalize(
        _PING_PARTIAL, "", {"hostname": "192.168.1.1"}
    )

    assert result["host"] == "192.168.1.1"
    assert result["reachable"] is True
    assert result["packets_sent"] == 2
    assert result["packets_received"] == 1
    assert result["packet_loss_pct"] == 50
    assert result["reply_count"] == 1
    assert result["rtt"]["min_ms"] is None


# ---------------------------------------------------------------------------
# NmapNormalizer
# ---------------------------------------------------------------------------


def test_nmap_normalizer_plugin_name() -> None:
    assert NmapNormalizer().plugin_name == "nmap"


def test_nmap_normalizer_multi_port_output() -> None:
    result = NmapNormalizer().normalize(
        _NMAP_MULTI_PORT, "", {"target": "10.10.10.5"}
    )

    assert result["target"] == "10.10.10.5"
    assert result["host_up"] is True
    assert len(result["ports"]) == 5
    assert result["open_port_count"] == 3
    assert result["closed_port_count"] == 1
    assert result["filtered_port_count"] == 1
    assert result["total_ports_scanned"] == 5

    port_numbers = [p["port"] for p in result["ports"]]
    assert port_numbers == [22, 80, 443, 8080, 9999]

    port_22 = result["ports"][0]
    assert port_22["state"] == "open"
    assert port_22["service"] == "ssh"
    assert port_22["version"] == "OpenSSH 8.9p1 Ubuntu 3ubuntu0.4"

    port_443 = result["ports"][2]
    assert port_443["state"] == "closed"
    assert port_443["version"] == ""

    port_8080 = result["ports"][3]
    assert port_8080["state"] == "filtered"


def test_nmap_normalizer_host_down() -> None:
    result = NmapNormalizer().normalize(
        _NMAP_HOST_DOWN, "", {"target": "10.255.255.1"}
    )

    assert result["host_up"] is False
    assert result["ports"] == []
    assert result["open_port_count"] == 0
    assert result["closed_port_count"] == 0
    assert result["filtered_port_count"] == 0
    assert result["total_ports_scanned"] == 0


def test_nmap_normalizer_empty_output() -> None:
    result = NmapNormalizer().normalize("", "", {"target": ""})

    assert result["target"] == ""
    assert result["host_up"] is False
    assert result["ports"] == []
    assert result["open_port_count"] == 0
    assert result["closed_port_count"] == 0
    assert result["filtered_port_count"] == 0
    assert result["total_ports_scanned"] == 0


def test_nmap_normalizer_extracts_target_from_output() -> None:
    result = NmapNormalizer().normalize(
        _NMAP_MULTI_PORT, "", {"target": "old-value"}
    )

    assert result["target"] == "10.10.10.5"


def test_nmap_normalizer_version_info_parsed() -> None:
    result = NmapNormalizer().normalize(
        _NMAP_MULTI_PORT, "", {"target": "10.10.10.5"}
    )

    openssh = result["ports"][0]
    assert "OpenSSH" in openssh["version"]
    assert "8.9p1" in openssh["version"]

    nginx = result["ports"][1]
    assert "nginx" in nginx["version"]
    assert "1.18.0" in nginx["version"]

    apache = result["ports"][4]
    assert "Apache" in apache["version"]
    assert "2.4.52" in apache["version"]


# ---------------------------------------------------------------------------
# NormalizerRegistry
# ---------------------------------------------------------------------------


def test_registry_register_and_get() -> None:
    registry = NormalizerRegistry()
    normalizer = PingNormalizer()
    registry.register(normalizer)

    assert registry.get("ping") is normalizer


def test_registry_get_unknown_returns_none() -> None:
    registry = NormalizerRegistry()
    assert registry.get("nonexistent") is None


def test_registry_list_returns_all() -> None:
    registry = NormalizerRegistry()
    ping = PingNormalizer()
    nmap = NmapNormalizer()
    registry.register(ping)
    registry.register(nmap)

    all_normalizers = registry.list()
    assert len(all_normalizers) == 2
    assert ping in all_normalizers
    assert nmap in all_normalizers
