"""Provider-agnostic embedding client factory.

The embedding provider is selected at runtime via the ``EMBEDDING_PROVIDER`` env var:

* ``google`` (default)  -- hosted Google Generative AI embeddings. No heavy local
  dependencies, so the deployed image stays small and cold-starts quickly.
* ``huggingface``       -- local/offline embeddings via ``langchain-huggingface``.
  Requires the optional ``local-embeddings`` extra (``uv sync --extra local-embeddings``),
  which pulls in ``torch`` + ``sentence-transformers``.

Heavy imports are performed lazily *inside* the builders so that importing this module
(and the wider app) never drags in ``torch`` unless the HuggingFace provider is used.

IMPORTANT: embeddings are provider/model-specific. Vectors written under one provider
are dimensionally incompatible with queries from another — re-enroll/re-embed schemas
whenever ``EMBEDDING_PROVIDER`` or the model changes.
"""

from __future__ import annotations

import os
from threading import Lock
from typing import Any

from app.utils.logger import setup_logging

logger = setup_logging(__name__)

DEFAULT_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "google").strip().lower()
DEFAULT_GOOGLE_MODEL = os.getenv("GOOGLE_EMBEDDING_MODEL", "models/gemini-embedding-001")
DEFAULT_HF_MODEL = os.getenv("EMBEDDING_MODEL_NAME", "jinaai/jina-embeddings-v3")

_GOOGLE_ALIASES = {"google", "gemini", "googleai", "google-genai"}
_HF_ALIASES = {"huggingface", "hf", "local", "sentence-transformers"}

_lock = Lock()
_instance: Any | None = None


def _build_google_embeddings(model: str) -> Any:
    """Construct a hosted Google Generative AI embedding client (lazy import)."""
    from langchain_google_genai import GoogleGenerativeAIEmbeddings

    # langchain-google-genai reads GOOGLE_API_KEY; app.core.config aliases GEMINI_API_KEY.
    return GoogleGenerativeAIEmbeddings(model=model)


def _build_huggingface_embeddings(model: str) -> Any:
    """Construct a local HuggingFace embedding client (lazy import; needs torch)."""
    try:
        from langchain_huggingface import HuggingFaceEmbeddings
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise RuntimeError(
            "EMBEDDING_PROVIDER=huggingface requires the optional 'local-embeddings' "
            "dependencies (torch, sentence-transformers). Install them with: "
            "uv sync --extra local-embeddings"
        ) from exc

    model_kwargs: dict[str, Any] = {"trust_remote_code": True}
    dtype = os.getenv("HF_DTYPE") or os.getenv("HF_TORCH_DTYPE")
    if dtype:
        model_kwargs["dtype"] = dtype
    return HuggingFaceEmbeddings(model_name=model, model_kwargs=model_kwargs)


def build_embedding_client(provider: str | None = None) -> Any:
    """Build a fresh embedding client for the given (or env-configured) provider."""
    provider = (provider or DEFAULT_PROVIDER).strip().lower()
    if provider in _GOOGLE_ALIASES:
        logger.info("Initializing Google embeddings (model=%s)", DEFAULT_GOOGLE_MODEL)
        return _build_google_embeddings(DEFAULT_GOOGLE_MODEL)
    if provider in _HF_ALIASES:
        logger.info("Initializing HuggingFace embeddings (model=%s)", DEFAULT_HF_MODEL)
        return _build_huggingface_embeddings(DEFAULT_HF_MODEL)
    raise ValueError(f"Unsupported EMBEDDING_PROVIDER '{provider}'. Use 'google' or 'huggingface'.")


def get_embedding_client() -> Any:
    """Return a process-wide cached embedding client."""
    global _instance
    if _instance is not None:
        return _instance
    with _lock:
        if _instance is None:
            _instance = build_embedding_client()
        return _instance
