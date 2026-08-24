"""Parsing, normalisation and fingerprinting (fail closed on anything we cannot parse)."""

from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError

from app.sqlpolicy.types import Dialect


class PolicyParseError(ValueError):
    """Raised when SQL cannot be reduced to exactly one well-formed statement."""


@dataclass(slots=True)
class ParsedSQL:
    expression: exp.Expression
    statement_type: str
    dialect: Dialect
    cleaned_sql: str


# Whitespace that is legal in SQL text. Anything else in the separator/format categories is
# treated as obfuscation (NBSP, zero-width joiners, line/paragraph separators, vertical tab...).
_ALLOWED_CONTROL = {"\t", "\n", "\r"}


def find_invisible_characters(sql: str) -> list[str]:
    """Return a list of offending code points (as ``U+XXXX``) found outside string literals."""
    bad: list[str] = []
    in_single = False
    in_double = False
    for ch in sql:
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        if in_single or in_double:
            continue
        cat = unicodedata.category(ch)
        if ch in _ALLOWED_CONTROL or ch == " ":
            continue
        if cat in {"Cf", "Zl", "Zp", "Cc", "Zs", "Co", "Cn"}:
            bad.append(f"U+{ord(ch):04X}")
    return bad


def parse_single_statement(sql: str, dialect: Dialect) -> ParsedSQL:
    cleaned = (sql or "").strip()
    if not cleaned:
        raise PolicyParseError("Empty SQL statement")
    invisible = find_invisible_characters(cleaned)
    if invisible:
        raise PolicyParseError(
            "SQL contains invisible or non-standard whitespace characters: "
            + ", ".join(sorted(set(invisible))[:5])
        )
    try:
        statements = sqlglot.parse(cleaned, read=dialect.sqlglot_name)
    except (ParseError, TokenError) as exc:
        raise PolicyParseError(f"SQL parse error: {_short(exc)}") from exc
    except Exception as exc:  # pragma: no cover - sqlglot internal errors are rare
        raise PolicyParseError(f"SQL parse error: {type(exc).__name__}") from exc

    real = [s for s in statements if s is not None]
    if not real:
        raise PolicyParseError("Empty SQL statement")
    if len(real) > 1:
        raise PolicyParseError("Multiple statements are not permitted")
    expression = real[0]
    # "(SELECT ...)" parses as a bare subquery; unwrap it so the root is the query itself.
    while isinstance(expression, (exp.Subquery, exp.Paren)) and isinstance(
        expression.this, exp.Expression
    ):
        expression = expression.this
    return ParsedSQL(
        expression=expression,
        statement_type=type(expression).__name__,
        dialect=dialect,
        cleaned_sql=cleaned,
    )


def normalized_sql(expression: exp.Expression, dialect: Dialect) -> str:
    """A canonical rendering used for display: comments stripped, identifiers lower-cased."""
    copy = expression.copy()
    for ident in copy.find_all(exp.Identifier):
        ident.set("this", ident.this.lower())
    return copy.sql(dialect=dialect.sqlglot_name, comments=False, pretty=False)


def fingerprint(expression: exp.Expression, dialect: Dialect) -> str:
    """SHA-256 over the normalised statement with every literal replaced by ``?``.

    Two queries that differ only in literal values (``city = 'Mumbai'`` vs ``city = 'Delhi'``) share a
    fingerprint; approvals and verified-query matching are bound to this value.
    """
    copy = expression.copy()
    for lit in list(copy.find_all(exp.Literal)):
        lit.replace(exp.Placeholder())
    text = normalized_sql(copy, dialect)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _short(exc: Exception, limit: int = 160) -> str:
    text = " ".join(str(exc).split())
    # sqlglot embeds ANSI underline markers around the failing token; strip them for API output.
    for marker in ("\x1b[4m", "\x1b[0m"):
        text = text.replace(marker, "")
    return text[:limit]


__all__ = [
    "ParsedSQL",
    "PolicyParseError",
    "find_invisible_characters",
    "fingerprint",
    "normalized_sql",
    "parse_single_statement",
]
