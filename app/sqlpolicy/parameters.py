"""Best-effort parameterisation: lift user-originated literals out of comparison predicates.

Only literals that are compared against a column (``col = 'x'``, ``col IN (1, 2)``,
``col BETWEEN 1 AND 9``, ``col LIKE '%a%'``) are converted; literals that shape the query (LIMIT,
CAST targets, function arguments such as ``date_trunc('month', ...)``) are left alone. When a
statement contains nothing we can safely lift, ``applied`` is ``False`` and the caller records that
parameterisation was *not* applied - the structural validator and least-privilege role remain the
controls. Placeholders are rendered in SQLAlchemy ``:name`` style for every dialect.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlglot import exp

from app.sqlpolicy.types import Dialect

_COMPARISONS: tuple[type[exp.Expression], ...] = (
    exp.EQ,
    exp.NEQ,
    exp.GT,
    exp.GTE,
    exp.LT,
    exp.LTE,
    exp.Like,
    exp.ILike,
)
_PG_PLACEHOLDER = re.compile(r"%\((p\d+)\)s")


@dataclass(slots=True)
class ParameterizedSQL:
    sql: str
    params: dict[str, Any] = field(default_factory=dict)
    applied: bool = False


def _literal_value(lit: exp.Literal) -> Any:
    if lit.is_string:
        return lit.this
    text = lit.this
    try:
        if "." in text or "e" in text.lower():
            return float(Decimal(text))
        return int(text)
    except (ValueError, ArithmeticError):
        return text


def _is_column(node: exp.Expression | None) -> bool:
    return isinstance(node, exp.Column)


def parameterize(root: exp.Expression, dialect: Dialect) -> ParameterizedSQL:
    copy = root.copy()
    params: dict[str, Any] = {}
    counter = 0

    def take(lit: exp.Literal) -> None:
        nonlocal counter
        name = f"p{counter}"
        counter += 1
        params[name] = _literal_value(lit)
        lit.replace(exp.Placeholder(this=name))

    for node in list(copy.find_all(*_COMPARISONS)):
        left, right = node.this, node.expression
        if _is_column(left) and isinstance(right, exp.Literal):
            take(right)
        elif _is_column(right) and isinstance(left, exp.Literal):
            take(left)
    for node in list(copy.find_all(exp.In)):
        if _is_column(node.this):
            for item in list(node.expressions):
                if isinstance(item, exp.Literal):
                    take(item)
    for node in list(copy.find_all(exp.Between)):
        if _is_column(node.this):
            if isinstance(node.args.get("low"), exp.Literal):
                take(node.args["low"])
            if isinstance(node.args.get("high"), exp.Literal):
                take(node.args["high"])

    if not params:
        return ParameterizedSQL(
            sql=root.sql(dialect=dialect.sqlglot_name, comments=False), params={}, applied=False
        )

    rendered = copy.sql(dialect=dialect.sqlglot_name, comments=False)
    rendered = _PG_PLACEHOLDER.sub(r":\1", rendered)  # psycopg style -> SQLAlchemy style
    return ParameterizedSQL(sql=rendered, params=params, applied=True)


__all__ = ["ParameterizedSQL", "parameterize"]
