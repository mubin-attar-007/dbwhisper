"""The embedding contract, and the version stamp that makes vectors safe to store.

Vectors from two different models are not comparable, and a cosine similarity between them is
meaningless rather than merely inaccurate. The v1 code had no way to tell them apart, so changing
``EMBEDDING_PROVIDER`` silently corrupted retrieval until someone re-enrolled every database.

Here every vector carries an :class:`EmbeddingVersion` - provider, model, dimension, normalisation -
and the retrieval layer filters on it. Changing the model produces a *new* version that is indexed
alongside the old one, and the old index keeps serving until the re-embed job finishes.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class EmbeddingVersion:
    """Everything needed to decide whether two vectors may be compared."""

    provider: str
    model: str
    dimension: int
    normalized: bool = True
    revision: str = "1"

    @property
    def key(self) -> str:
        """Stable identifier stored next to every vector."""
        return f"{self.provider}:{self.model}:{self.dimension}:{'n' if self.normalized else 'r'}:{self.revision}"

    def compatible_with(self, other: EmbeddingVersion) -> bool:
        return self.key == other.key

    def __str__(self) -> str:
        return self.key


@dataclass(slots=True)
class EmbeddingResult:
    vectors: list[list[float]]
    version: EmbeddingVersion
    content_hashes: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.vectors)


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Turns text into vectors. Implementations must be deterministic for the same input."""

    name: str

    @property
    def version(self) -> EmbeddingVersion: ...

    def embed_documents(self, texts: list[str]) -> EmbeddingResult:
        """Embed a batch for indexing."""
        ...

    def embed_query(self, text: str) -> list[float]:
        """Embed one query. Some models use a different prefix for queries than for documents."""
        ...


def content_hash(text: str) -> str:
    """Identifies the exact text a vector was produced from, so stale rows can be found."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def normalize(vector: list[float]) -> list[float]:
    magnitude = math.sqrt(sum(component * component for component in vector))
    if magnitude == 0:
        return vector
    return [component / magnitude for component in vector]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    """Cosine similarity, guarding the zero-vector case rather than dividing by zero."""
    if len(left) != len(right):
        raise ValueError(f"Vectors have different dimensions: {len(left)} vs {len(right)}")
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_magnitude = math.sqrt(sum(a * a for a in left))
    right_magnitude = math.sqrt(sum(b * b for b in right))
    if left_magnitude == 0 or right_magnitude == 0:
        return 0.0
    return dot / (left_magnitude * right_magnitude)


__all__ = [
    "EmbeddingProvider",
    "EmbeddingResult",
    "EmbeddingVersion",
    "content_hash",
    "cosine_similarity",
    "normalize",
]
