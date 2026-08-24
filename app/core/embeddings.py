"""LangChain-compatible embedding client, backed by :mod:`app.embeddings`.

``PGVector`` expects an object with ``embed_documents`` and ``embed_query`` returning plain lists.
The v2 providers return a versioned :class:`~app.embeddings.base.EmbeddingResult` instead, because a
bare list of floats cannot tell you which model produced it. This adapter bridges the two, and
exposes the version so the caller can record it alongside whatever it stores.

Selection is controlled by ``EMBEDDING_PROFILE`` (``auto`` -> local CPU model, ``fake`` -> CI,
``google`` -> hosted). The legacy ``EMBEDDING_PROVIDER`` variable is still honoured so existing
deployments keep working, with a warning pointing at the replacement.
"""

from __future__ import annotations

import os
from threading import Lock
from typing import Any

from app.embeddings.base import EmbeddingVersion
from app.embeddings.service import build_embedding_provider
from app.utils.logger import setup_logging

logger = setup_logging(__name__)

_LEGACY_PROVIDER_TO_PROFILE = {
    "google": "google",
    "gemini": "google",
    "googleai": "google",
    "google-genai": "google",
    "huggingface": "auto",
    "hf": "auto",
    "local": "auto",
    "sentence-transformers": "auto",
    "fastembed": "auto",
    "fake": "fake",
}

_lock = Lock()
_instance: Any | None = None


class EmbeddingClient:
    """Adapter presenting a v2 provider through the interface LangChain stores expect."""

    def __init__(self, provider: Any) -> None:
        self._provider = provider

    @property
    def version(self) -> EmbeddingVersion:
        return self._provider.version

    @property
    def provider_name(self) -> str:
        return getattr(self._provider, "name", "unknown")

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._provider.embed_documents(list(texts)).vectors

    def embed_query(self, text: str) -> list[float]:
        return self._provider.embed_query(text)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"EmbeddingClient({self.version.key})"


def resolve_profile(env: dict[str, str] | None = None) -> str:
    """Work out the embedding profile, honouring the legacy variable with a deprecation notice."""
    environment = env if env is not None else os.environ
    profile = (environment.get("EMBEDDING_PROFILE") or "").strip().lower()
    if profile:
        return profile
    legacy = (environment.get("EMBEDDING_PROVIDER") or "").strip().lower()
    if legacy:
        mapped = _LEGACY_PROVIDER_TO_PROFILE.get(legacy, "auto")
        logger.warning(
            "EMBEDDING_PROVIDER=%s is deprecated; use EMBEDDING_PROFILE=%s "
            "(auto = local CPU model, no API key required).",
            legacy,
            mapped,
        )
        return mapped
    return "auto"


def build_embedding_client(profile: str | None = None) -> EmbeddingClient:
    """Build a fresh client for the given (or configured) profile."""
    return EmbeddingClient(build_embedding_provider(profile or resolve_profile()))


def get_embedding_client() -> EmbeddingClient:
    """Process-wide cached client."""
    global _instance
    if _instance is not None:
        return _instance
    with _lock:
        if _instance is None:
            _instance = build_embedding_client()
            logger.info("Embedding client ready: %s", _instance.version.key)
        return _instance


def reset() -> None:
    """Drop the cached client (tests, and after a configuration change)."""
    global _instance
    _instance = None


__all__ = [
    "EmbeddingClient",
    "build_embedding_client",
    "get_embedding_client",
    "reset",
    "resolve_profile",
]
