"""LangChain tools for schema retrieval and SQL validation."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from langchain.tools import tool

from app.core import sql_validator
from app.core.retriever import default_collection_name, vector_search
from app.utils.logger import sanitize_for_log, setup_logging
from app.utils.tool_cache import default_cache

logger = setup_logging(__name__)

_current_db_flag: ContextVar[str | None] = ContextVar("agent_db_flag", default=None)
_current_collection: ContextVar[str | None] = ContextVar("agent_collection", default=None)
_current_user_id: ContextVar[str | None] = ContextVar("agent_user_id", default=None)
_current_session_id: ContextVar[str | None] = ContextVar("agent_session", default=None)
_accessed_tables: ContextVar[set[str] | None] = ContextVar("agent_tables", default=None)
_tool_cache: ContextVar[dict | None] = ContextVar("agent_tool_cache", default=None)
_tool_call_counts: ContextVar[dict | None] = ContextVar("agent_tool_call_counts", default=None)

VALID_SECTIONS = {"summary", "header", "columns", "relationships", "stats"}


def _record_table(table_name: str | None) -> None:
    if not table_name:
        return
    table = table_name.strip()
    if not table:
        return
    visited = _accessed_tables.get(None)
    if visited is None:
        visited = set()
        _accessed_tables.set(visited)
    visited.add(table)


def _filters_with_context(base: dict[str, object] | None = None) -> dict[str, object]:
    filters: dict[str, object] = dict(base or {})
    db_flag = _current_db_flag.get(None)
    if db_flag:
        filters.setdefault("db_flag", db_flag)
    return filters


def _require_collection() -> str:
    collection = _current_collection.get(None)
    if not collection:
        raise RuntimeError("Vector collection context is not configured for the agent run")
    return collection


def _log_tool(
    name: str, params: dict[str, object], result: str | None, extra: dict[str, object] | None = None
) -> None:
    # Sanitize tool parameters and results before logging

    safe_params = {k: sanitize_for_log(v, max_len=500) for k, v in (params or {}).items()}
    sanitize_for_log(result, max_len=500)
    log_data = {
        "params": safe_params,
        "result_length": len(result) if result else 0,
    }
    if extra:
        safe_extra = {k: sanitize_for_log(v, max_len=500) for k, v in extra.items()}
        log_data.update(safe_extra)
    logger.debug("Tool call: %s %s", name, log_data)


@contextmanager
def agent_context(
    db_flag: str,
    collection_name: str | None = None,
    user_id: str | None = None,
    session_id: str | None = None,
) -> Iterator[None]:
    """Context manager that binds db_flag and collection for tool invocations."""

    token_db = _current_db_flag.set(db_flag)
    collection = collection_name or default_collection_name(db_flag)
    token_collection = _current_collection.set(collection)
    token_tables = _accessed_tables.set(set())
    # Per-run cache to avoid repeated identical tool calls and limit abuse
    token_cache = _tool_cache.set({})
    token_counts = _tool_call_counts.set({})
    token_user = _current_user_id.set(user_id)
    token_session = _current_session_id.set(session_id)
    logger.debug("Agent context set db_flag=%s collection=%s", db_flag, collection)
    try:
        yield
    finally:
        logger.debug("Clearing agent context db_flag=%s collection=%s", db_flag, collection)
        _accessed_tables.reset(token_tables)
        _tool_cache.reset(token_cache)
        _tool_call_counts.reset(token_counts)
        _current_collection.reset(token_collection)
        _current_db_flag.reset(token_db)
        _current_user_id.reset(token_user)
        _current_session_id.reset(token_session)


def get_collected_tables() -> list[str]:
    """Return the sorted list of table names accessed during the agent run."""

    visited = _accessed_tables.get(None)
    if not visited:
        return []
    return sorted(visited)


def get_context_user_id() -> str | None:
    return _current_user_id.get(None)


def get_context_session_id() -> str | None:
    return _current_session_id.get(None)


def get_context_db_flag() -> str | None:
    return _current_db_flag.get(None)


_MAX_TOOL_CALLS_PER_TOOL = 8
_GLOBAL_TOOL_CACHE_TTL = int(os.getenv("TOOL_CACHE_TTL_SECONDS", "60"))


def _tool_cache_key(name: str, *args, **kwargs) -> str:
    # Simple reproducible cache key for a tool call
    return f"{name}:{args}:{sorted(kwargs.items())}"


def _tool_maybe_cache_or_count(name: str, key: str, value: str) -> str:
    """Return cached value if exists; otherwise, increment counts, cache and return value.
    If a tool is called too many times, return a short signal message that the LLM
    can use to stop repeating calls.
    """
    cache = _tool_cache.get(None)
    counts = _tool_call_counts.get(None)
    if cache is None or counts is None:
        # If no per-run context, don't attempt to cache
        return value
    # If cached value exists, return it without counting again
    if key in cache:
        return cache[key]
    # Count the calls per tool name
    counts[name] = counts.get(name, 0) + 1
    count = counts[name]
    # Cache the value
    cache[key] = value
    # If the tool has been called too many times, return a short abort hint
    if count > _MAX_TOOL_CALLS_PER_TOOL:
        msg = (
            f"Tool '{name}' called {count} times in this agent run. "
            "Please avoid repeated identical calls and proceed to final output."
        )
        # Also cache the abort hint so further calls quickly return the hint
        cache[key] = msg
        return msg
    return value


@tool("search_tables", return_direct=False)
def search_tables_tool(query: str, k: int = 4) -> str:
    """Identify candidate tables for the user query via summary embeddings."""

    if not query.strip():
        return "No query provided."
    collection = _require_collection()
    filters = _filters_with_context({"section": "summary"})
    cache_key = _tool_cache_key("search_tables", query, k, frozenset(filters.items()))
    # Attempt cross-run cache first to reduce repeated vector searches
    key_payload = {"q": query, "collection": collection, "filters": dict(filters or {}), "k": k}
    key_str = json.dumps(key_payload, sort_keys=True, default=str)
    key_hash = hashlib.sha256(key_str.encode("utf-8")).hexdigest()
    docs = default_cache.get(key_hash)
    if docs is None:
        docs = vector_search(query, collection, filters=filters, k=k)
    if not docs:
        return "No matching tables found in summaries."
    formatted = []
    for doc in docs:
        table_name = doc.metadata.get("table_name") or doc.metadata.get("table")
        db_schema = doc.metadata.get("schema", "dbo")
        _record_table(table_name)
        formatted.append(
            f"Table: {table_name} (schema={db_schema})\nSummary: {doc.page_content.strip()}"
        )
    out = "\n\n".join(formatted)
    _table_names = [doc.metadata.get("table_name") or doc.metadata.get("table") for doc in docs]
    _log_tool(
        "search_tables",
        {"query": query, "k": k, "filters": filters},
        out,
        {"hits": len(docs), "tables": _table_names},
    )
    return _tool_maybe_cache_or_count("search_tables", cache_key, out)


@tool("fetch_table_summary", return_direct=False)
def fetch_table_summary_tool(table_name: str, db_schema: str | None = None) -> str:
    """Retrieve the minimal summary chunk for a given table."""

    if not table_name.strip():
        return "Table name is required."
    collection = _require_collection()
    filters = _filters_with_context({"section": "summary", "table_name": table_name})
    if db_schema:
        filters.setdefault("schema", db_schema)
    cache_key = _tool_cache_key(
        "fetch_table_summary", table_name, db_schema, frozenset(filters.items())
    )
    key_payload = {
        "q": f"summary for {table_name}",
        "collection": collection,
        "filters": dict(filters or {}),
        "k": 1,
    }
    key_str = json.dumps(key_payload, sort_keys=True, default=str)
    key_hash = hashlib.sha256(key_str.encode("utf-8")).hexdigest()
    docs = default_cache.get(key_hash)
    if docs is None:
        docs = vector_search(f"summary for {table_name}", collection, filters=filters, k=1)
    if not docs:
        return f"No summary found for table {table_name}."
    _record_table(table_name)
    out = docs[0].page_content.strip()
    _log_tool(
        "fetch_table_summary",
        {"table_name": table_name, "db_schema": db_schema, "filters": filters},
        out,
        {"hits": len(docs)},
    )
    return _tool_maybe_cache_or_count("fetch_table_summary", cache_key, out)


@tool("fetch_table_section", return_direct=False)
def fetch_table_section_tool(table_name: str, section: str, db_schema: str | None = None) -> str:
    """Retrieve a structured section (columns, relationships, stats) for a table."""

    if section not in VALID_SECTIONS:
        return f"Unsupported section '{section}'. Use one of: {', '.join(sorted(VALID_SECTIONS))}."
    if not table_name.strip():
        return "Table name is required."
    collection = _require_collection()
    filters = _filters_with_context({"section": section, "table_name": table_name})
    if db_schema:
        filters.setdefault("schema", db_schema)
    cache_key = _tool_cache_key(
        "fetch_table_section", table_name, section, db_schema, frozenset(filters.items())
    )
    key_payload = {
        "q": f"{section} for {table_name}",
        "collection": collection,
        "filters": dict(filters or {}),
        "k": 1,
    }
    key_str = json.dumps(key_payload, sort_keys=True, default=str)
    key_hash = hashlib.sha256(key_str.encode("utf-8")).hexdigest()
    docs = default_cache.get(key_hash)
    if docs is None:
        docs = vector_search(f"{section} for {table_name}", collection, filters=filters, k=1)
    if not docs:
        return f"No {section} section found for table {table_name}."
    _record_table(table_name)
    out = docs[0].page_content.strip()
    _log_tool(
        "fetch_table_section",
        {"table_name": table_name, "section": section, "filters": filters},
        out,
        {"hits": len(docs)},
    )
    return _tool_maybe_cache_or_count("fetch_table_section", cache_key, out)


@tool("validate_sql", return_direct=False)
def validate_sql_tool(sql: str) -> str:
    """Validate that SQL is read-only and safe."""

    # We still validate each SQL call (not cached) because it depends on the SQL text
    db_flag = _current_db_flag.get(None)
    result = sql_validator.validate_sql(sql or "", db_flag=db_flag)
    _log_tool(
        "validate_sql",
        {"sql": sql},
        "OK" if result.get("valid") else f"Invalid: {result.get('reason')}",
        {"valid": result.get("valid"), "reason": result.get("reason")},
    )
    return "OK" if result.get("valid") else f"Invalid: {result.get('reason')}"


__all__ = [
    "agent_context",
    "default_collection_name",
    "fetch_table_section_tool",
    "fetch_table_summary_tool",
    "get_collected_tables",
    "get_context_db_flag",
    "get_context_session_id",
    "get_context_user_id",
    "search_tables_tool",
    "validate_sql_tool",
]
