"""Tests for the per-IP rate-limiting middleware (app/security/ratelimit.py)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.utils.tool_cache as tool_cache
from app.core.config import Settings
from app.security import ratelimit
from app.security.ratelimit import RateLimitMiddleware


def _client(settings: Settings, monkeypatch) -> TestClient:
    monkeypatch.setattr(ratelimit, "get_settings", lambda: settings)
    tool_cache._rate_limiters.clear()  # isolate limiter state between tests

    app = FastAPI()
    app.add_middleware(RateLimitMiddleware)

    @app.get("/query")
    def q():
        return {"ok": True}

    @app.get("/health")
    def h():
        return {"ok": True}

    return TestClient(app)


def test_query_bucket_returns_429(monkeypatch):
    settings = Settings(
        rate_limit_enabled=True, query_rate_limit_burst=2, query_rate_limit_per_sec=0.0
    )
    c = _client(settings, monkeypatch)
    assert c.get("/query").status_code == 200
    assert c.get("/query").status_code == 200
    blocked = c.get("/query")
    assert blocked.status_code == 429
    assert blocked.headers.get("Retry-After") == "60"
    assert blocked.headers.get("content-type", "").startswith("application/problem+json")


def test_health_is_exempt(monkeypatch):
    settings = Settings(rate_limit_enabled=True, rate_limit_burst=1, rate_limit_per_sec=0.0)
    c = _client(settings, monkeypatch)
    for _ in range(5):
        assert c.get("/health").status_code == 200


def test_disabled_passes_through(monkeypatch):
    settings = Settings(rate_limit_enabled=False, query_rate_limit_burst=1)
    c = _client(settings, monkeypatch)
    for _ in range(5):
        assert c.get("/query").status_code == 200
