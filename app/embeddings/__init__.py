"""Embeddings: local CPU vectors by default, versioned so incompatible ones can never be mixed."""

from app.embeddings.base import (
    EmbeddingProvider,
    EmbeddingResult,
    EmbeddingVersion,
    content_hash,
    cosine_similarity,
    normalize,
)
from app.embeddings.fake import FakeEmbeddingProvider
from app.embeddings.local import LocalEmbeddingProvider
from app.embeddings.service import (
    IncompatibleEmbeddingVersion,
    build_embedding_provider,
    get_embedding_provider,
    require_compatible,
)

__all__ = [
    "EmbeddingProvider",
    "EmbeddingResult",
    "EmbeddingVersion",
    "FakeEmbeddingProvider",
    "IncompatibleEmbeddingVersion",
    "LocalEmbeddingProvider",
    "build_embedding_provider",
    "content_hash",
    "cosine_similarity",
    "get_embedding_provider",
    "normalize",
    "require_compatible",
]
