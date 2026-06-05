"""Tests for the provider-agnostic embedding factory (no network/torch)."""

from __future__ import annotations

import pytest

from app.core import embeddings


def test_unknown_provider_raises():
    with pytest.raises(ValueError):
        embeddings.build_embedding_client("not-a-provider")


def test_google_provider_dispatch(monkeypatch):
    captured = {}

    def fake_google(model):
        captured["model"] = model
        return object()

    monkeypatch.setattr(embeddings, "_build_google_embeddings", fake_google)
    client = embeddings.build_embedding_client("google")
    assert client is not None
    assert captured["model"] == embeddings.DEFAULT_GOOGLE_MODEL


def test_gemini_alias_dispatch(monkeypatch):
    monkeypatch.setattr(embeddings, "_build_google_embeddings", lambda m: "google-client")
    assert embeddings.build_embedding_client("gemini") == "google-client"


def test_huggingface_dispatch(monkeypatch):
    monkeypatch.setattr(embeddings, "_build_huggingface_embeddings", lambda m: "hf-client")
    assert embeddings.build_embedding_client("huggingface") == "hf-client"
