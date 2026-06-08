"""Tests for the API-key auth dependency (app/security/auth.py)."""

from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.security.auth import require_api_key, require_api_key_if_enabled


def _client(settings: Settings) -> TestClient:
    app = FastAPI()

    @app.post("/protected", dependencies=[Depends(require_api_key)])
    def protected():
        return {"ok": True}

    @app.post("/maybe", dependencies=[Depends(require_api_key_if_enabled)])
    def maybe():
        return {"ok": True}

    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


def test_dev_no_tokens_allows():
    c = _client(Settings(app_env="development", api_auth_tokens=None))
    assert c.post("/protected").status_code == 200


def test_required_but_no_tokens_503():
    c = _client(Settings(auth_required=True, api_auth_tokens=None))
    assert c.post("/protected").status_code == 503


def test_production_no_tokens_fails_closed():
    c = _client(Settings(app_env="production", api_auth_tokens=None))
    assert c.post("/protected").status_code == 503


def test_tokens_require_valid_key():
    c = _client(Settings(api_auth_tokens="secret1, secret2"))
    assert c.post("/protected").status_code == 401
    assert c.post("/protected", headers={"X-API-Key": "wrong"}).status_code == 401
    assert c.post("/protected", headers={"X-API-Key": "secret1"}).status_code == 200
    assert c.post("/protected", headers={"X-API-Key": "secret2"}).status_code == 200


def test_query_gate_toggle():
    off = _client(Settings(api_auth_tokens="s", gate_query_endpoint=False))
    assert off.post("/maybe").status_code == 200  # open even with tokens, gate off

    on = _client(Settings(api_auth_tokens="s", gate_query_endpoint=True))
    assert on.post("/maybe").status_code == 401
    assert on.post("/maybe", headers={"X-API-Key": "s"}).status_code == 200
