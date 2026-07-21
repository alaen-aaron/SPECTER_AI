"""
Ping output normalizer (Milestone 4A).

Parses standard `ping -c N` output into a structured payload:
  - host: the target hostname/IP
  - reachable: bool (any reply received)
  - packets_sent / packets_received / packet_loss_pct
  - rtt_min_ms / rtt_avg_ms / rtt_max_ms / rtt_mdev_ms
"""

from __future__ import annotations

import re
from typing import Any

_STATS_RE = re.compile(
    r"(\d+)\s+packets?\s+transmitted.*?"
    r"(\d+)\s+(?:packets?\s+)?received.*?"
    r"([\d.]+)%\s+packet\s+loss"
)
_RTT_RE = re.compile(
    r"(?:rtt|round-trip)\s+min/avg/max/(?:mdev|stddev)\s*=\s*"
    r"([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)"
)
_REPLY_RE = re.compile(r"(\d+)\s+bytes\s+from\s+([^:]+):")


class PingNormalizer:
    @property
    def plugin_name(self) -> str:
        return "ping"

    def normalize(
        self,
        raw_stdout: str,
        raw_stderr: str,
        plugin_config: dict[str, Any],
    ) -> dict[str, Any]:
        hostname = str(plugin_config.get("hostname", ""))
        reachable = False
        packets_sent = 0
        packets_received = 0
        packet_loss_pct = 100
        rtt: dict[str, float | None] = {
            "min_ms": None,
            "avg_ms": None,
            "max_ms": None,
            "mdev_ms": None,
        }
        reply_count = 0

        reply_match = _REPLY_RE.search(raw_stdout)
        if reply_match:
            reachable = True
            reply_count = len(_REPLY_RE.findall(raw_stdout))

        stats_match = _STATS_RE.search(raw_stdout)
        if stats_match:
            packets_sent = int(stats_match.group(1))
            packets_received = int(stats_match.group(2))
            packet_loss_pct = int(float(stats_match.group(3)))

        rtt_match = _RTT_RE.search(raw_stdout)
        if rtt_match:
            rtt["min_ms"] = float(rtt_match.group(1))
            rtt["avg_ms"] = float(rtt_match.group(2))
            rtt["max_ms"] = float(rtt_match.group(3))
            rtt["mdev_ms"] = float(rtt_match.group(4))

        return {
            "host": hostname,
            "reachable": reachable,
            "reply_count": reply_count,
            "packets_sent": packets_sent,
            "packets_received": packets_received,
            "packet_loss_pct": packet_loss_pct,
            "rtt": rtt,
        }
