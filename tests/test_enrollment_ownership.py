"""Who owns an enrolled identifier, and what happens when someone else claims it.

Recorded as G-8 in ``docs/v2/THREAT_MODEL.md``: ``_fetch_or_create_database_config`` looked a row up
by ``db_flag`` alone and handed it back to any caller. Because the row carries the *credential*, a
second tenant could re-run the enrollment pipeline — extraction, per-table model documentation,
re-embedding — against the first tenant's database, using the first tenant's stored connection.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

import app.main as main_module
from app.models import SchemaPipelineRequest
from app.platform.audit import AuditAction, InMemoryAuditSink, audit_to
from db.database_manager import create_metadata_tables, get_session
from db.model import DatabaseConfig

OWNER = 1
INTRUDER = 2


@pytest.fixture
def project_db(tmp_path) -> str:
    url = f"sqlite:///{tmp_path.as_posix()}/project.db"
    create_metadata_tables(url)
    return url


def _enrol_row(url: str, *, db_flag: str, owner_id: int | None) -> None:
    session = get_session(url)
    try:
        session.add(
            DatabaseConfig(
                db_flag=db_flag,
                db_type="sqlite",
                connection_string="sqlite:///theirs.db",
                owner_id=owner_id,
                max_rows=10000,
                query_timeout=30,
            )
        )
        session.commit()
    finally:
        session.close()


def _request(db_flag: str) -> SchemaPipelineRequest:
    return SchemaPipelineRequest(
        db_flag=db_flag,
        db_type="sqlite",
        connection_string="sqlite:///mine.db",
    )


class TestOwnershipOfAnEnrolledIdentifier:
    def test_the_owner_may_re_enroll(self, project_db):
        _enrol_row(project_db, db_flag="acme", owner_id=OWNER)
        row = main_module._fetch_or_create_database_config(
            _request("acme"), project_db, owner_id=OWNER
        )
        assert row.db_flag == "acme"

    def test_another_tenant_may_not(self, project_db):
        _enrol_row(project_db, db_flag="acme", owner_id=OWNER)
        with pytest.raises(HTTPException) as exc_info:
            main_module._fetch_or_create_database_config(
                _request("acme"), project_db, owner_id=INTRUDER
            )
        assert exc_info.value.status_code == 403
        assert "another owner" in exc_info.value.detail

    def test_the_intruder_never_receives_the_stored_credential(self, project_db):
        """The point of the defect: the row carries someone else's connection string."""
        _enrol_row(project_db, db_flag="acme", owner_id=OWNER)
        with pytest.raises(HTTPException) as exc_info:
            main_module._fetch_or_create_database_config(
                _request("acme"), project_db, owner_id=INTRUDER
            )
        assert "theirs.db" not in str(exc_info.value.detail)

    def test_an_anonymous_caller_may_not_take_over_an_owned_identifier(self, project_db):
        _enrol_row(project_db, db_flag="acme", owner_id=OWNER)
        with pytest.raises(HTTPException):
            main_module._fetch_or_create_database_config(
                _request("acme"), project_db, owner_id=None
            )

    def test_a_public_identifier_stays_shared(self, project_db):
        """A row with no owner — the demo — is open on purpose and must not start refusing."""
        _enrol_row(project_db, db_flag="demo", owner_id=None)
        row = main_module._fetch_or_create_database_config(
            _request("demo"), project_db, owner_id=INTRUDER
        )
        assert row.db_flag == "demo"

    def test_a_fresh_identifier_is_created_for_the_caller(self, project_db, monkeypatch):
        # Creating a row stores a credential, and this deployment mode requires it encrypted.
        from cryptography.fernet import Fernet

        monkeypatch.setenv("DBW_SECRET_KEYS", Fernet.generate_key().decode())
        row = main_module._fetch_or_create_database_config(
            _request("brand_new"), project_db, owner_id=INTRUDER
        )
        assert row.owner_id == INTRUDER
        assert row.connection_secret, "a new row must be stored encrypted"

    def test_the_refusal_is_audited(self, project_db):
        _enrol_row(project_db, db_flag="acme", owner_id=OWNER)
        sink = InMemoryAuditSink()
        with audit_to(sink), pytest.raises(HTTPException):
            main_module._fetch_or_create_database_config(
                _request("acme"), project_db, owner_id=INTRUDER
            )

        refusals = [e for e in sink.events if e.action is AuditAction.CONNECTION_ENROLL_REFUSED]
        assert len(refusals) == 1
        assert refusals[0].subject == "acme"
        assert "another owner" in refusals[0].reason
