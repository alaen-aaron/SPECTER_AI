"""
HTTPX output normalizer (M7.3 Phase 1).

Parses ProjectDiscovery httpx `-json` JSONL output — one JSON object
per line — into a structured, correlation-ready payload. HTTPX already
provides structured fields (url/host/port/scheme/status/title/
webserver/tech/cdn), so no heuristic string parsing is used for those;
the only derivation is filling host/port/scheme from the URL when the
binary omits them.

Malformed JSONL lines are skipped and counted — never fatal (matching
the existing normalizer conventions used by nmap/ping/nuclei).
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import urlsplit

_log = logging.getLogger(__name__)

_DEFAULT_PORTS: dict[str, int] = {"http": 80, "https": 443}


def _as_int_port(value: object) -> int | None:
    """Normalize a port that may arrive as int or numeric string."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _split_url(url: str) -> tuple[str | None, str | None, int | None]:
    """Deterministically derive (scheme, host, port) from a URL."""
    try:
        parts = urlsplit(url)
        port = parts.port  # may be None; raises on malformed ports
    except ValueError:
        return None, None, None
    scheme = parts.scheme.lower() or None
    host = parts.hostname.lower() if parts.hostname else None
    if port is None and scheme in _DEFAULT_PORTS:
        port = _DEFAULT_PORTS[scheme]
    return scheme, host, port


def _normalize_tech_name(raw: str) -> str:
    name = raw.strip().lower()
    # Deterministic slug: keep [a-z0-9._-], collapse other runs to '-'.
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


def _parse_tech_entry(raw: str) -> dict[str, str | None]:
    """
    Split an HTTPX tech string into a normalized name/version pair.
    HTTPX emits ``Name:Value`` entries (``Nginx:1.24.0``, ``HSTS``,
    ``X-Powered-By:Express``), so the FIRST colon separates them; both
    halves are preserved so no information is ever lost.
    """
    text = raw.strip()
    if ":" in text:
        left, right = text.split(":", 1)
        return {
            "name": _normalize_tech_name(left),
            "version": right.strip() or None,
        }
    return {"name": _normalize_tech_name(text), "version": None}


class HttpxNormalizer:
    @property
    def plugin_name(self) -> str:
        return "httpx"

    def normalize(
        self,
        raw_stdout: str,
        raw_stderr: str,
        plugin_config: dict[str, Any],
    ) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        skipped_malformed = 0
        skipped_failed = 0

        for line in raw_stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                skipped_malformed += 1
                _log.debug("httpx non-json line skipped: %r", line[:200])
                continue
            if not isinstance(entry, dict):
                skipped_malformed += 1
                continue

            if entry.get("failed") is True:
                skipped_failed += 1
                continue

            url = entry.get("url")
            url_str = url.strip() if isinstance(url, str) else ""

            scheme_field = entry.get("scheme")
            host_field = entry.get("host")
            port_field = _as_int_port(entry.get("port"))

            url_scheme, url_host, url_port = (
                _split_url(url_str) if url_str else (None, None, None)
            )

            scheme = (
                scheme_field.lower()
                if isinstance(scheme_field, str) and scheme_field
                else url_scheme
            )
            host = (
                str(host_field).lower()
                if host_field not in (None, "")
                else url_host
            )
            port = port_field if port_field is not None else url_port

            if not url_str and not host:
                skipped_malformed += 1
                continue

            status_code = entry.get("status_code")
            status_int = status_code if isinstance(status_code, int) else None

            title = entry.get("title")
            title_str = title.strip() if isinstance(title, str) else None

            webserver = entry.get("webserver")
            webserver_str = webserver.strip() if isinstance(webserver, str) else None

            raw_tech = entry.get("tech")
            technologies: list[dict[str, str | None]] = []
            if isinstance(raw_tech, list):
                for item in raw_tech:
                    if isinstance(item, str) and item.strip():
                        technologies.append(_parse_tech_entry(item))

            cdn_raw = entry.get("cdn")

            if url_str:
                canonical_url = url_str
            elif host and port:
                canonical_url = f"{scheme or 'http'}://{host}:{port}"
            else:
                canonical_url = ""

            record: dict[str, Any] = {
                "url": canonical_url,
                "host": host,
                "port": port,
                "scheme": scheme,
                "status_code": status_int,
                "title": title_str,
                "webserver": webserver_str,
                "technologies": technologies,
                "cdn": bool(cdn_raw),
            }
            results.append(record)

        target = str(plugin_config.get("target", ""))
        if not target and results:
            first_host = results[0].get("host")
            if isinstance(first_host, str):
                target = first_host

        payload: dict[str, Any] = {
            "target": target,
            "results": results,
            "result_count": len(results),
            "skipped_lines": skipped_malformed + skipped_failed,
        }
        _log.info(
            "HTTPX_NORMALIZE_RESULT records=%d skipped=%d",
            len(results),
            payload["skipped_lines"],
        )
        return payload
