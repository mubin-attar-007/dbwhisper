"""Vector store helpers for schema retrieval."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
from threading import Lock
from typing import Any

from langchain_core.documents import Document
from langchain_postgres import PGVector
from sqlalchemy import text

from app.utils.logger import sanitize_for_log, setup_logging
from app.utils.tool_cache import default_cache, get_rate_limiter
from db import database_manager

logger = setup_logging(__name__)

_vector_store_lock = Lock()
_vector_store_cache: dict[str, PGVector] = {}


def default_collection_name(db_flag: str) -> str:
    """Resolve the PGVector collection name for a database flag."""
    normalized_flag = (db_flag or "").strip()
    return f"{normalized_flag}_docs"


def get_embeddings() -> Any:
    """Return the process-wide embedding client.

    The provider (google | huggingface) is configured via EMBEDDING_PROVIDER; see
    ``app.core.embeddings``. Kept as a thin wrapper for backward compatibility.
    """
    from app.core.embeddings import get_embedding_client

    return get_embedding_client()


def get_vector_store(collection_name: str) -> PGVector:
    """Return a cached PGVector instance for the collection."""
    connection_string = os.getenv("POSTGRES_CONNECTION_STRING")
    if not connection_string:
        raise RuntimeError("POSTGRES_CONNECTION_STRING environment variable is required")
    if collection_name in _vector_store_cache:
        return _vector_store_cache[collection_name]
    with _vector_store_lock:
        if collection_name in _vector_store_cache:
            return _vector_store_cache[collection_name]
        logger.debug(
            "Creating PGVector client for collection=%s (masked=%s)",
            collection_name,
            sanitize_for_log(collection_name),
        )
        store = PGVector(
            embeddings=get_embeddings(),
            collection_name=collection_name,
            connection=connection_string,
            use_jsonb=True,
        )
        # Ensure extension and index exist for faster similarity search.
        try:
            _ensure_pgvector_index(connection_string)
        except Exception as exc:
            logger.warning(
                "Failed to ensure PGVector index: %s", sanitize_for_log(str(exc), max_len=300)
            )
        _vector_store_cache[collection_name] = store
        return store


def vector_search(
    query: str,
    collection_name: str,
    filters: dict[str, Any] | None = None,
    k: int = 3,
) -> list[Document]:
    """Run a similarity search and return top matching documents."""
    store = get_vector_store(collection_name)

    # Build a deterministic cache key for the query and parameters
    key_payload = {
        "q": query,
        "collection": collection_name,
        "filters": dict(filters or {}),
        "k": k,
    }
    key_str = json.dumps(key_payload, sort_keys=True, default=str)
    key_hash = hashlib.sha256(key_str.encode("utf-8")).hexdigest()

    # Try to satisfy request from cache first
    cached = default_cache.get(key_hash)
    if cached is not None:
        logger.debug("vector_search cache hit collection=%s key=%s", collection_name, key_hash[:8])
        return cached

    # Rate limit per collection (tool guard) - allow some burst and refill rate
    limiter = get_rate_limiter(collection_name)
    if not limiter.allow():
        # If rate limited, return cached response if available, otherwise empty result
        logger.warning("vector_search rate-limited for collection=%s", collection_name)
        if cached is not None:
            return cached
        return []

    results = store.similarity_search(query, k=k, filter=filters or None)
    logger.debug(
        "vector_search collection=%s filters=%s hits=%d",
        collection_name,
        filters,
        len(results),
    )
    # Store results in the TTL cache for repeat queries
    try:
        default_cache.set(key_hash, results)
    except Exception:
        logger.debug("Failed to set vector_search cache for collection=%s", collection_name)
    return results


def _ensure_pgvector_index(connection_string: str) -> None:
    """Ensure the `vector` extension exists and (best-effort) build an ivfflat index.

    The extension is required; the index is an optional optimization. ivfflat only supports
    vector columns of <=2000 dimensions, so high-dimensional models (e.g. gemini-embedding-001
    at 3072 dims) skip the index and rely on sequential scan — which is fine for modest
    collections. Index failures are non-fatal and never raised. Safe to call repeatedly.
    """
    engine = database_manager.get_engine(connection_string)
    with engine.connect() as conn:
        # 1) Extension (required for vector storage).
        try:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
            with contextlib.suppress(Exception):
                conn.commit()
        except Exception as exc:
            logger.debug(
                "Could not ensure 'vector' extension: %s", sanitize_for_log(str(exc), max_len=300)
            )
            return

        # 2) ivfflat index (best-effort optimization; never fatal).
        try:
            row = conn.execute(
                text(
                    "SELECT udt_name FROM information_schema.columns "
                    "WHERE table_name = 'langchain_pg_embedding' AND column_name = 'embedding' LIMIT 1"
                )
            ).fetchone()
            if not row or row[0] != "vector":
                return  # table not created yet, or not a native vector column

            dims = conn.execute(
                text("SELECT vector_dims(embedding) FROM langchain_pg_embedding LIMIT 1")
            ).scalar()
            if dims is not None and dims > 2000:
                logger.info(
                    "Skipping ivfflat index: embedding dim=%s exceeds pgvector's 2000-dim "
                    "index limit (sequential scan will be used).",
                    dims,
                )
                return

            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS langchain_pg_embedding_ivfflat_idx "
                    "ON langchain_pg_embedding USING ivfflat (embedding vector_l2_ops) WITH (lists = 100)"
                )
            )
            with contextlib.suppress(Exception):
                conn.commit()
        except Exception as exc:
            logger.debug(
                "Skipped ivfflat index (non-fatal): %s", sanitize_for_log(str(exc), max_len=300)
            )
