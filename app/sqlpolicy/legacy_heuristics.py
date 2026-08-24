"""The v1 ``sqlparse``/regex validator, retained as an *independent second opinion*.

It runs after the AST engine; when it denies something the engine allowed, the stricter answer wins
and the disagreement is recorded (that is how we notice gaps in either layer). Several false
positives of the original were fixed so this layer cannot poison the new engine:

* substring matching of forbidden words inside identifiers/literals (``grant_total``, ``'update'``);
* ``REPLACE(...)`` the string function being treated like MySQL ``REPLACE INTO``;
* a leading comment (``/* hint */ SELECT ...``) being read as a non-SELECT statement;
* ``EXTRACT(YEAR FROM col)`` / ``SUBSTRING(x FROM 1)`` being read as a table reference;
* quoted and bracketed identifiers (``"demo"."customers"``, ``[dbo].[customers]``, `` `customers` ``).
"""

from __future__ import annotations

import re
from collections.abc import Iterable

import sqlparse
from sqlparse.tokens import DDL, DML, Keyword

READ_ONLY_PATTERN = re.compile(r"^\s*(with|select|\()", re.IGNORECASE)
FORBIDDEN_STATEMENT_KEYWORDS = {
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
    "exec",
    "execute",
    "openrowset",
    "bulk",
}
SELECT_INTO_PATTERN = re.compile(r"\bselect\b[\s\S]*?\binto\b", re.IGNORECASE)
INTO_TARGET_PATTERN = re.compile(r"\binto\s+(?:outfile|dumpfile|@|#?[\w\[\]\"`.]+)", re.IGNORECASE)
REPLACE_INTO_PATTERN = re.compile(r"\breplace\s+into\b", re.IGNORECASE)
CALL_PATTERN = re.compile(r"^\s*call\b", re.IGNORECASE)
SYSTEM_SCHEMA_PATTERN = re.compile(
    r"\b(information_schema|pg_catalog|pg_toast|performance_schema|mysql)\s*\.|\bsys\s*\.",
    re.IGNORECASE,
)
MAX_SQL_LENGTH_CHARS = 5000

# ``FROM`` / ``IN`` inside these functions is syntax, not a table reference.
_FROM_IN_FUNCTION = re.compile(
    r"\b(?:extract|substring|trim|overlay|position)\s*\([^()]*\)", re.IGNORECASE
)
_LEADING_COMMENTS = re.compile(r"^\s*(?:/\*.*?\*/|--[^\n]*(?:\n|$))\s*", re.DOTALL)
_QUOTE_CHARS = '[]"`'
#: Words that can follow FROM/JOIN without naming a physical table.
_NON_TABLE_WORDS = {"lateral", "select", "values", "unnest", "generate_series", "table"}


def _strip_leading_comments(sql: str) -> str:
    previous = None
    text = sql
    while previous != text:
        previous = text
        text = _LEADING_COMMENTS.sub("", text, count=1)
    return text.strip()


def _is_multiple_statements(trimmed: str) -> bool:
    parts = sqlparse.split(trimmed)
    return len([p for p in parts if p.strip()]) > 1


def _first_token(stmt) -> str:
    for token in stmt.tokens:
        if token.is_whitespace or token.ttype in (sqlparse.tokens.Comment,):
            continue
        return (getattr(token, "normalized", str(token)) or "").lower()
    return ""


def _contains_forbidden_keyword(stmt) -> str | None:
    for token in stmt.flatten():
        if token.is_whitespace or token.ttype in (sqlparse.tokens.Comment, sqlparse.tokens.String):
            continue
        if token.ttype in (Keyword, DML, DDL) or (
            token.ttype is not None and token.ttype in Keyword
        ):
            val = (token.value or "").strip().lower()
            if val in FORBIDDEN_STATEMENT_KEYWORDS:
                return val
    return None


def _strip_string_literals(sql: str) -> str:
    """Blank out single-quoted literals. Double quotes are identifiers, so they are preserved."""
    return re.sub(r"'(?:[^']|'')*'", "''", sql)


