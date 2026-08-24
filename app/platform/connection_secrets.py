"""Storing and reading target-database connection strings without keeping them in plaintext.

The brief is explicit that plaintext database credentials must never be stored, and until now they
were: ``Database_config.connection_string`` held the DSN, password included, as ordinary text.

The migration path had to work on live data, so this is deliberately two-column and tolerant:

* new writes go to ``connection_secret`` as ``enc:v1:...`` ciphertext;
* reads prefer the encrypted column and fall back to the legacy plaintext one, so a database that
  has not been re-encrypted yet keeps working;
* a maintenance pass (:func:`encrypt_existing_rows`) moves the legacy values across.

Whether encryption is *required* is a mode decision, not a local one. In production and self-hosted
modes a missing key is refused outright rather than silently downgraded - a deployment that thinks
it is encrypting and is not is worse than one that knows it is not.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from app.platform.secrets import SecretBox, SecretDecryptError, is_encrypted, secret_box_from_env

logger = logging.getLogger(__name__)


class ConnectionSecretError(RuntimeError):
    """A connection secret could not be stored or read under the active policy."""


@dataclass(frozen=True, slots=True)
class StoredSecret:
    """What to persist: ciphertext when a key exists, plaintext only when policy allows it."""

    secret: str | None
    plaintext: str | None
    encrypted: bool


def _box(keys: list[str] | None = None) -> SecretBox | None:
    if keys:
        return SecretBox(tuple(keys))
    return secret_box_from_env(os.environ.get("DBW_SECRET_KEYS"))


def prepare_for_storage(
    connection_string: str,
    *,
    encryption_required: bool,
    keys: list[str] | None = None,
) -> StoredSecret:
    """Encrypt a connection string, or refuse when the policy says it must be encrypted."""
    box = _box(keys)
    if box is not None:
        return StoredSecret(secret=box.encrypt(connection_string), plaintext=None, encrypted=True)
    if encryption_required:
        raise ConnectionSecretError(
            "This deployment requires encrypted connection secrets but DBW_SECRET_KEYS is not set. "
            "Generate a key with 'uv run python -m app.platform.secrets generate-key' and set it "
            "before enrolling a database."
        )
    logger.warning(
        "DBW_SECRET_KEYS is not set: the connection string will be stored in plaintext. "
        "Set a key to encrypt it at rest."
    )
    return StoredSecret(secret=None, plaintext=connection_string, encrypted=False)


def read_connection_string(
    *,
    secret: str | None,
    plaintext: str | None,
    keys: list[str] | None = None,
) -> str:
    """Return the usable connection string, preferring the encrypted column."""
    if secret:
        box = _box(keys)
        if box is None:
            raise ConnectionSecretError(
                "This database's connection secret is encrypted but DBW_SECRET_KEYS is not set. "
                "Restore the key to use it."
            )
        try:
            return box.decrypt(secret)
        except SecretDecryptError as exc:
            raise ConnectionSecretError(
                "The stored connection secret could not be decrypted with any configured key. "
                "If the key was rotated, add the previous key to DBW_SECRET_KEYS."
            ) from exc
    if plaintext:
        if is_encrypted(plaintext):
            # A ciphertext that ended up in the legacy column (an interrupted migration).
            return read_connection_string(secret=plaintext, plaintext=None, keys=keys)
        return plaintext
    raise ConnectionSecretError("No connection string is stored for this database.")


def encrypt_existing_rows(session, *, keys: list[str] | None = None) -> int:
    """Move plaintext connection strings into the encrypted column. Idempotent.

    Returns how many rows were re-encrypted. Safe to run repeatedly and safe to interrupt: each row
    is committed on its own, and a row that already has ciphertext is skipped.
    """
    from db.model import DatabaseConfig

    box = _box(keys)
    if box is None:
        raise ConnectionSecretError("Cannot encrypt stored secrets: DBW_SECRET_KEYS is not set.")

    migrated = 0
    for row in session.query(DatabaseConfig).all():
        if getattr(row, "connection_secret", None):
            continue
        legacy = row.connection_string
        if not legacy:
            continue
        row.connection_secret = box.encrypt(legacy)
        # The legacy column is left in place for one release so a rollback is survivable; it is
        # dropped by a later migration once deployments have moved.
        session.commit()
        migrated += 1
    if migrated:
        logger.info("Encrypted %d stored connection string(s).", migrated)
        _audit_rotation(migrated)
    return migrated


def _audit_rotation(migrated: int) -> None:
    """Record that stored credentials changed at rest.

    Credentials moving from plaintext into ciphertext is a change to how every enrolled secret is
    held, and it happens unattended at start-up. Without a record, the only evidence it ran at all
    is a log line that rotates away.
    """
    try:
        from app.platform.audit import AuditAction, AuditOutcome, record

        record(
            AuditAction.SECRET_ROTATED,
            subject="connection_secret",
            outcome=AuditOutcome.SUCCESS,
            reason="plaintext connection strings moved to the encrypted column",
            detail={"rows": migrated},
        )
    except Exception:  # pragma: no cover - auditing must not fail a start-up migration
        logger.debug("Could not audit the secret rotation", exc_info=True)


__all__ = [
    "ConnectionSecretError",
    "StoredSecret",
    "encrypt_existing_rows",
    "prepare_for_storage",
    "read_connection_string",
]
