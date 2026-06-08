"""SQL validation helpers to enforce read-only access."""

from __future__ import annotations

import re

import sqlparse
from sqlparse.tokens import DML, Keyword

READ_ONLY_PATTERN = re.compile(r"^\s*(with|select)\b", re.IGNORECASE)
FORBIDDEN_WORDS = {
    "insert",
    "update",
    "delete",
    "drop",
    "alter",
    "truncate",
    "create",
    "grant",
    "revoke",
    "merge",
    "call",
    "replace",
    "exec",
    "execute",
    "openrowset",
    "bulk",
}
SELECT_INTO_PATTERN = re.compile(r"\bselect\b[\s\S]*\binto\b", re.IGNORECASE)
MAX_SQL_LENGTH_CHARS = 5000


def _is_multiple_statements(trimmed: str) -> bool:
    parts = sqlparse.split(trimmed)
    return len([p for p in parts if p.strip()]) > 1


def _first_non_whitespace_token(stmt) -> tuple[str, object]:
    """Return the first non-whitespace/comment token's normalized value and token."""
    for token in stmt.tokens:
        if token.is_whitespace or token.ttype in (sqlparse.tokens.Comment,):
            continue
        # token could be an Identifier, Keyword, or DML
        return (getattr(token, "normalized", str(token)).lower(), token)
    return ("", None)


def _contains_forbidden_keyword(stmt) -> tuple[bool, str | None]:
    """Scan tokens and detect forbidden keywords while skipping comments and strings."""
    for token in stmt.flatten():
        # Skip comments and string literals
        if token.is_whitespace or token.ttype in (sqlparse.tokens.Comment, sqlparse.tokens.String):
            continue
        val = (token.value or "").lower()
        # Detect exact keyword matches
        if token.ttype in (Keyword, DML) and val in FORBIDDEN_WORDS:
            return True, val
        # Also check raw word boundaries to capture keywords treated as names
        for bad in FORBIDDEN_WORDS:
            if f" {bad} " in f" {val} ":
                return True, bad
    return False, None


def _extract_referenced_tables(trimmed: str):
    """Extract table names referenced in FROM and JOIN clauses.

    This is a heuristic fallback parser that looks for table names after FROM/JOIN.
    It returns the last component of a possibly schema-qualified table name.
    """
    candidates = []
    for match in re.findall(r'\b(?:from|join)\s+([\w\[\]".]+)', trimmed, flags=re.IGNORECASE):
        # strip brackets/quotes
        name = match.strip().strip('[]"')
        # take last segment for schema.table
        parts = name.split(".")
        if parts:
            candidates.append(parts[-1].lower())
    return set(candidates)


_CTE_NAME_PATTERN = re.compile(r"(?:\bwith\b|,)\s+([a-zA-Z_]\w*)\s+as\s*\(", re.IGNORECASE)


def _extract_cte_names(trimmed: str) -> set[str]:
    """Names introduced by WITH ... AS (...) CTEs, which are valid FROM/JOIN targets."""
    return {m.group(1).lower() for m in _CTE_NAME_PATTERN.finditer(trimmed)}


def _load_schema_index_tables(db_flag: str):
    try:
        from pathlib import Path

        import yaml

        from app.user_db_config_loader import PROJECT_ROOT

        index_path = (
            Path(PROJECT_ROOT) / "database_schemas" / db_flag / "schema" / "schema_index.yaml"
        )
        if not index_path.exists():
            return None
        with index_path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        entries = data.get("tables", [])
        return {str(item.get("table", "")).lower() for item in entries if item.get("table")}
    except Exception:
        return None


def validate_sql(sql: str, db_flag: str | None = None) -> dict[str, object]:
    """Validate SQL is read-only and safe.

    This function uses sqlparse to parse and tokenise SQL to detect multiple statements,
    non-SELECT statements, and the presence of forbidden keywords. It tries to avoid
    false positives by skipping comments and string literals.
    """
    trimmed = sql.strip()
    if trimmed.endswith(";"):
        trimmed = trimmed[:-1].strip()
    if not trimmed:
        return {"valid": False, "reason": "Empty SQL statement"}

    if _is_multiple_statements(trimmed):
        return {"valid": False, "reason": "Multiple statements are not permitted"}

    if len(trimmed) > MAX_SQL_LENGTH_CHARS:
        return {"valid": False, "reason": "SQL statement too long"}

    # Basic textual check for starting statement
    if not READ_ONLY_PATTERN.match(trimmed):
        return {"valid": False, "reason": "SQL must start with SELECT or WITH (CTE)"}

    try:
        parsed = sqlparse.parse(trimmed)
        if not parsed:
            return {"valid": False, "reason": "Unable to parse SQL statement"}
        stmt = parsed[0]
    except Exception:
        return {"valid": False, "reason": "SQL parse error"}

    first_val, _first_token = _first_non_whitespace_token(stmt)
    if not (first_val.startswith("select") or first_val.startswith("with")):
        return {"valid": False, "reason": "SQL must start with SELECT or WITH (CTE)"}

    # Reject SELECT INTO
    if SELECT_INTO_PATTERN.search(trimmed):
        return {"valid": False, "reason": "SELECT ... INTO is not permitted"}

    # Detect forbidden keywords in parsed tokens (skip strings & comments)
    found, word = _contains_forbidden_keyword(stmt)
    if found:
        return {"valid": False, "reason": f"Detected forbidden keyword: {word}"}

    # Basic protection against system schema access
    lower_trim = trimmed.lower()
    if "information_schema" in lower_trim or "sys." in lower_trim or "pg_catalog." in lower_trim:
        return {"valid": False, "reason": "Access to system schemas is not permitted"}

    # Validate referenced tables are in the enrolled schema index (fail-open if the index
    # file is absent). CTE names defined in the same query are allowed FROM/JOIN targets.
    if db_flag:
        allowed = _load_schema_index_tables(db_flag)
        if allowed is not None:
            refs = _extract_referenced_tables(trimmed)
            cte_names = _extract_cte_names(trimmed)
            missing = refs - allowed - cte_names
            if missing:
                return {
                    "valid": False,
                    "reason": f"Unknown or unauthorized tables referenced: {', '.join(sorted(missing))}",
                }

    return {"valid": True, "reason": "SQL passed read-only validation"}
