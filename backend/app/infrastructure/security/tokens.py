"""
Refresh-token generation and hashing (SRS §16.1).

Refresh tokens are opaque, cryptographically random strings handed to
the client. Only their SHA-256 hash is ever persisted (in `sessions.
refresh_token_hash`), so a stolen database dump alone can't be replayed
as a valid refresh token. Rotation (issuing a new token + revoking the
old session) is handled by `application/auth_service.py`, not here —
this module is pure crypto/util, no persistence.
"""

from __future__ import annotations

import hashlib
import secrets

_TOKEN_BYTES = 32  # 256 bits of entropy


def generate_refresh_token() -> str:
    """Return a new, URL-safe, cryptographically random refresh token."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


def hash_refresh_token(token: str) -> str:
    """Deterministic SHA-256 hash used to look up/compare stored sessions."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
