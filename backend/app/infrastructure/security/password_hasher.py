"""
Password hashing (SRS §16.1: "Argon2id password hashing, configurable
work factor").

Thin wrapper around `argon2-cffi`'s `PasswordHasher`, isolated behind a
small interface so `application/` services depend on this module's two
functions rather than importing argon2 directly — keeps the concrete
hashing library swappable without touching use-case code.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

# Defaults are argon2-cffi's own recommended parameters, which already
# target Argon2id. Overriding time_cost/memory_cost is a production
# tuning concern (SRS §16.1 "configurable work factor") deferred until
# real load/latency numbers justify a specific choice.
_hasher = PasswordHasher()


def hash_password(plain_password: str) -> str:
    """Hash a plaintext password. Never store the plaintext anywhere."""
    return _hasher.hash(plain_password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    """Return True if `plain_password` matches `password_hash`, else False."""
    try:
        return _hasher.verify(password_hash, plain_password)
    except VerifyMismatchError:
        return False
