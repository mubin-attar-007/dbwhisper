"""Shared LangGraph/Store resources for conversation memory."""

from __future__ import annotations

import atexit
import os

from dotenv import load_dotenv
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.store.postgres import PostgresStore

from app.utils.logger import setup_logging

load_dotenv()

logger = setup_logging(__name__)


def _get_postgres_uri():
    raw_uri = os.getenv("POSTGRES_CONNECTION_STRING")
    if raw_uri and "+psycopg" in raw_uri:
        return raw_uri.replace("+psycopg", "", 1)
    return raw_uri


def _cleanup_contexts(store_ctx, checkpointer_ctx):
    try:
        checkpointer_ctx.__exit__(None, None, None)
    except Exception:  # pragma: no cover - best-effort cleanup
        logger.debug("Error closing checkpointer context")
    try:
        store_ctx.__exit__(None, None, None)
    except Exception:  # pragma: no cover - best-effort cleanup
        logger.debug("Error closing store context")


from functools import lru_cache


@lru_cache(maxsize=1)
def _init_store_and_checkpointer():
    uri = _get_postgres_uri()
    if not uri:
        raise RuntimeError("POSTGRES_CONNECTION_STRING must be configured to use LangGraph memory")
    store_ctx = PostgresStore.from_conn_string(uri)
    checkpointer_ctx = PostgresSaver.from_conn_string(uri)
    store = store_ctx.__enter__()
    checkpointer = checkpointer_ctx.__enter__()
    try:
        store.setup()
        logger.info("PostgresStore initialized for conversation memory")
    except Exception as exc:
        from app.utils.logger import sanitize_for_log

        logger.error(
            "Failed to create LangGraph store tables: %s", sanitize_for_log(str(exc), max_len=500)
        )
    try:
        checkpointer.setup()
        logger.info("Postgres checkpointer initialized")
    except Exception as exc:
        from app.utils.logger import sanitize_for_log

        logger.error(
            "Failed to create LangGraph checkpointer tables: %s",
            sanitize_for_log(str(exc), max_len=500),
        )
    atexit.register(lambda: _cleanup_contexts(store_ctx, checkpointer_ctx))
    return store, checkpointer


def get_store() -> PostgresStore:
    return _init_store_and_checkpointer()[0]


def get_checkpointer() -> PostgresSaver:
    return _init_store_and_checkpointer()[1]


__all__ = ["get_checkpointer", "get_store"]
