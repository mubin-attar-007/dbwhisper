"""SQL execution utilities."""

from __future__ import annotations

import re

import pandas as pd
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.utils.logger import sanitize_for_log, setup_logging
from db import database_manager

logger = setup_logging(__name__)
from app.core import sql_validator


def _has_order_by(sql_text: str) -> bool:
    # Very small heuristic: look for top-level "ORDER BY" (case-insensitive)
    return re.search(r"\border\s+by\b", sql_text, flags=re.IGNORECASE) is not None


def _is_simple_aggregation(sql_text: str) -> bool:
    """Return True if SQL is a simple aggregation (COUNT/SUM/AVG/MIN/MAX) without GROUP BY.

    This heuristic avoids applying pagination to queries that return a single aggregated value.
    """
    s = sql_text.lower()
    # Quick checks: presence of GROUP BY usually indicates multiple rows
    if "group by" in s:
        return False
    # If it contains one of the aggregate keywords near the start, treat as aggregation
    return bool(re.search(r"\b(count|sum|avg|min|max)\s*\(", s))


def _has_unbound_parameters(sql_text: str) -> bool:
    """Detect if SQL contains parameter placeholders that are not bound by our API.

    We consider the following patterns as unbound placeholders for now:
    - MSSQL style: @param
    - Named style: :param (if no params are provided)
    - Python DBAPI style: %s and ? (positional)
    """
    s = sql_text
    # MSSQL-style @param
    if re.search(r"\@[A-Za-z_][A-Za-z0-9_]*", s):
        return True
    # Named or positional: :name (could be valid if params passed) - we don't support params via our API
    if re.search(r"\:[A-Za-z_][A-Za-z0-9_]*", s):
        return True
    # DBAPI-style placeholders
    return bool("%s" in s or "?" in s)


def _extract_first_select_column(sql_text: str) -> str | None:
    # Try to grab the first column token after SELECT and before FROM
    m = re.search(
        r"select\s+(distinct\s+)?(?P<cols>.*?)\s+from\b", sql_text, flags=re.IGNORECASE | re.DOTALL
    )
    if not m:
        return None
    cols = m.group("cols")
    # split on commas, ignore parentheses that may occur; use a simple split for now
    first_col = cols.split(",")[0].strip()
    # Remove common alias using ' as ' or space alias
    as_match = re.split(r"\s+as\s+|\s+", first_col, flags=re.IGNORECASE)
    if as_match:
        return as_match[0]
    return first_col


def _wrap_sql_with_pagination(
    sanitized_sql: str, db_type: str, page: int, page_size: int
) -> tuple[str, int]:
    offset = (int(page) - 1) * int(page_size)
    if db_type in ("postgres", "postgresql", "mysql"):
        wrapped_sql = f"SELECT * FROM ({sanitized_sql}) AS _sub LIMIT {page_size} OFFSET {offset}"
        return wrapped_sql, offset
    if db_type in ("mssql", "sqlserver"):
        # SQL Server requires ORDER BY for OFFSET..FETCH pagination. Do not auto-infer an order,
        # instead require the caller to provide an explicit ORDER BY when asking for pages.
        # Optionally allow an automatic fallback order-by insertion for older queries
        if not _has_order_by(sanitized_sql):
            from os import getenv

            fallback_enabled = str(getenv("ALLOW_MSSQL_AUTO_ORDER_BY", "false")).lower() in (
                "1",
                "true",
                "yes",
            )
            if not fallback_enabled:
                raise ValueError(
                    "SQL Server pagination requires an explicit ORDER BY clause when requesting a page. Please add ORDER BY to your query."
                )
            # Auto-insert a fallback ORDER BY that is deterministic enough for pagination
            # Use ORDER BY (SELECT NULL) as a non-invasive fallback (MSSQL supports it)
            logger.debug("Applying MSSQL ORDER BY fallback for pagination to avoid crash.")
            sanitized_sql = sanitized_sql + " ORDER BY (SELECT NULL)"
        # Extract existing ORDER BY expression and use OFFSET..FETCH for SQL Server.
        order_match = re.search(r"(?i)\border\s+by\b(?P<order>.*)$", sanitized_sql)
        if order_match:
            order_expr = order_match.group("order").strip()
            base_sql = sanitized_sql[: order_match.start()].strip()
        else:
            # This shouldn't happen because of the earlier check, but guard anyway.
            order_expr = _extract_first_select_column(sanitized_sql) or "(SELECT NULL)"
            base_sql = sanitized_sql

        wrapped_sql = f"{base_sql} ORDER BY {order_expr} OFFSET {offset} ROWS FETCH NEXT {page_size} ROWS ONLY"
        return wrapped_sql, offset
    # Default fallback
    wrapped_sql = f"SELECT * FROM ({sanitized_sql}) AS _sub LIMIT {page_size} OFFSET {offset}"
    return wrapped_sql, offset


