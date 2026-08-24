"""Deterministic hash embeddings for CI and offline development.

These are real vectors with real geometry - the same text always produces the same vector, and
similar texts share hashed features, so nearest-neighbour behaviour is *plausible* rather than
random. That is enough to test indexing, filtering, fusion and ranking mechanics end to end.

It is not enough to measure retrieval quality. Any evaluation of recall must run with a real
embedding model, and the report has to say which one; a Recall@k figure produced with this provider
would be a number about hashing, not about retrieval.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from app.embeddings.base import EmbeddingResult, EmbeddingVersion, content_hash, normalize

DEFAULT_DIMENSION = 64
_TOKEN = re.compile(r"[a-z0-9_]+")


@dataclass
class FakeEmbeddingProvider:
    """Hashed bag-of-tokens vectors. Deterministic across processes and platforms."""

    dimension: int = DEFAULT_DIMENSION
    name: str = "fake"
    model_name: str = "hashed-tokens"

    @property
    def version(self) -> EmbeddingVersion:
        return EmbeddingVersion(
            provider=self.name, model=self.model_name, dimension=self.dimension, normalized=True
        )

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        tokens = _TOKEN.findall((text or "").lower())
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimension
            # A second hashed bucket keeps distinct tokens from colliding into one axis.
            second = int.from_bytes(digest[4:8], "big") % self.dimension
            sign = 1.0 if digest[8] % 2 == 0 else -1.0
            vector[index] += sign
            vector[second] += sign * 0.5
        return normalize(vector)

    def embed_documents(self, texts: list[str]) -> EmbeddingResult:
        return EmbeddingResult(
            vectors=[self._vector(text) for text in texts],
            version=self.version,
            content_hashes=[content_hash(text) for text in texts],
        )

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


__all__ = ["DEFAULT_DIMENSION", "FakeEmbeddingProvider"]
