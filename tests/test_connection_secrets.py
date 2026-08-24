"""Connection strings must not sit in the database as plaintext.

These are regression tests for a defect the v2 audit found in code that already existed: the DSN,
password included, was stored as ordinary text in ``Database_config.connection_string``.
"""

from __future__ import annotations

import pytest

from app.platform.connection_secrets import (
    ConnectionSecretError,
    encrypt_existing_rows,
    prepare_for_storage,
    read_connection_string,
)
from app.platform.secrets import generate_key, is_encrypted
from db.database_manager import create_metadata_tables, get_session
from db.model import DatabaseConfig

DSN = "postgresql+psycopg://analyst:hunter2@db.internal:5432/warehouse"


@pytest.fixture
def key() -> str:
    return generate_key()


class TestPrepareForStorage:
    def test_encrypts_when_a_key_is_available(self, key: str):
        stored = prepare_for_storage(DSN, encryption_required=True, keys=[key])
        assert stored.encrypted is True
        assert stored.plaintext is None
        assert is_encrypted(stored.secret)
        assert "hunter2" not in stored.secret

    def test_refuses_plaintext_when_the_mode_requires_encryption(self, monkeypatch):
        monkeypatch.delenv("DBW_SECRET_KEYS", raising=False)
        with pytest.raises(ConnectionSecretError, match="DBW_SECRET_KEYS"):
            prepare_for_storage(DSN, encryption_required=True)

    def test_falls_back_to_plaintext_with_a_warning_when_allowed(self, monkeypatch, caplog):
        import logging

        monkeypatch.delenv("DBW_SECRET_KEYS", raising=False)
        logger = logging.getLogger("app.platform.connection_secrets")
        logger.addHandler(caplog.handler)
        try:
            with caplog.at_level(logging.WARNING):
                stored = prepare_for_storage(DSN, encryption_required=False)
        finally:
            logger.removeHandler(caplog.handler)
        assert stored.encrypted is False and stored.plaintext == DSN
        assert "plaintext" in caplog.text.lower()


class TestReadConnectionString:
    def test_round_trip(self, key: str):
        stored = prepare_for_storage(DSN, encryption_required=True, keys=[key])
        assert read_connection_string(secret=stored.secret, plaintext="", keys=[key]) == DSN

    def test_prefers_the_encrypted_column(self, key: str):
        stored = prepare_for_storage(DSN, encryption_required=True, keys=[key])
        resolved = read_connection_string(
            secret=stored.secret, plaintext="postgresql://stale@old/db", keys=[key]
        )
        assert resolved == DSN

    def test_legacy_plaintext_still_works(self):
        assert read_connection_string(secret=None, plaintext=DSN) == DSN

    def test_ciphertext_in_the_legacy_column_is_handled(self, key: str):
        stored = prepare_for_storage(DSN, encryption_required=True, keys=[key])
        assert read_connection_string(secret=None, plaintext=stored.secret, keys=[key]) == DSN

    def test_a_missing_key_is_an_explicit_error(self, key: str, monkeypatch):
        monkeypatch.delenv("DBW_SECRET_KEYS", raising=False)
        stored = prepare_for_storage(DSN, encryption_required=True, keys=[key])
        with pytest.raises(ConnectionSecretError, match="Restore the key"):
            read_connection_string(secret=stored.secret, plaintext=None)

    def test_a_rotated_away_key_says_so(self, key: str):
        stored = prepare_for_storage(DSN, encryption_required=True, keys=[key])
        with pytest.raises(ConnectionSecretError, match="previous key"):
            read_connection_string(secret=stored.secret, plaintext=None, keys=[generate_key()])

    def test_nothing_stored_is_an_error_not_an_empty_string(self):
        with pytest.raises(ConnectionSecretError, match="No connection string"):
            read_connection_string(secret=None, plaintext=None)

    def test_environment_variables_are_not_expanded_in_a_secret(self, key: str, monkeypatch):
        """A stored '%POSTGRES_CONNECTION_STRING%' must not pull in the app's own credentials."""
        monkeypatch.setenv("POSTGRES_CONNECTION_STRING", "postgresql://app:appsecret@localhost/app")
        stored = prepare_for_storage(
            "$POSTGRES_CONNECTION_STRING", encryption_required=True, keys=[key]
        )
        resolved = read_connection_string(secret=stored.secret, plaintext=None, keys=[key])
        assert resolved == "$POSTGRES_CONNECTION_STRING"
        assert "appsecret" not in resolved


class TestMigratingExistingRows:
    def test_plaintext_rows_are_encrypted_in_place(self, tmp_path, key: str):
        url = f"sqlite:///{tmp_path.as_posix()}/p.db"
        create_metadata_tables(url)
        session = get_session(url)
        try:
            session.add(DatabaseConfig(db_flag="legacy", db_type="postgres", connection_string=DSN))
            session.commit()

            assert encrypt_existing_rows(session, keys=[key]) == 1
            row = session.query(DatabaseConfig).filter_by(db_flag="legacy").one()
            assert is_encrypted(row.connection_secret)
            assert (
                read_connection_string(
                    secret=row.connection_secret, plaintext=row.connection_string, keys=[key]
                )
                == DSN
            )

            # Idempotent: a second pass finds nothing to do.
            assert encrypt_existing_rows(session, keys=[key]) == 0
        finally:
            session.close()

    def test_refuses_without_a_key(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DBW_SECRET_KEYS", raising=False)
        url = f"sqlite:///{tmp_path.as_posix()}/q.db"
        create_metadata_tables(url)
        session = get_session(url)
        try:
            with pytest.raises(ConnectionSecretError):
                encrypt_existing_rows(session)
        finally:
            session.close()
