"""Read-only SQL validation entry point used by v1 callers (``/query``, ``/run_sql``, executor, tools).

Since v2 this is a thin wrapper over :mod:`app.sqlpolicy` (SQLGlot AST policy engine + the original
heuristic validator as an independent second layer). The return shape is unchanged -
``{"valid": bool, "reason": str, ...}`` - with extra keys (``decision``, ``fingerprint``,
``transformed_sql``, ``tables``, ``policy_version``) for callers that want them.
"""

from __future__ import annotations

from app.sqlpolicy.engine import POLICY_VERSION, evaluate
from app.sqlpolicy.legacy_heuristics import MAX_SQL_LENGTH_CHARS
from app.sqlpolicy.scope_loader import scope_from_schema_index
from app.sqlpolicy.types import Dialect, PolicyContext, PolicyDecision, PolicyLevel


def evaluate_sql(
    sql: str,
    db_flag: str | None = None,
    *,
    dialect: str | Dialect | None = None,
    level: PolicyLevel = PolicyLevel.STANDARD,
    default_row_limit: int = 1000,
    inject_limit: bool = False,
) -> PolicyDecision:
    """Full typed decision. ``db_flag`` selects the enrolled schema scope (fail closed if missing)."""
    d = dialect if isinstance(dialect, Dialect) else Dialect.from_db_type(dialect)
    scope = scope_from_schema_index(db_flag, d) if db_flag else None
    ctx = PolicyContext(
        dialect=d,
        scope=scope,
        level=level,
        default_row_limit=default_row_limit,
        inject_limit=inject_limit,
        require_scope=bool(db_flag),
    )
    return evaluate(sql, ctx)


def validate_sql(
    sql: str, db_flag: str | None = None, dialect: str | None = None
) -> dict[str, object]:
    """Validate SQL is read-only and safe (v1-compatible dict result)."""
    return evaluate_sql(sql, db_flag, dialect=dialect).as_legacy_dict()


__all__ = ["MAX_SQL_LENGTH_CHARS", "POLICY_VERSION", "evaluate_sql", "validate_sql"]