def _extract_referenced_tables(trimmed: str) -> set[str]:
    text = _FROM_IN_FUNCTION.sub("", _strip_string_literals(trimmed))
    candidates: set[str] = set()
    for match in re.findall(r'\b(?:from|join)\s+([\w\[\]"`.]+)', text, flags=re.IGNORECASE):
        name = match.strip()
        if name.startswith("("):
            continue
        # ``db..table`` (T-SQL) leaves an empty middle segment; the last one is the table.
        last = name.split(".")[-1].strip(_QUOTE_CHARS).lower()
        if not last or last in _NON_TABLE_WORDS:
            continue
        candidates.add(last)
    return candidates


_CTE_NAME_PATTERN = re.compile(
    r"(?:\bwith\b(?:\s+recursive)?|,)\s+([a-zA-Z_]\w*)\s*(?:\([^)]*\))?\s+as\s*\(", re.IGNORECASE
)


def _extract_cte_names(trimmed: str) -> set[str]:
    return {m.group(1).lower() for m in _CTE_NAME_PATTERN.finditer(trimmed)}


def legacy_validate(sql: str, allowed_tables: Iterable[str] | None = None) -> dict[str, object]:
    """Heuristic read-only validation. Returns ``{"valid": bool, "reason": str}``."""
    raw = (sql or "").strip()
    if not raw:
        return {"valid": False, "reason": "Empty SQL statement"}
    if _is_multiple_statements(raw):
        return {"valid": False, "reason": "Multiple statements are not permitted"}
    if len(raw) > MAX_SQL_LENGTH_CHARS:
        return {"valid": False, "reason": "SQL statement too long"}

    trimmed = _strip_leading_comments(raw)
    if trimmed.endswith(";"):
        trimmed = trimmed[:-1].strip()
    if not trimmed:
        return {"valid": False, "reason": "Empty SQL statement"}
    if not READ_ONLY_PATTERN.match(trimmed):
        return {"valid": False, "reason": "SQL must start with SELECT or WITH (CTE)"}
    if CALL_PATTERN.match(trimmed):
        return {"valid": False, "reason": "Detected forbidden keyword: call"}
    try:
        parsed = sqlparse.parse(trimmed)
        if not parsed:
            return {"valid": False, "reason": "Unable to parse SQL statement"}
        stmt = parsed[0]
    except Exception:
        return {"valid": False, "reason": "SQL parse error"}

    first = _first_token(stmt)
    if not (first.startswith("select") or first.startswith("with") or first.startswith("(")):
        return {"valid": False, "reason": "SQL must start with SELECT or WITH (CTE)"}

    stripped = _strip_string_literals(trimmed)
    if (
        SELECT_INTO_PATTERN.search(stripped)
        and INTO_TARGET_PATTERN.search(stripped)
        and not re.search(r"\binsert\b", stripped, re.IGNORECASE)
    ):
        return {"valid": False, "reason": "SELECT ... INTO is not permitted"}
    if REPLACE_INTO_PATTERN.search(stripped):
        return {"valid": False, "reason": "Detected forbidden keyword: replace"}
    word = _contains_forbidden_keyword(stmt)
    if word:
        return {"valid": False, "reason": f"Detected forbidden keyword: {word}"}
    if SYSTEM_SCHEMA_PATTERN.search(stripped):
        return {"valid": False, "reason": "Access to system schemas is not permitted"}

    if allowed_tables is not None:
        allowed = {t.lower() for t in allowed_tables}
        refs = _extract_referenced_tables(trimmed)
        missing = refs - allowed - _extract_cte_names(trimmed)
        if missing:
            return {
                "valid": False,
                "reason": f"Unknown or unauthorized tables referenced: {', '.join(sorted(missing))}",
            }
    return {"valid": True, "reason": "SQL passed read-only validation"}


__all__ = ["MAX_SQL_LENGTH_CHARS", "legacy_validate"]
