"""
Nmap output normalizer (Milestone 4A).

Parses standard nmap stdout into a structured payload:
  - target: the scanned target
  - ports: list of {port, protocol, state, service, version}
  - open_port_count / filtered_port_count / closed_port_count
  - host_up: bool
  - scan_info: dict with scan flags used
"""

from __future__ import annotations

import logging
import re
from typing import Any

_log = logging.getLogger(__name__)

# Matches lines like: 22/tcp open ssh OpenSSH 8.9p1
_PORT_LINE_RE = re.compile(
    r"^(\d+)/(tcp|udp)\s+(open|closed|filtered)\s+(\S+)(?:[^\S\n]+([^\n]*))?$",
    re.MULTILINE,
)

# Matches: Nmap scan report for 10.10.10.5 (or "Nmap scan report for hostname (ip)")
_TARGET_RE = re.compile(r"Nmap scan report for\s+(?:\S+\s+\()?([^\s)]+)\)?")

# Matches: 4 ports scanned (or "256 ports scanned")
_SCANNED_RE = re.compile(r"(\d+)\s+ports?\s+scanned")

# Matches: Host is up (0.0012s latency)
_HOST_UP_RE = re.compile(r"Host is up")


class NmapNormalizer:
    @property
    def plugin_name(self) -> str:
        return "nmap"

    def normalize(
        self,
        raw_stdout: str,
        raw_stderr: str,
        plugin_config: dict[str, Any],
    ) -> dict[str, Any]:
        _log.warning(
            "NMAP_NORMALIZE_RAW_STDOUT length=%d, repr_first_500=%r",
            len(raw_stdout),
            raw_stdout[:500],
        )
        if raw_stderr:
            _log.warning(
                "NMAP_NORMALIZE_RAW_STDERR length=%d, repr_first_500=%r",
                len(raw_stderr),
                raw_stderr[:500],
            )

        target = str(plugin_config.get("target", ""))

        # Try to extract target from output if config doesn't have it
        target_match = _TARGET_RE.search(raw_stdout)
        if target_match:
            target = target_match.group(1)

        host_up = bool(_HOST_UP_RE.search(raw_stdout))

        ports: list[dict[str, object]] = []
        open_count = 0
        closed_count = 0
        filtered_count = 0

        regex_matches = list(_PORT_LINE_RE.finditer(raw_stdout))
        _log.warning(
            "NMAP_NORMALIZE_REGEX matches=%d", len(regex_matches)
        )

        for match in regex_matches:
            port_num = int(match.group(1))
            protocol = match.group(2)
            state = match.group(3)
            service = match.group(4)
            version_info = (match.group(5) or "").strip()

            ports.append(
                {
                    "port": port_num,
                    "protocol": protocol,
                    "state": state,
                    "service": service,
                    "version": version_info,
                }
            )

            if state == "open":
                open_count += 1
            elif state == "closed":
                closed_count += 1
            elif state == "filtered":
                filtered_count += 1

        result = {
            "target": target,
            "host_up": host_up,
            "ports": ports,
            "open_port_count": open_count,
            "closed_port_count": closed_count,
            "filtered_port_count": filtered_count,
            "total_ports_scanned": open_count + closed_count + filtered_count,
        }
        _log.warning(
            "NMAP_NORMALIZE_RESULT target=%s host_up=%s ports_count=%d "
            "open=%d closed=%d filtered=%d",
            target, host_up, len(ports), open_count, closed_count, filtered_count,
        )
        return result
