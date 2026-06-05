"""Conversation memory helpers backed by LangGraph Postgres store."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from langgraph.store.base import SearchItem

from app.utils.logger import sanitize_for_log, setup_logging
from db.langchain_memory import get_store

logger = setup_logging(__name__)

_store: object | None = None


def _get_store():
    global _store
    if _store is None:
        _store = get_store()
    return _store


QUERY_NAMESPACE = "queries"
SUMMARY_NAMESPACE = "conversation_summary"
SUMMARY_KEY = "meta"


def _query_namespace(user_id: str, session_id: str, db_flag: str) -> tuple[str, ...]:
    return (QUERY_NAMESPACE, user_id, session_id, db_flag)


def _summary_namespace(user_id: str, session_id: str, db_flag: str) -> tuple[str, ...]:
    return (SUMMARY_NAMESPACE, user_id, session_id, db_flag)


def _iterate_namespace(namespace: tuple[str, ...], limit: int = 100) -> Iterable[SearchItem]:
    offset = 0
    while True:
        store = _get_store()
        page = store.search(namespace, limit=limit, offset=offset, query="")
        if not page:
            break
        yield from page
        if len(page) < limit:
            break
        offset += len(page)


def store_query_context(
    user_id: str,
    session_id: str,
    db_flag: str,
    query_text: str,
    sql_generated: str,
    tables_used: list[str] | None = None,
    follow_up_questions: list[str] | None = None,
    contextual_insights: str | None = None,
    execution_time: float | None = None,
) -> str:
    """Persist a query turn in conversation memory."""
    namespace = _query_namespace(user_id, session_id, db_flag)
    timestamp = datetime.now(UTC).isoformat()
    key = f"{timestamp}-{uuid4().hex}"
    entry: dict[str, Any] = {
        "query_text": query_text,
        "sql_generated": sql_generated,
        "tables_used": tables_used or [],
        "follow_up_questions": follow_up_questions or [],
        "contextual_insights": contextual_insights,
        "execution_time": execution_time,
        "timestamp": timestamp,
        "db_flag": db_flag,
    }
    logger.debug(
        "Storing query context namespace=%s key=%s tables=%s followups=%s",
        "/".join(namespace),
        key,
        sanitize_for_log(entry["tables_used"]),
        sanitize_for_log(entry["follow_up_questions"]),
    )
    store = _get_store()
    store.put(namespace, key, entry)
    logger.info("Stored query context for %s/%s (key=%s)", user_id, session_id, key)
    return key


def get_query_history(
    user_id: str,
    session_id: str,
    db_flag: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Retrieve the most recent query turns for a session."""
    namespace = _query_namespace(user_id, session_id, db_flag)
    logger.debug(
        "Fetching query history namespace=%s limit=%s",
        "/".join(namespace),
        limit,
    )
    try:
        store = _get_store()
        items = store.search(namespace, limit=limit, query="")
    except Exception as exc:
        from app.utils.logger import sanitize_for_log

        logger.error(
            "Failed to retrieve query history: %s", sanitize_for_log(str(exc), max_len=500)
        )
        return []
    result: list[dict[str, Any]] = []
    for item in items:
        value = item.value
        result.append(
            {
                "key": item.key,
                "query_text": value.get("query_text"),
                "sql_generated": value.get("sql_generated"),
                "tables_used": value.get("tables_used") or [],
                "follow_up_questions": value.get("follow_up_questions") or [],
                "contextual_insights": value.get("contextual_insights"),
                "execution_time": value.get("execution_time"),
                "timestamp": value.get("timestamp"),
            }
        )
    logger.debug("Retrieved %d history entries for %s/%s", len(result), user_id, session_id)
    return result


def format_conversation_summary(query_history: list[dict[str, Any]]) -> str:
    """Create a readable summary of the latest history."""
    if not query_history:
        return "No conversation history yet."
    lines: list[str] = []
    from app.utils.logger import sanitize_for_log

    for idx, record in enumerate(reversed(query_history), 1):
        q = sanitize_for_log(record.get("query_text"))
        s = sanitize_for_log(record.get("sql_generated"))
        line = f"{idx}. Query: {q} | SQL: {s}"
        insights = record.get("contextual_insights")
        if insights:
            line += f" | Facts: {insights}"
        follow_ups = record.get("follow_up_questions") or []
        if follow_ups:
            line += f" | Follow-ups: {', '.join(follow_ups)}"
        lines.append(line)
    return "\n".join(lines)


def get_session_accessed_tables(
    user_id: str,
    session_id: str,
    db_flag: str,
    limit: int = 5,
) -> set[str]:
    """Return the tables referenced in the most recent turns."""
    history = get_query_history(user_id, session_id, db_flag, limit=limit)
    tables: set[str] = set()
    for record in history:
        for table in record.get("tables_used", []):
            tables.add(table)
    return tables


def update_or_create_session_summary(
    user_id: str,
    session_id: str,
    db_flag: str,
) -> None:
    """Store summary metadata in the LangGraph store."""
    history = get_query_history(user_id, session_id, db_flag, limit=10)
    summary_text = format_conversation_summary(history)
    entry: dict[str, Any] = {
        "summary": summary_text,
        "accessed_tables": list(
            get_session_accessed_tables(user_id, session_id, db_flag, limit=10)
        ),
        "total_queries": len(history),
        "updated_at": datetime.now(UTC).isoformat(),
    }
    namespace = _summary_namespace(user_id, session_id, db_flag)
    store = _get_store()
    store.put(namespace, SUMMARY_KEY, entry)
    logger.debug("Updated conversation summary for %s/%s", user_id, session_id)


def get_session_summary(
    user_id: str,
    session_id: str,
    db_flag: str,
) -> dict[str, Any] | None:
    """Retrieve the persisted summary metadata."""
    namespace = _summary_namespace(user_id, session_id, db_flag)
    try:
        store = _get_store()
        item = store.get(namespace, SUMMARY_KEY)
    except Exception as exc:
        from app.utils.logger import sanitize_for_log

        logger.error("Failed to read session summary: %s", sanitize_for_log(str(exc), max_len=500))
        item = None
    if item:
        return item.value
    history = get_query_history(user_id, session_id, db_flag, limit=5)
    if not history:
        return None
    return {
        "summary": format_conversation_summary(history),
        "accessed_tables": list(get_session_accessed_tables(user_id, session_id, db_flag, limit=5)),
        "total_queries": len(history),
        "updated_at": datetime.now(UTC).isoformat(),
    }


def clear_conversation_history(user_id: str, session_id: str, db_flag: str) -> None:
    """Remove all conversation and summary records for a session."""
    query_namespace = _query_namespace(user_id, session_id, db_flag)
    summary_namespace = _summary_namespace(user_id, session_id, db_flag)
    for item in _iterate_namespace(query_namespace):
        store = _get_store()
        store.put(query_namespace, item.key, None)
    store = _get_store()
    store.put(summary_namespace, SUMMARY_KEY, None)
    logger.info("Cleared conversation history for %s/%s", user_id, session_id)


__all__ = [
    "clear_conversation_history",
    "format_conversation_summary",
    "get_query_history",
    "get_session_accessed_tables",
    "get_session_summary",
    "store_query_context",
    "update_or_create_session_summary",
]