def execute_query(
    sql: str,
    db_config: dict[str, object],
    db_flag: str | None = None,
    page: int | None = None,
    page_size: int | None = None,
    include_total: bool = False,
) -> dict[str, object]:
    """Execute SQL and return tabular results."""

    connection_string = str(db_config["connection_string"])
    query_timeout = int(db_config.get("query_timeout", 30))
    # Max rows configured by DB row acts as a safety cap for page_size
    max_rows = int(db_config.get("max_rows", 1000))
    # Respect explicit page arguments: require both page and page_size to be present to enable pagination
    provided_page = page is not None
    provided_page_size = page_size is not None
    pagination_requested = provided_page and provided_page_size
    if page_size is not None:
        if page_size <= 0:
            return {"success": False, "error": "page_size must be >= 1", "dataframe": None}
        # Clamp page_size to max_rows to avoid retrieving huge sets
        if page_size > max_rows:
            page_size = max_rows
            logger.debug("Clamping requested page_size to max_rows: %s", max_rows)
    else:
        # Do not set a default page_size unless pagination is explicitly requested with both page and page_size.
        page_size = max_rows if pagination_requested else None
    if page is None and pagination_requested:
        page = 1
    # If only one of page or page_size is present, log and ignore pagination
    if (provided_page or provided_page_size) and not pagination_requested:
        logger.debug(
            "Pagination ignored because both page and page_size are required for pagination; page=%s page_size=%s",
            sanitize_for_log(page),
            sanitize_for_log(page_size),
        )

    # Final validation before execution (defense in depth)
    validation = sql_validator.validate_sql(sql, db_flag=db_flag)
    if not validation.get("valid"):
        return {
            "success": False,
            "error": validation.get("reason"),
            "dataframe": None,
        }

    try:
        conn = database_manager.get_connection(connection_string)

        # Build a dialect-aware wrapper applying LIMIT/OFFSET
        # Resolve db_type from db_config first; fallback to SQLAlchemy engine dialect
        db_type = db_config.get("db_type")
        if not db_type:
            try:
                engine = database_manager.get_engine(connection_string)
                db_type = str(engine.dialect.name)
            except Exception:
                db_type = "postgres"
        db_type = str(db_type).lower()
        # Normalize common dialect naming variations such as `sql_server` -> `sqlserver` and remove hyphens
        db_type = db_type.replace("_", "").replace("-", "")
        logger.debug(
            "Using db_type=%s for connection=%s",
            sanitize_for_log(db_type),
            sanitize_for_log(connection_string),
        )
        sanitized_sql = sql.strip().rstrip(";")
        # If this is a simple aggregation like COUNT() without GROUP BY, skip pagination
        if pagination_requested and _is_simple_aggregation(sanitized_sql):
            pagination_requested = False
            logger.debug(
                "Detected simple aggregation; pagination disabled for sql=%s",
                sanitize_for_log(sanitized_sql, max_len=200),
            )
        # Do not try to execute SQL with parameter placeholders; our API doesn't bind params.
        if _has_unbound_parameters(sanitized_sql):
            return {
                "success": False,
                "error": "Query contains parameter placeholders (e.g. @param, :param, %s, ?) but no parameter values were provided. Please provide a literal value or use a parameter-free query.",
                "dataframe": None,
            }

        if pagination_requested:
            # If the caller requested pagination, apply the wrapper
            try:
                wrapped_sql, offset = _wrap_sql_with_pagination(
                    sanitized_sql, db_type, page, page_size
                )
            except ValueError as e:
                # Friendly error back to API client instead of raising
                return {"success": False, "error": str(e), "dataframe": None}
            logger.debug(
                "Pagination wrapper applied: %s", sanitize_for_log(wrapped_sql, max_len=300)
            )
            result = conn.execution_options(timeout=query_timeout).execute(text(wrapped_sql))
        else:
            # No pagination requested: execute raw SQL and rely on `max_rows` safety cap for fetchmany
            result = conn.execution_options(timeout=query_timeout).execute(text(sql))

        columns: list[str] = result.keys()
        rows = result.fetchmany(int(page_size or max_rows))
        conn.close()
    except SQLAlchemyError as exc:
        return {
            "success": False,
            "error": str(exc),
            "dataframe": None,
        }

    df = pd.DataFrame(rows, columns=columns)

    result_payload = {
        "success": True,
        "error": None,
        "dataframe": df,
    }

    # Optionally include total_rows and has_next using a count() query (expensive)
    if include_total:
        try:
            conn = database_manager.get_connection(connection_string)
            count_sql = f"SELECT COUNT(*) FROM ({sanitized_sql}) AS _count_sub"
            count_result = (
                conn.execution_options(timeout=query_timeout).execute(text(count_sql)).fetchone()
            )
            total_rows = int(count_result[0]) if count_result and len(count_result) > 0 else None
            conn.close()
            result_payload.update(
                {"total_rows": total_rows, "page": int(page), "page_size": int(page_size)}
            )
            if total_rows is not None:
                result_payload["has_next"] = (offset + int(page_size)) < total_rows
        except SQLAlchemyError:
            # Do not fail the primary query for count failures - set fields conservatively
            result_payload.update(
                {
                    "total_rows": None,
                    "page": int(page),
                    "page_size": int(page_size),
                    "has_next": None,
                }
            )

    return result_payload
    return {
        "success": True,
        "error": None,
        "dataframe": df,
    }
