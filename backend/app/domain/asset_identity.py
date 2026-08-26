"""
Canonical asset identity keys (M7.3 Phase 2).

Deterministic identity functions that decouple an asset's correlation
identity from its human-readable display value. The display value stays
in ``Asset.value`` untouched (legacy compatibility); the identity lives
in ``Asset.identity_key`` and is what cross-tool correlation merges on.

Design (approved M7.3 Phase 0 report §5):

- HOST / SUBDOMAIN: normalized IP when the value parses as one,
  otherwise lowercased hostname/domain.
- SERVICE: ``{transport}/{host}:{port}`` plus ``/{scheme}`` only when a
  meaningful scheme is known. Deliberately INDEPENDENT of nmap's
  service-name guess — ``ppp?://h:p/tcp`` and a later-confirmed
  ``http://h:p`` collapse to one identity.
- WEB ENDPOINT: service origin identity + normalized path (lowercase
  host, default ports materialized, fragment dropped, query params
  sorted). Endpoints are NOT materialized as assets; this key is for
  enrichment/provenance.
- TECHNOLOGY: ``{context}#{slug(name)}`` — always scoped to its host or
  service context.

Pure functions, zero framework imports (domain layer).
"""

from __future__ import annotations

import re
from ipaddress import ip_address
from urllib.parse import parse_qsl, urlsplit

_DEFAULT_PORTS: dict[str, int] = {"http": 80, "https": 443}

# Legacy nmap SERVICE value shape produced by AssetService:
#   {service}://{host}:{port}/{protocol}
_LEGACY_SERVICE_RE = re.compile(
    r"^(?P<service>[^:/]+)://(?P<host>[^:/]+):(?P<port>\d+)/(?P<proto>[a-z0-9]+)$",
    re.IGNORECASE,
)


def _slug(value: str) -> str:
    """Lowercase slug keeping [a-z0-9._-]; other runs collapse to '-'."""
    out: list[str] = []
    prev_dash = False
    for ch in value.strip().lower():
        if ch.isalnum() or ch in "._":
            out.append(ch)
            prev_dash = False
        elif not prev_dash and out:
            out.append("-")
            prev_dash = True
    return "".join(out).strip("-")


def normalize_host(value: str) -> str:
    """Normalized IP (compressed form) or lowercased hostname."""
    text = value.strip()
    try:
        return str(ip_address(text))
    except ValueError:
        pass
    # Tolerate URL-ish input for hosts: keep just the hostname part.
    if "://" in text:
        parts = urlsplit(text)
        text = parts.hostname or text
    return text.strip().strip(".").lower()


def split_url(url: str) -> tuple[str | None, str | None, int | None]:
    """Deterministic (scheme, host, port) with defaults materialized."""
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return None, None, None
    scheme = parts.scheme.lower() or None
    host = parts.hostname.lower() if parts.hostname else None
    if port is None and scheme in _DEFAULT_PORTS:
        port = _DEFAULT_PORTS[scheme]
    return scheme, host, port


def service_identity(
    host: str, port: int, transport: str = "tcp", scheme: str | None = None
) -> str:
    """Canonical L4/L7 service identity."""
    transport_clean = transport.strip().lower() or "tcp"
    base = f"{transport_clean}/{normalize_host(host)}:{int(port)}"
    if scheme and scheme.strip().lower() in ("http", "https"):
        return f"{base}/{scheme.strip().lower()}"
    return base


def endpoint_identity(url: str) -> str | None:
    """
    Canonical web-endpoint identity: origin + normalized path/query.
    Returns None when the URL cannot be parsed.
    """
    scheme, host, port = split_url(url)
    if not scheme or not host or not port:
        return None
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    path = parts.path or "/"
    query_pairs = sorted(parse_qsl(parts.query, keep_blank_values=True))
    query = "&".join(f"{k}={v}" for k, v in query_pairs)
    origin = service_identity(host, port, "tcp", scheme)
    return f"{origin}{path}" + (f"?{query}" if query else "")


def technology_identity(name: str, context_identity: str) -> str:
    """Technology name scoped to its host/service context."""
    return f"{context_identity}#{_slug(name)}"


def identity_for_asset(asset_type: str, value: str) -> str | None:
    """
    Backfill/compat dispatcher: compute the canonical identity for an
    existing-style asset value. Never raises; falls back to a slugged
    value so backfill can always proceed deterministically.
    """
    text = (value or "").strip()
    if not text:
        return None

    if asset_type in ("host", "subdomain"):
        return normalize_host(text)

    if asset_type == "service":
        legacy = _LEGACY_SERVICE_RE.match(text)
        if legacy:
            return service_identity(
                legacy.group("host"),
                int(legacy.group("port")),
                legacy.group("proto").lower(),
            )
        if "://" in text:
            scheme, host, port = split_url(text)
            if host and port:
                return service_identity(host, port, "tcp", scheme)
        return _slug(text)

    if asset_type == "technology":
        return _slug(text)

    # credential / unknown types: stable slug fallback.
    return _slug(text)
