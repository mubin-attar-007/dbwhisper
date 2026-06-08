"""Tenancy enforcement gate (_enforce_db_access) — active only when user_auth_enabled."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from db.database_manager import create_metadata_tables, get_session
from db.model import DatabaseConfig


def _seed(db_url: str) -> None:
    create_metadata_tables(db_url)
    s = get_session(db_url)
    try:
        s.add(DatabaseConfig(db_flag="demo", db_type="pg", connection_string="x", owner_id=None))
        s.add(DatabaseConfig(db_flag="secret", db_type="pg", connection_string="x", owner_id=999))
        s.commit()
    finally:
        s.close()


def _anon_request() -> Request:
    return Request({"type": "http", "headers": []})


def _point_conn(monkeypatch, db_url: str) -> None:
    monkeypatch.setattr("app.security.tenancy.get_project_db_connection_string", lambda: db_url)
    monkeypatch.setattr("app.security.user_auth.get_project_db_connection_string", lambda: db_url)


def test_disabled_is_noop(tmp_path, monkeypatch) -> None:
    db_url = f"sqlite:///{tmp_path.as_posix()}/t.db"
    _seed(db_url)
    _point_conn(monkeypatch, db_url)
    from app.core.config import get_settings
    from app.main import _enforce_db_access

    monkeypatch.setattr(get_settings(), "user_auth_enabled", False)
    _enforce_db_access(_anon_request(), "secret")  # gate off -> no raise


def test_anonymous_denied_on_private_db(tmp_path, monkeypatch) -> None:
    db_url = f"sqlite:///{tmp_path.as_posix()}/t.db"
    _seed(db_url)
    _point_conn(monkeypatch, db_url)
    from app.core.config import get_settings
    from app.main import _enforce_db_access

    monkeypatch.setattr(get_settings(), "user_auth_enabled", True)
    with pytest.raises(HTTPException) as exc:
        _enforce_db_access(_anon_request(), "secret")
    assert exc.value.status_code == 403


def test_anonymous_allowed_on_public_db(tmp_path, monkeypatch) -> None:
    db_url = f"sqlite:///{tmp_path.as_posix()}/t.db"
    _seed(db_url)
    _point_conn(monkeypatch, db_url)
    from app.core.config import get_settings
    from app.main import _enforce_db_access

    monkeypatch.setattr(get_settings(), "user_auth_enabled", True)
    _enforce_db_access(_anon_request(), "demo")  # public -> no raise
