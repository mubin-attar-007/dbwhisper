"""Integration tests for /auth/* against a sqlite-backed project DB."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def auth_client(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path.as_posix()}/auth.db"
    from db.database_manager import create_metadata_tables

    create_metadata_tables(db_url)
    for mod in ("app.api.auth", "app.security.sessions", "app.security.user_auth"):
        monkeypatch.setattr(f"{mod}.get_project_db_connection_string", lambda: db_url)

    from app.main import app

    return TestClient(app)


def test_register_login_me_logout_flow(auth_client) -> None:
    r = auth_client.post("/auth/register", json={"email": "A@B.com", "password": "supersecret1"})
    assert r.status_code == 201, r.text
    assert r.json()["email"] == "a@b.com"  # normalized lowercase

    r = auth_client.get("/auth/me")
    assert r.status_code == 200
    assert r.json()["email"] == "a@b.com"

    assert auth_client.post("/auth/logout").status_code == 204
    assert auth_client.get("/auth/me").status_code == 401


def test_login_wrong_password_401(auth_client) -> None:
    auth_client.post("/auth/register", json={"email": "c@d.com", "password": "supersecret1"})
    auth_client.post("/auth/logout")
    r = auth_client.post("/auth/login", json={"email": "c@d.com", "password": "wrongpassword"})
    assert r.status_code == 401


def test_duplicate_register_409(auth_client) -> None:
    auth_client.post("/auth/register", json={"email": "e@f.com", "password": "supersecret1"})
    r = auth_client.post("/auth/register", json={"email": "e@f.com", "password": "supersecret1"})
    assert r.status_code == 409


def test_register_rejects_short_password(auth_client) -> None:
    r = auth_client.post("/auth/register", json={"email": "g@h.com", "password": "short"})
    assert r.status_code == 422
