"""Assembling the v2 pipeline from configuration, once per process.

The graph is deliberately ignorant of where its collaborators come from - it receives a
:class:`~app.graph.deps.GraphDeps` and nothing else. This module is where that object is actually
built: it reads settings, resolves an enrolled ``db_flag`` into a connection (decrypting the stored
secret), builds the retrieval index from the enrolled schema artefacts, and picks a checkpointer.

Two things are cached because they are expensive and change rarely:

* the **retrieval index** per data source, keyed on the schema file's mtime, so editing the enrolled
  schema invalidates it without a restart;
* the **checkpointer**, which owns a database connection.

Nothing here is imported by the graph, the policy engine or the execution service - the dependency
arrow points one way, which is what keeps those packages testable without a running application.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.config import Settings, get_settings
from app.embeddings.service import get_embedding_provider
from app.graph.deps import DataSourceTarget, GraphDeps
from app.llm.service import get_router
from app.platform.connection_secrets import read_connection_string
from app.platform.paths import schema_index_path as safe_schema_index_path
from app.platform.paths import validate_source_id
from app.retrieval.base import SearchDocument
from app.retrieval.documents import documents_from_schema_index, verified_query_document
from app.retrieval.memory_index import InMemoryRetrievalIndex
from app.sqlpolicy.types import Dialect
from app.user_db_config_loader import PROJECT_ROOT

logger = logging.getLogger(__name__)

_index_lock = threading.Lock()
_index_cache: dict[str, tuple[int, InMemoryRetrievalIndex]] = {}


class UnknownDataSource(KeyError):
    """The requested db_flag is not enrolled."""


def schema_index_path(source_id: str) -> Path:
    """Validated: ``source_id`` reaches this from a URL, so it cannot be trusted as a path part."""
    return safe_schema_index_path(source_id)


# ---------------------------------------------------------------------------------------------
# Data sources
# ---------------------------------------------------------------------------------------------


def resolve_source(source_id: str) -> DataSourceTarget:
    """Turn an enrolled ``db_flag`` into something the execution service can connect with.

    The decrypted connection string exists only inside the returned object and only for as long as
    the caller holds it; it is never placed in graph state, a checkpoint or a log line.
    """
    from db.database_manager import get_project_db_connection_string, get_session
    from db.model import DatabaseConfig

    # Validated here, not only where a path is built: the identifier goes on to reach a database
    # query and several log lines, and a value carrying a newline can forge a log entry.
    source_id = validate_source_id(source_id)

    session = get_session(get_project_db_connection_string())
    try:
        row = session.query(DatabaseConfig).filter_by(db_flag=source_id).first()
        if row is None:
            raise UnknownDataSource(source_id)
        connection = read_connection_string(
            secret=getattr(row, "connection_secret", None), plaintext=row.connection_string
        )
        return DataSourceTarget(
            source_id=source_id,
            connection_string=connection,
            dialect=Dialect.from_db_type(row.db_type),
            max_rows=int(row.max_rows or 1000),
            timeout_seconds=int(row.query_timeout or 30),
            snapshot_id=_snapshot_id(source_id),
            description=row.description or "",
        )
    finally:
        session.close()


def _snapshot_id(source_id: str) -> str | None:
    """Identify the enrolled schema by its file mtime until real snapshots land (Phase 3 catalog)."""
    path = schema_index_path(source_id)
    return f"yaml:{path.stat().st_mtime_ns}" if path.is_file() else None


# ---------------------------------------------------------------------------------------------
# Retrieval index
# ---------------------------------------------------------------------------------------------


def _verified_query_documents(source_id: str, snapshot_id: str) -> list[SearchDocument]:
    """Human-approved question/SQL pairs, so the model can reuse what a reviewer already blessed."""
    try:
        from db.database_manager import get_project_db_connection_string, get_session
        from db.model import VerifiedQuery

        session = get_session(get_project_db_connection_string())
        try:
            rows = session.query(VerifiedQuery).filter_by(db_flag=source_id).all()
            return [
                verified_query_document(
                    source_id=source_id,
                    snapshot_id=snapshot_id,
                    question=row.question,
                    sql=row.sql,
                    pair_id=row.id,
                )
                for row in rows
            ]
        finally:
            session.close()
    except Exception as exc:
        # A missing verified-query table must not stop a query from running.
        logger.debug("Verified queries unavailable for %s: %s", source_id, exc)
        return []


def build_index(source_id: str) -> InMemoryRetrievalIndex:
    """Index one data source's schema. Embeddings are best-effort: lexical search still works."""
    source_id = validate_source_id(source_id)
    path = schema_index_path(source_id)
    if not path.is_file():
        raise UnknownDataSource(f"{source_id} has no enrolled schema at {path}")

    snapshot_id = _snapshot_id(source_id) or "unknown"
    documents = documents_from_schema_index(path, source_id=source_id, snapshot_id=snapshot_id)
    documents += _verified_query_documents(source_id, snapshot_id)

    try:
        embedder = get_embedding_provider()
        result = embedder.embed_documents([d.searchable_text() for d in documents])
        for document, vector in zip(documents, result.vectors, strict=True):
            document.embedding = vector
            document.embedding_version = result.version.key
    except Exception as exc:
        logger.warning(
            "Embeddings unavailable for %s; retrieval will use lexical search only: %s",
            source_id,
            exc,
        )

    index = InMemoryRetrievalIndex()
    index.index(documents)
    logger.info("Indexed %d document(s) for %s", len(documents), source_id)
    return index


