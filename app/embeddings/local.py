"""CPU embeddings via fastembed (ONNX Runtime) - the default, and the reason no key is needed.

fastembed was chosen over ``sentence-transformers`` for one practical reason: it runs the model
through ONNX Runtime instead of PyTorch, so the install is tens of megabytes rather than a
multi-gigabyte CUDA-capable stack that most deployments never use. The default model,
``BAAI/bge-small-en-v1.5``, is 384-dimensional, MIT-licensed, and fast enough on a laptop CPU to
embed a schema in seconds.

The model file is downloaded once on first use and cached. That download is why CI uses the fake
provider instead: a test suite that reaches the network is a test suite that fails on a train.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.embeddings.base import EmbeddingResult, EmbeddingVersion, content_hash

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
#: Dimensions of the models we have verified. Anything else is measured on first use.
KNOWN_DIMENSIONS: dict[str, int] = {
    "BAAI/bge-small-en-v1.5": 384,
    "BAAI/bge-base-en-v1.5": 768,
    "sentence-transformers/all-MiniLM-L6-v2": 384,
    "intfloat/multilingual-e5-small": 384,
}

#: bge models were trained with an instruction prefix on the query side only.
QUERY_PREFIXES: dict[str, str] = {
    "BAAI/bge-small-en-v1.5": "Represent this sentence for searching relevant passages: ",
    "BAAI/bge-base-en-v1.5": "Represent this sentence for searching relevant passages: ",
    "intfloat/multilingual-e5-small": "query: ",
}


class LocalEmbeddingsUnavailable(RuntimeError):
    """fastembed is not installed, or the model could not be loaded."""


@dataclass
class LocalEmbeddingProvider:
    """Local, CPU-only embeddings. No API key, no network after the first model download."""

    model_name: str = DEFAULT_MODEL
    name: str = "fastembed"
    cache_dir: str | None = None
    batch_size: int = 32
    _model: Any = field(default=None, repr=False)
    _dimension: int | None = field(default=None, repr=False)

    def _load(self) -> Any:
        if self._model is None:
            try:
                from fastembed import TextEmbedding
            except ImportError as exc:  # pragma: no cover - depends on the install
                raise LocalEmbeddingsUnavailable(
                    "Local embeddings need the 'fastembed' package. Install it with "
                    "'uv sync', or set EMBEDDING_PROFILE=fake for offline testing."
                ) from exc
            try:
                self._model = TextEmbedding(model_name=self.model_name, cache_dir=self.cache_dir)
            except Exception as exc:
                raise LocalEmbeddingsUnavailable(
                    f"Could not load the local embedding model '{self.model_name}': {exc}"
                ) from exc
            logger.info("Local embedding model ready: %s", self.model_name)
        return self._model

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            known = KNOWN_DIMENSIONS.get(self.model_name)
            self._dimension = known if known else len(self._embed_raw(["dimension probe"])[0])
        return self._dimension

    @property
    def version(self) -> EmbeddingVersion:
        return EmbeddingVersion(
            provider=self.name, model=self.model_name, dimension=self.dimension, normalized=True
        )

    def _embed_raw(self, texts: list[str]) -> list[list[float]]:
        model = self._load()
        try:
            return [
                list(map(float, vector))
                for vector in model.embed(texts, batch_size=self.batch_size)
            ]
        except Exception as exc:
            raise LocalEmbeddingsUnavailable(f"Local embedding failed: {exc}") from exc

    def embed_documents(self, texts: list[str]) -> EmbeddingResult:
        if not texts:
            return EmbeddingResult(vectors=[], version=self.version, content_hashes=[])
        vectors = self._embed_raw(texts)
        return EmbeddingResult(
            vectors=vectors,
            version=self.version,
            content_hashes=[content_hash(text) for text in texts],
        )

    def embed_query(self, text: str) -> list[float]:
        prefix = QUERY_PREFIXES.get(self.model_name, "")
        return self._embed_raw([prefix + text])[0]


__all__ = [
    "DEFAULT_MODEL",
    "KNOWN_DIMENSIONS",
    "QUERY_PREFIXES",
    "LocalEmbeddingProvider",
    "LocalEmbeddingsUnavailable",
]
