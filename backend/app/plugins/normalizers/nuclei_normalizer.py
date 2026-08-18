"""
Nuclei output normalizer (Milestone 5).

Parses nuclei `-jsonl` stdout (one JSON object per line, format used by
nuclei v3.x) into a structured payload consumable by the correlation
service:

  - target: the scanned URL/host
  - vulnerabilities: list of {template_id, title, severity, description,
    matched_at, type}

Without this normalizer, nuclei tool results are persisted but never
correlate into findings despite the plugin advertising
``produces_findings=True``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

_log = logging.getLogger(__name__)


class NucleiNormalizer:
    @property
    def plugin_name(self) -> str:
        return "nuclei"

    def normalize(
        self,
        raw_stdout: str,
        raw_stderr: str,
        plugin_config: dict[str, Any],
    ) -> dict[str, Any]:
        target = str(plugin_config.get("target", ""))
        vulnerabilities: list[dict[str, Any]] = []

        for line in raw_stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                _log.debug("nuclei non-json line skipped: %r", line[:200])
                continue
            if not isinstance(entry, dict):
                continue

            info = entry.get("info")
            info = info if isinstance(info, dict) else {}

            template_id = str(entry.get("template-id", ""))
            title = str(info.get("name", entry.get("matched-at", "nuclei finding")))
            severity = str(info.get("severity", "info")).lower()
            description = info.get("description")
            if not isinstance(description, str) or not description:
                description = None

            vulnerabilities.append(
                {
                    "template_id": template_id,
                    "title": title,
                    "severity": severity,
                    "description": description,
                    "matched_at": entry.get("matched-at"),
                    "type": info.get("classification", {}).get(
                        "cve-id", entry.get("type", "")
                    ),
                }
            )

        if not target:
            for entry in vulnerabilities:
                matched = entry.get("matched_at")
                if isinstance(matched, str):
                    target = matched
                    break

        result: dict[str, Any] = {
            "target": target,
            "vulnerabilities": vulnerabilities,
        }
        _log.info(
            "NUCLEI_NORMALIZE_RESULT target=%s vulnerabilities=%d",
            target,
            len(vulnerabilities),
        )
        return result
