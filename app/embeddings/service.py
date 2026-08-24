"""Choosing an embedding provider, and refusing to mix incompatible vectors.

``EMBEDDING_PROFILE`` selects: ``auto`` (local, falling back to a configured hosted provider),
``fake`` (CI), ``local-*`` or ``google``. The default is local - the point of the v2 program is that
a working install needs no API key.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Any

from app.embeddings.base import EmbeddingProvider, EmbeddingVersion
from app.embeddings.fake import FakeEmbeddingProvider
from app.embeddings.local import DEFAULT_MODEL, LocalEmbeddingProvider

logger = logging.getLogger(__name__)


class GoogleEmbeddingProvider:
    """Hosted Google embeddings, kept for deployments that already use them. Requires a key."""

    name = "google"

    def __init__(self, model_name: str = "models/gemini-embedding-001", dimension: int = 3072):
        self.model_name = model_name
        self._dimension = dimension
        self._client: Any = None

    @property
    def version(self) -> EmbeddingVersion:
        return EmbeddingVersion(
            provider=self.name, model=self.model_name, dimension=self._dimension, normalized=False
        )

    def _load(self) -> Any:
        if self._client is None:
            from langchain_google_genai import GoogleGenerativeAIEmbeddings

            self._client = GoogleGenerativeAIEmbeddings(model=self.model_name)
        return self._client

    def embed_documents(self, texts: list[str]):
        from app.embeddings.base import EmbeddingResult, content_hash

        vectors = self._load().embed_documents(texts) if texts else []
        return EmbeddingResult(
            vectors=[list(map(float, v)) for v in vectors],
            version=self.version,
            content_hashes=[content_hash(t) for t in texts],
        )

    def embed_query(self, text: str) -> list[float]:
        return [float(v) for v in self._load().embed_query(text)]


def build_embedding_provider(
    selection: str = "auto", *, env: dict[str, str] | None = None
) -> EmbeddingProvider:
    """Construct the provider named by ``EMBEDDING_PROFILE``."""
    environment = env if env is not None else os.environ
    chosen = (selection or "auto").strip().lower()

    if chosen == "fake":
        return FakeEmbeddingProvider()
    if chosen in {"google", "gemini"}:
        return GoogleEmbeddingProvider()
    if chosen.startswith("local"):
        model = environment.get("EMBEDDING_MODEL_NAME") or DEFAULT_MODEL
        return LocalEmbeddingProvider(model_name=model)
    if chosen in {"auto", ""}:
        model = environment.get("EMBEDDING_MODEL_NAME") or DEFAULT_MODEL
        return LocalEmbeddingProvider(model_name=model)
    raise ValueError(
        f"Unknown EMBEDDING_PROFILE '{selection}'. Use auto, fake, local-bge-small or google."
    )


@lru_cache(maxsize=1)
def get_embedding_provider() -> EmbeddingProvider:
    from app.core.config import get_settings

    return build_embedding_provider(get_settings().embedding_profile)


def reset() -> None:
    get_embedding_provider.cache_clear()


class IncompatibleEmbeddingVersion(ValueError):
    """A query vector and an index were produced by different models."""


def require_compatible(query: EmbeddingVersion, index: EmbeddingVersion) -> None:
    """Refuse to compare vectors from different models, loudly and early."""
    if not query.compatible_with(index):
        raise IncompatibleEmbeddingVersion(
            f"Query vectors ({query.key}) cannot be compared with index vectors ({index.key}). "
            "Re-embed the index, or switch back to the model it was built with."
        )


__all__ = [
    "GoogleEmbeddingProvider",
    "IncompatibleEmbeddingVersion",
    "build_embedding_provider",
    "get_embedding_provider",
    "require_compatible",
    "reset",
]
