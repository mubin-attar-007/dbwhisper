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
    """Ensure the PGVector extension and an index exist for embeddings.

    This function tries to create the `vector` extension and an ivfflat index on
    `langchain_pg_embedding.embedding` if it doesn't exist. It's safe to call multiple times.
    """
    engine = database_manager.get_engine(connection_string)
    with engine.connect() as conn:
        try:
            # Ensure the vector extension is present
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))

            # Detect whether the 'embedding' column is a PGVector (udt_name == 'vector') before
            # attempting an ivfflat index. If the column is JSONB / jsonb array, creating a
            # native vector index will fail; skip the index in that case.
            try:
                col_q = text(
                    "SELECT udt_name FROM information_schema.columns "
                    "WHERE table_name = 'langchain_pg_embedding' AND column_name = 'embedding' LIMIT 1"
                )
                row = conn.execute(col_q).fetchone()
                udt_name = row[0] if row and len(row) > 0 else None
            except Exception:
                # Could not detect column type: fall back to attempting to create the index and
                # let it fail gracefully; do not raise on metadata differences.
                udt_name = None

            if udt_name != "vector":
                logger.info(
                    "Skipping ivfflat index creation because langchain_pg_embedding.embedding "
                    "is not a vector column (udt=%s)",
                    udt_name,
                )
            else:
                # ivfflat index on the canonical langchain embedding table (L2 distance).
                index_ddl = (
                    "CREATE INDEX IF NOT EXISTS langchain_pg_embedding_ivfflat_idx "
                    "ON langchain_pg_embedding USING ivfflat (embedding vector_l2_ops) WITH (lists = 100)"
                )
                conn.execute(text(index_ddl))
            with contextlib.suppress(Exception):
                # Some engines/older SQLAlchemy may not implement commit on this proxy; ignore.
                conn.commit()
        except Exception as exc:
            logger.debug(
                "PGVector index creation issue: %s", sanitize_for_log(str(exc), max_len=300)
            )
            raise
