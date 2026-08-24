"""Row-limit enforcement and pagination by AST transformation (never string concatenation)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlglot import exp

from app.sqlpolicy.types import Dialect


@dataclass(slots=True)
class LimitOutcome:
    expression: exp.Expression
    existing_limit: int | None
    applied_limit: int | None
    reason: str


def existing_limit(root: exp.Expression) -> int | None:
    """The statement's own LIMIT / TOP / FETCH value when it is a plain integer literal."""
    limit = root.args.get("limit")
    if limit is None:
        return None
    literal = limit.find(exp.Literal)
    if literal is not None and literal.is_int:
        return int(literal.this)
    return None


def is_aggregate_only(root: exp.Expression) -> bool:
    """True for queries that return one row by construction (aggregates, no GROUP BY)."""
    if isinstance(root, exp.SetOperation):
        return False
    if not isinstance(root, exp.Select) or root.args.get("group"):
        return False
    projections = root.expressions
    if not projections:
        return False
    for proj in projections:
        node = proj.this if isinstance(proj, exp.Alias) else proj
        if not isinstance(node, exp.AggFunc) and node.find(exp.AggFunc) is None:
            return False
        if node.find(exp.Window) is not None:
            return False
    return True


def apply_row_limit(root: exp.Expression, dialect: Dialect, max_rows: int) -> LimitOutcome:
    """Ensure the outermost query returns at most ``max_rows`` rows.

    * keeps a stricter existing limit untouched;
    * lowers a looser existing limit to ``max_rows``;
    * leaves aggregate-only queries alone (they already return one row);
    * otherwise adds a limit, rendered per dialect (``LIMIT n`` / ``TOP n``) by sqlglot.
    """
    current = existing_limit(root)
    if current is not None and current <= max_rows:
        return LimitOutcome(root, current, None, "existing limit retained")
    if current is None and is_aggregate_only(root):
        return LimitOutcome(root, None, None, "aggregate-only query; no limit needed")
    if not isinstance(root, (exp.Select, exp.SetOperation)):
        return LimitOutcome(root, current, None, "unsupported root; limit not applied")
    if current is None and dialect is Dialect.MSSQL and isinstance(root, exp.SetOperation):
        # TOP cannot be attached to a bare UNION in T-SQL; wrap it first.
        wrapped = exp.select("*").from_(root.subquery("_dbw_limited"))
        return LimitOutcome(wrapped.limit(max_rows), None, max_rows, "limit applied via wrapper")
    limited = root.limit(max_rows)
    reason = "limit lowered to policy maximum" if current is not None else "limit applied"
    return LimitOutcome(limited, current, max_rows, reason)


def apply_pagination(
    root: exp.Expression, dialect: Dialect, page: int, page_size: int
) -> exp.Expression:
    """Wrap a query for server-side pagination. T-SQL requires ORDER BY for OFFSET/FETCH."""
    if page < 1 or page_size < 1:
        raise ValueError("page and page_size must be >= 1")
    offset = (page - 1) * page_size
    if dialect is Dialect.MSSQL:
        if root.args.get("order") is None:
            raise ValueError(
                "SQL Server pagination requires an explicit ORDER BY clause when requesting a page."
            )
        paged = root.copy()
        paged.set("limit", None)
        return paged.offset(offset).limit(page_size)
    return exp.select("*").from_(root.subquery("_dbw_page")).limit(page_size).offset(offset)


def count_wrapper(root: exp.Expression) -> exp.Expression:
    """``SELECT COUNT(*) FROM (<query>) AS _dbw_count`` built from the AST."""
    inner = root.copy()
    inner.set("limit", None)
    inner.set("offset", None)
    return exp.select(exp.Count(this=exp.Star())).from_(inner.subquery("_dbw_count"))


__all__ = [
    "LimitOutcome",
    "apply_pagination",
    "apply_row_limit",
    "count_wrapper",
    "existing_limit",
    "is_aggregate_only",
]
