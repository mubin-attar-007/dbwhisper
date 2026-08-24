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


def test_every_setting_is_documented_in_env_example():
    """A setting nobody can discover is a setting nobody will set correctly.

    `.env.example` is the only place an operator learns what is configurable, so a new field on
    `Settings` that never reaches it is invisible until something behaves unexpectedly in
    production. This is the same class of check as the CSRF route sweep: the module was fine, the
    seam was not.
    """
    from pathlib import Path

    env_example = Path(__file__).resolve().parents[1] / ".env.example"
    text = env_example.read_text(encoding="utf-8")

    undocumented = sorted(
        (field.alias or name).upper()
        for name, field in Settings.model_fields.items()
        if (field.alias or name).upper() not in text and name.upper() not in text
    )
    assert undocumented == [], (
        "these settings exist on Settings but are absent from .env.example, so nobody deploying "
        "this can discover them: " + ", ".join(undocumented)
    )


def test_env_example_does_not_ship_a_real_secret():
    """The template is committed; a filled-in value in it is a leaked credential."""
    import re
    from pathlib import Path

    env_example = Path(__file__).resolve().parents[1] / ".env.example"
    suspicious: list[str] = []
    for line in env_example.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        if not value:
            continue
        looks_secret = re.search(r"KEY|TOKEN|SECRET|PASSWORD|DSN", key, re.IGNORECASE)
        # A documented non-secret default (a port, a boolean, a profile name) is fine; a filled-in
        # credential-shaped value is not.
        if looks_secret and not re.fullmatch(
            r"(|false|true|0|\d+|fake|local|<.*>|change-?me)", value, re.IGNORECASE
        ):
            suspicious.append(f"{key}={value[:12]}...")
    assert suspicious == [], "credential-shaped values committed in .env.example: " + ", ".join(
        suspicious
    )
