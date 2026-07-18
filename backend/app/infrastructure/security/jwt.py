"""
JWT access-token encode/decode (SRS §1.2/§16.1: short-lived access
tokens, ~15 min).

Access tokens intentionally carry only `sub` (user id) and standard
registered claims (`exp`, `iat`) — no roles or permissions are embedded
in the token. Roles are scoped per-organization/per-project (FR-1.5)
and can change at any time, so every permission check re-reads
membership from the database rather than trusting a claim that could
be stale by the time the token is used.

Refresh tokens are NOT JWTs — they are opaque random strings, stored
hashed in the `sessions` table (see `infrastructure/security/tokens.py`),
matching SRS §16.1 ("refresh tokens stored hashed, rotated on every use").
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from pydantic import BaseModel

from app.core.config import Settings


class AccessTokenPayload(BaseModel):
    sub: str
    exp: datetime
    iat: datetime


class InvalidAccessTokenError(Exception):
    """Raised when an access token is malformed, expired, or has a bad signature."""


def create_access_token(user_id: UUID, settings: Settings, *, now: datetime | None = None) -> str:
    """Issue a signed JWT access token for `user_id`."""
    issued_at = now or datetime.now(UTC)
    expires_at = issued_at + timedelta(minutes=settings.JWT_ACCESS_TTL_MIN)
    payload = {
        "sub": str(user_id),
        "iat": issued_at,
        "exp": expires_at,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")


def decode_access_token(token: str, settings: Settings) -> AccessTokenPayload:
    """Validate signature + expiry and return the decoded payload."""
    try:
        raw = jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise InvalidAccessTokenError(str(exc)) from exc
    return AccessTokenPayload(**raw)
