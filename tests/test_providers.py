"""Tests for LLM provider availability / fallback ordering."""

from __future__ import annotations

from app.agent import chain


def _clear_provider_keys(monkeypatch):
    for _provider, key_env in chain.PROVIDER_PRIORITY:
        if key_env:
            monkeypatch.delenv(key_env, raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)


def test_gemini_always_available(monkeypatch):
    _clear_provider_keys(monkeypatch)
    providers = chain.get_available_providers()
    assert "gemini" in providers


def test_groq_detected_from_env(monkeypatch):
    _clear_provider_keys(monkeypatch)
    monkeypatch.setenv("GROQ_API_KEY", "x")
    providers = chain.get_available_providers()
    assert "groq" in providers
    # Priority places groq ahead of the always-on gemini fallback.
    assert providers[0] == "groq"


def test_preferred_provider_is_first(monkeypatch):
    _clear_provider_keys(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    assert chain.get_preferred_provider() == "openai"
