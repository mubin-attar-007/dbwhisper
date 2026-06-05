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
