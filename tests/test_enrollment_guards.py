"""Regression tests for two defects the v2 audit found in the enrollment endpoint.

1. The read-only guard **failed open**: ``read_only_ok`` was initialised to ``True`` and left
   untouched when the privilege probe raised, so a connection whose privileges could not be read
   was enrolled as though it had been verified - directly contrary to the comment above it.
2. ``_mark_schema_extracted`` sat inside the ``else`` of ``if request.run_embeddings:``, so on the
   normal path it never ran. The "already enrolled" fast path was therefore unreachable and every
   re-enroll repeated extraction, per-table LLM documentation and re-embedding.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.execution.readonly import ReadOnlyReport, ReadOnlyStatus
from app.models import SchemaDocumentationSummary, SchemaEmbeddingResult, SchemaPipelineResult
from db.database_manager import create_metadata_tables, get_session
from db.model import DatabaseConfig

PAYLOAD = {
    "db_flag": "probe_db",
    "db_type": "postgres",
    "connection_string": "postgresql://reader:secret@db.example.com:5432/app",
    "description": "test",
}


@pytest.fixture
def enroll_client(tmp_path, monkeypatch):
    """A client whose enrollment does no real extraction, documentation or embedding."""
    from app.platform.secrets import generate_key

    # Since v2 the self-hosted and production modes refuse to store a plaintext DSN, so a key is
    # part of a working configuration rather than an optional extra.
    monkeypatch.setenv("DBW_SECRET_KEYS", generate_key())
    project_url = f"sqlite:///{(tmp_path / 'project.db').as_posix()}"
    create_metadata_tables(project_url)

    import app.main as main

    monkeypatch.setattr(main, "get_project_db_connection_string", lambda: project_url)
    monkeypatch.setattr(
        "app.user_db_config_loader.get_project_db_connection_string", lambda: project_url
    )

    class _Orchestrator:
        def __init__(self, *args, **kwargs):
            self.calls = kwargs

        def run(self):
            return SchemaPipelineResult(
                extraction_output=Path(tmp_path / "schema"),
                tables_exported=3,
                documentation_summary=SchemaDocumentationSummary(
                    tables_total=3, documented=3, failed=0
                ),
                embedding_result=SchemaEmbeddingResult(minimal_files=[], document_chunks=7),
            )

    monkeypatch.setattr(main, "SchemaPipelineOrchestrator", _Orchestrator)

    from fastapi.testclient import TestClient as _TestClient

    return _TestClient(main.app), project_url


def _row(project_url: str, db_flag: str = "probe_db") -> DatabaseConfig | None:
    session = get_session(project_url)
    try:
        return session.query(DatabaseConfig).filter_by(db_flag=db_flag).first()
    finally:
        session.close()


class TestReadOnlyGuardFailsClosed:
    def test_a_probe_that_raises_refuses_enrollment(self, enroll_client, monkeypatch):
        client, _url = enroll_client

        def _explode(*_args, **_kwargs):
            raise RuntimeError("the privilege view is unreadable")

        monkeypatch.setattr("app.main.verify_read_only", _explode)
        response = client.post("/schemas/enroll", json=PAYLOAD)
        assert response.status_code == 400
        assert "read-only" in response.json()["detail"].lower()

    def test_a_writable_connection_is_refused(self, enroll_client, monkeypatch):
        client, _url = enroll_client
        monkeypatch.setattr(
            "app.main.verify_read_only",
            lambda *_a, **_k: ReadOnlyReport(ReadOnlyStatus.WRITABLE, "the user is a superuser"),
        )
        assert client.post("/schemas/enroll", json=PAYLOAD).status_code == 400

    def test_an_unverifiable_dialect_is_refused(self, enroll_client, monkeypatch):
        """UNSUPPORTED is not 'safe'. If we cannot check, we do not enroll."""
        client, _url = enroll_client
        monkeypatch.setattr(
            "app.main.verify_read_only",
            lambda *_a, **_k: ReadOnlyReport(
                ReadOnlyStatus.UNSUPPORTED, "no check for this dialect"
            ),
        )
        assert client.post("/schemas/enroll", json=PAYLOAD).status_code == 400

    def test_a_verified_connection_is_accepted(self, enroll_client, monkeypatch):
        client, _url = enroll_client
        monkeypatch.setattr(
            "app.main.verify_read_only",
            lambda *_a, **_k: ReadOnlyReport(
                ReadOnlyStatus.VERIFIED_READ_ONLY, "no write grants found"
            ),
        )
        assert client.post("/schemas/enroll", json=PAYLOAD).status_code == 200


class TestSchemaExtractedFlag:
    @pytest.fixture(autouse=True)
    def _readonly_ok(self, monkeypatch):
        monkeypatch.setattr(
            "app.main.verify_read_only",
            lambda *_a, **_k: ReadOnlyReport(
                ReadOnlyStatus.VERIFIED_READ_ONLY, "no write grants found"
            ),
        )

    def test_the_flag_is_set_on_the_normal_path(self, enroll_client):
        client, url = enroll_client
        response = client.post("/schemas/enroll", json={**PAYLOAD, "run_embeddings": True})
        assert response.status_code == 200, response.text
        row = _row(url)
        assert row is not None
        assert row.schema_extracted is True, (
            "without this, the already-enrolled fast path is unreachable and every re-enroll "
            "repeats extraction, per-table LLM documentation and re-embedding"
        )

    def test_the_flag_is_also_set_when_embeddings_are_skipped(self, enroll_client):
        client, url = enroll_client
        client.post("/schemas/enroll", json={**PAYLOAD, "run_embeddings": False})
        assert _row(url).schema_extracted is True

    def test_re_enrolling_can_take_the_fast_path(self, enroll_client):
        client, url = enroll_client
        client.post("/schemas/enroll", json={**PAYLOAD, "run_embeddings": True})
        assert _row(url).schema_extracted is True

        second = client.post(
            "/schemas/enroll", json={**PAYLOAD, "incremental_documentation": False}
        )
        assert second.status_code == 200
        body = second.json()
        assert body["documentation"]["status"] == "skipped"
        assert body["embeddings"]["status"] == "skipped"


class TestConnectionSecretsOnEnroll:
    @pytest.fixture(autouse=True)
    def _readonly_ok(self, monkeypatch):
        monkeypatch.setattr(
            "app.main.verify_read_only",
            lambda *_a, **_k: ReadOnlyReport(ReadOnlyStatus.VERIFIED_READ_ONLY, "ok"),
        )

    def test_the_dsn_is_encrypted_and_never_stored_in_the_clear(self, enroll_client):
        from app.platform.secrets import is_encrypted

        client, url = enroll_client
        assert client.post("/schemas/enroll", json=PAYLOAD).status_code == 200

        row = _row(url)
        assert is_encrypted(row.connection_secret)
        assert "secret@db.example.com" not in (row.connection_secret or "")
        assert "secret@db.example.com" not in (row.connection_string or "")

    def test_the_stored_secret_round_trips_for_the_query_path(self, enroll_client):
        from app.user_db_config_loader import get_user_database_settings

        client, _url = enroll_client
        client.post("/schemas/enroll", json=PAYLOAD)
        settings = get_user_database_settings("probe_db")
        assert settings.connection_string == PAYLOAD["connection_string"]

    def test_without_a_key_enrollment_is_refused_with_an_actionable_message(
        self, enroll_client, monkeypatch
    ):
        """A breaking change, deliberately: storing a DSN in the clear is no longer an option."""
        monkeypatch.delenv("DBW_SECRET_KEYS", raising=False)
        client, url = enroll_client
        response = client.post("/schemas/enroll", json=PAYLOAD)
        assert response.status_code == 400
        detail = response.json()["detail"]
        assert "DBW_SECRET_KEYS" in detail
        assert "generate-key" in detail
        assert _row(url) is None or not _row(url).connection_secret
