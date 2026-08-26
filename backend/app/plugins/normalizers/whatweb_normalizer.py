"""
WhatWeb output normalizer (M7.3 Phase 1).

Parses WhatWeb `--log-json` output into a structured payload. WhatWeb
emits a JSON document whose top level is an array of per-target result
objects:

    [ {"target": "http://h:p", "http_status": 200,
       "plugins": {"HTTPServer": {"string": ["Express"]},
                    "Bootstrap": {"version": ["4.5.0"]}, ...}}, ... ]

The document may be embedded in noisy stdout (banner text around the
JSON), so extraction uses an incremental JSON decoder rather than
assuming the whole buffer is JSON. Malformed fragments are skipped and
counted — never fatal.

Technology names are normalized deterministically (lowercase slug) so
"WhatWeb"/"whatweb" style variations collapse to one identity later.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import urlsplit

_log = logging.getLogger(__name__)

_DEFAULT_PORTS: dict[str, int] = {"http": 80, "https": 443}


def _split_url(url: str) -> tuple[str | None, str | None, int | None]:
    """Deterministically derive (scheme, host, port) from a URL."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None, None, None
    scheme = parts.scheme.lower() or None
    host = parts.hostname.lower() if parts.hostname else None
    if port is None and scheme in _DEFAULT_PORTS:
        port = _DEFAULT_PORTS[scheme]
    return scheme, host, port


def _normalize_tech_name(raw: str) -> str:
    name = raw.strip().lower()
    out: list[str] = []
    prev_dash = False
    for ch in name:
        if ch.isalnum() or ch in "._":
            out.append(ch)
            prev_dash = False
        elif not prev_dash and out:
            out.append("-")
            prev_dash = True
    return "".join(out).strip("-")


def _extract_json_documents(text: str) -> list[object]:
    """
    Decode every JSON value embedded in `text`, skipping noise between
    them (WhatWeb may print banner/progress text around --log-json).
    Scans both '[' and '{' starts so arrays and single objects decode.
    """
    decoder = json.JSONDecoder()
    documents: list[object] = []
    positions = sorted(
        i for i, ch in enumerate(text) if ch in "[{"
    )
    seen_end = -1
    for idx in positions:
        if idx < seen_end:
            continue  # already consumed by a previous document
        try:
            value, end = decoder.raw_decode(text, idx)
            documents.append(value)
            seen_end = end
        except json.JSONDecodeError:
            continue
    return documents


def _plugin_versions(info: object) -> list[str]:
    """Extract version strings from a WhatWeb plugin info object."""
    versions: list[str] = []
    if isinstance(info, dict):
        raw = info.get("version")
        if isinstance(raw, list):
            versions.extend(str(v) for v in raw if str(v).strip())
        elif isinstance(raw, str) and raw.strip():
            versions.append(raw.strip())
    return versions


class WhatwebNormalizer:
    @property
    def plugin_name(self) -> str:
        return "whatweb"

    def normalize(
        self,
        raw_stdout: str,
        raw_stderr: str,
        plugin_config: dict[str, Any],
    ) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        skipped_entries = 0

        for document in _extract_json_documents(raw_stdout):
            entries: list[object]
            if isinstance(document, dict):
                entries = [document]
            elif isinstance(document, list):
                entries = document
            else:
                continue

            for entry in entries:
                if not isinstance(entry, dict):
                    skipped_entries += 1
                    continue

                target_raw = entry.get("target")
                target_url = (
                    target_raw.strip() if isinstance(target_raw, str) else ""
                )
                scheme, host, port = (
                    _split_url(target_url) if target_url else (None, None, None)
                )

                plugins_raw = entry.get("plugins")
                technologies: list[dict[str, Any]] = []
                server: str | None = None
                if isinstance(plugins_raw, dict):
                    for name, info in plugins_raw.items():
                        if not isinstance(name, str) or not name.strip():
                            continue
                        normalized = _normalize_tech_name(name)
                        if not normalized:
                            continue
                        versions = _plugin_versions(info)
                        strings: list[str] = []
                        if isinstance(info, dict):
                            raw_strings = info.get("string")
                            if isinstance(raw_strings, list):
                                strings = [
                                    str(s) for s in raw_strings if str(s).strip()
                                ]
                        # The responding web server surfaces as the
                        # HTTPServer / Header-Server fingerprint.
                        if normalized in ("httpserver", "header-server") and strings:
                            server = strings[0]
                        technologies.append(
                            {
                                "name": normalized,
                                "raw_name": name.strip(),
                                "versions": versions,
                                **({"string": strings} if strings else {}),
                            }
                        )

                http_status = entry.get("http_status")
                status_int = (
                    http_status if isinstance(http_status, int) else None
                )

                if not target_url and not host:
                    skipped_entries += 1
                    continue

                results.append(
                    {
                        "url": target_url,
                        "host": host,
                        "port": port,
                        "scheme": scheme,
                        "http_status": status_int,
                        "server": server,
                        "technologies": technologies,
                        "technology_count": len(technologies),
                    }
                )

        target = str(plugin_config.get("target", ""))
        if not target and results:
            first_host = results[0].get("host")
            if isinstance(first_host, str):
                target = first_host

        payload: dict[str, Any] = {
            "target": target,
            "results": results,
            "result_count": len(results),
            "skipped_entries": skipped_entries,
        }
        _log.info(
            "WHATWEB_NORMALIZE_RESULT records=%d skipped=%d",
            len(results),
            skipped_entries,
        )
        return payload
