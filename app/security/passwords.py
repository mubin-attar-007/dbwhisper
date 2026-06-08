"""Password hashing with Argon2id (the OWASP-recommended default)."""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

_ph = PasswordHasher()


def hash_password(raw: str) -> str:
    """Return an Argon2id hash of the password."""
    return _ph.hash(raw)


def verify_password(hashed: str, raw: str) -> bool:
    """Constant-time verify; False on mismatch or a malformed hash (never raises)."""
    try:
        return _ph.verify(hashed, raw)
    except (VerifyMismatchError, InvalidHashError):
        return False


def needs_rehash(hashed: str) -> bool:
    """True if the hash should be upgraded (params changed) — rehash on next login."""
    try:
        return _ph.check_needs_rehash(hashed)
    except InvalidHashError:
        return True
