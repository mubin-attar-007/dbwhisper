"""Tests for typed application settings and the GEMINI->GOOGLE key alias."""

from __future__ import annotations

import os

from app.core.config import Settings, _normalize_google_key


def test_cors_wildcard():
    assert Settings(cors_allow_origins="*").cors_origins_list == ["*"]


def test_cors_empty_is_wildcard():
    assert Settings(cors_allow_origins="").cors_origins_list == ["*"]


def test_cors_comma_list():
    s = Settings(cors_allow_origins="https://a.com, https://b.com")
    assert s.cors_origins_list == ["https://a.com", "https://b.com"]


def test_is_production():
    assert Settings(app_env="production").is_production
    assert Settings(app_env="PROD").is_production
    assert not Settings(app_env="development").is_production


def test_gemini_alias_populates_google_key(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-123")
    settings = Settings()
    _normalize_google_key(settings)
    assert os.environ.get("GOOGLE_API_KEY") == "test-key-123"


def test_gemini_alias_does_not_override_existing(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "already-set")
    monkeypatch.setenv("GEMINI_API_KEY", "other")
    settings = Settings()
    _normalize_google_key(settings)
    assert os.environ.get("GOOGLE_API_KEY") == "already-set"


def test_mode_defaults_to_self_hosted_and_legacy_env_maps():
    from app.platform.modes import AppMode, EgressPolicy

    assert Settings(app_env="development").mode is AppMode.SELF_HOSTED
    assert Settings(app_env="production").mode is AppMode.PRODUCTION
    assert Settings(app_mode="demo", app_env="production").mode is AppMode.DEMO
    assert Settings(app_mode="demo").mode_policy.allow_arbitrary_connections is False
    assert Settings(app_mode="self_hosted").effective_egress_policy is EgressPolicy.LOCAL_ONLY
    assert (
        Settings(app_mode="self_hosted", egress_policy="remote_allowed").effective_egress_policy
        is EgressPolicy.REMOTE_ALLOWED
    )


def test_secret_keys_and_allowlist_parse():
    assert Settings(dbw_secret_keys="a, b,").secret_keys_list == ["a", "b"]
    assert Settings(dbw_secret_keys=None).secret_keys_list == []
    assert Settings(network_allowlist="10.0.0.0/8, db.internal").network_allowlist_list == [
        "10.0.0.0/8",
        "db.internal",
    ]


def test_cookie_secure_follows_mode_policy():
    assert Settings(app_mode="demo").cookie_secure is True
    assert Settings(app_mode="self_hosted").cookie_secure is False
    assert Settings(app_mode="production").cookie_secure is True
    assert Settings(app_mode="production", session_cookie_secure=False).cookie_secure is False