def get_index(source_id: str) -> InMemoryRetrievalIndex:
    """Cached index, rebuilt when the enrolled schema file changes."""
    source_id = validate_source_id(source_id)
    path = schema_index_path(source_id)
    mtime = path.stat().st_mtime_ns if path.is_file() else 0
    with _index_lock:
        cached = _index_cache.get(source_id)
        if cached and cached[0] == mtime:
            return cached[1]
        index = build_index(source_id)
        _index_cache[source_id] = (mtime, index)
        return index


def invalidate_index(source_id: str | None = None) -> None:
    """Drop cached indexes - called after enrollment re-writes a schema."""
    with _index_lock:
        if source_id is None:
            _index_cache.clear()
        else:
            _index_cache.pop(source_id, None)


# ---------------------------------------------------------------------------------------------
# Checkpointing
# ---------------------------------------------------------------------------------------------


@dataclass(slots=True)
class CheckpointerHandle:
    """Owns a checkpointer and the context manager keeping its connection open."""

    saver: Any
    backend: str
    _context: Any = None

    def close(self) -> None:
        if self._context is not None:
            try:
                self._context.__exit__(None, None, None)
            except Exception:  # pragma: no cover - best effort
                logger.debug("Closing the checkpointer failed", exc_info=True)
            self._context = None


@lru_cache(maxsize=1)
def get_checkpointer() -> CheckpointerHandle:
    """Postgres when the application database is Postgres, SQLite otherwise.

    An interrupt that cannot be resumed is not human-in-the-loop, so this never silently falls back
    to an in-memory saver in a deployment: a local file is used instead, which survives a restart.
    """
    settings = get_settings()
    dsn = settings.postgres_connection_string or ""

    if dsn.startswith(("postgres", "postgresql")):
        try:
            from langgraph.checkpoint.postgres import PostgresSaver

            uri = dsn.replace("+psycopg", "", 1)
            context = PostgresSaver.from_conn_string(uri)
            saver = context.__enter__()
            saver.setup()
            return CheckpointerHandle(saver=saver, backend="postgres", _context=context)
        except Exception as exc:
            logger.warning("Postgres checkpointer unavailable (%s); using SQLite instead.", exc)

    from langgraph.checkpoint.sqlite import SqliteSaver

    path = PROJECT_ROOT / "Temp" / "graph-checkpoints.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    context = SqliteSaver.from_conn_string(str(path))
    saver = context.__enter__()
    return CheckpointerHandle(saver=saver, backend="sqlite", _context=context)


# ---------------------------------------------------------------------------------------------
# Graph dependencies
# ---------------------------------------------------------------------------------------------


def build_graph_deps(source_id: str, settings: Settings | None = None) -> GraphDeps:
    """Everything the graph needs, for one data source."""
    settings = settings or get_settings()
    policy = settings.mode_policy
    return GraphDeps(
        router=get_router(),
        index=get_index(source_id),
        embedder=get_embedding_provider(),
        resolve_source=resolve_source,
        egress_policy=settings.effective_egress_policy,
        network_level=policy.network_policy,
        network_allowlist=settings.network_allowlist_list,
        bundled_hosts=settings.network_allowlist_list,
        run_metadata={"mode": settings.mode.value},
        on_examples_used=_record_example_usage,
    )


def _record_example_usage(pair_ids: list[str]) -> None:
    """Credit the verified pairs the graph reported showing to the model.

    This lives here rather than in the graph node because it writes to the application database, and
    the graph is deliberately kept unable to reach one. A pair nobody's questions ever reach is a
    candidate for retirement; one used constantly is worth re-checking first when the schema moves.
    """
    from db.verified_queries import record_use

    for pair_id in pair_ids:
        try:
            record_use(int(pair_id))
        except (ValueError, TypeError):
            continue
        except Exception as exc:  # pragma: no cover - a counter must not break a query
            logger.debug("Could not record usage for verified pair %s: %s", pair_id, exc)


def reset() -> None:
    """Drop every cache (tests, and after a configuration change)."""
    invalidate_index()
    handle = get_checkpointer.cache_info().currsize and get_checkpointer()
    if handle:
        handle.close()
    get_checkpointer.cache_clear()


__all__ = [
    "CheckpointerHandle",
    "UnknownDataSource",
    "build_graph_deps",
    "build_index",
    "get_checkpointer",
    "get_index",
    "invalidate_index",
    "reset",
    "resolve_source",
    "schema_index_path",
]
