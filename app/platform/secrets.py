"""Authenticated encryption for stored secrets (target-database connection strings, tokens).

* Algorithm: Fernet (AES-128-CBC + HMAC-SHA256, via ``cryptography``) through ``MultiFernet`` so that
  several keys can be active at once: the **first** key encrypts, **all** keys decrypt. That is what
  makes rotation possible without downtime: add the new key in front, re-encrypt rows, drop the old key.
* Keys live **outside** the database, in ``DBW_SECRET_KEYS`` (comma-separated Fernet keys). Generate
  one with ``python -m app.platform.secrets generate-key``.
* Ciphertext is stored with the prefix ``enc:v1:`` so plaintext rows written before encryption existed
  can be told apart and migrated. Decrypted values are never logged and never returned by the API.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

ENC_PREFIX = "enc:v1:"


class SecretError(RuntimeError):
    """Base class for secret-handling failures."""


class SecretKeyMissing(SecretError):
    """No encryption key is configured but one is required."""


class SecretDecryptError(SecretError):
    """Ciphertext could not be authenticated/decrypted with any configured key."""


def generate_key() -> str:
    """Return a new urlsafe-base64 Fernet key (44 chars)."""
    return Fernet.generate_key().decode("ascii")


def is_encrypted(value: str | None) -> bool:
    return bool(value) and str(value).startswith(ENC_PREFIX)


@dataclass(frozen=True, slots=True)
class SecretBox:
    """Encrypt/decrypt with a primary key and any number of legacy keys."""

    keys: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.keys:
            raise SecretKeyMissing("At least one secret key is required.")
        for key in self.keys:
            try:
                Fernet(key.encode("ascii"))
            except (ValueError, TypeError) as exc:
                raise SecretError("DBW_SECRET_KEYS contains an invalid Fernet key.") from exc

    @property
    def _multi(self) -> MultiFernet:
        return MultiFernet([Fernet(k.encode("ascii")) for k in self.keys])

    def encrypt(self, plaintext: str) -> str:
        token = self._multi.encrypt(plaintext.encode("utf-8")).decode("ascii")
        return f"{ENC_PREFIX}{token}"

    def decrypt(self, value: str) -> str:
        if not is_encrypted(value):
            raise SecretDecryptError("Value is not an encrypted secret (missing enc:v1: prefix).")
        token = value[len(ENC_PREFIX) :].encode("ascii")
        try:
            return self._multi.decrypt(token).decode("utf-8")
        except InvalidToken as exc:
            raise SecretDecryptError(
                "Could not decrypt secret with any configured key (rotated or wrong key?)."
            ) from exc

    def needs_rotation(self, value: str) -> bool:
        """True when ``value`` was encrypted with a non-primary key."""
        if not is_encrypted(value):
            return True
        token = value[len(ENC_PREFIX) :].encode("ascii")
        try:
            Fernet(self.keys[0].encode("ascii")).decrypt(token)
            return False
        except InvalidToken:
            return True

    def rotate(self, value: str) -> str:
        """Re-encrypt ``value`` (plaintext or ciphertext) with the primary key."""
        plaintext = self.decrypt(value) if is_encrypted(value) else value
        return self.encrypt(plaintext)


def parse_keys(raw: str | None) -> tuple[str, ...]:
    return tuple(k.strip() for k in (raw or "").split(",") if k.strip())


def secret_box_from_env(raw_keys: str | None) -> SecretBox | None:
    """Build a :class:`SecretBox` from the ``DBW_SECRET_KEYS`` value, or ``None`` if unset."""
    keys = parse_keys(raw_keys)
    return SecretBox(keys) if keys else None


def _cli(argv: list[str]) -> int:
    if len(argv) >= 1 and argv[0] == "generate-key":
        sys.stdout.write(generate_key() + "\n")
        return 0
    sys.stderr.write("usage: python -m app.platform.secrets generate-key\n")
    return 2


if __name__ == "__main__":  # pragma: no cover - thin CLI
    raise SystemExit(_cli(sys.argv[1:]))


__all__ = [
    "ENC_PREFIX",
    "SecretBox",
    "SecretDecryptError",
    "SecretError",
    "SecretKeyMissing",
    "generate_key",
    "is_encrypted",
    "parse_keys",
    "secret_box_from_env",
]
