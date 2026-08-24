"""Structural facts read out of a SQL statement, used to score without running anything.

Three of the metrics need to know what a query *refers to* rather than what it returns:

* retrieval recall needs the tables and columns the reference query used;
* hallucination rate needs the tables and columns the generated query used, checked against the
  schema rather than against the reference (a query may legitimately use a table the gold query did
  not, but it may never use one that does not exist);
* execution comparison needs to know whether the reference query declared an ``ORDER BY``, because
  that is what decides whether row order is part of the answer.

Everything here is best-effort parsing with sqlglot and returns empty rather than raising: a fact we
could not extract must degrade a metric visibly, not abort a run. Callers that need to distinguish
"no tables" from "could not parse" read :attr:`SqlFacts.parsed`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

from app.sqlpolicy.types import Dialect


@dataclass(frozen=True, slots=True)
class SqlFacts:
    """What a statement references, in lower case and de-duplicated."""

    parsed: bool
    tables: tuple[str, ...] = ()
    #: ``table.column`` where the alias could be resolved, bare ``column`` otherwise.
    columns: tuple[str, ...] = ()
    #: ``left_table.col -> right_table.col``, sorted so ``a JOIN b`` and ``b JOIN a`` agree.
    join_edges: tuple[str, ...] = ()
    has_order_by: bool = False
    has_group_by: bool = False
    has_aggregate: bool = False
    has_limit: bool = False
    ctes: tuple[str, ...] = ()
    parse_error: str = ""
    #: Columns that could not be attributed to a table (unqualified in a multi-table query).
    unqualified_columns: tuple[str, ...] = field(default=())

    @property
    def table_set(self) -> frozenset[str]:
        return frozenset(self.tables)

    @property
    def column_set(self) -> frozenset[str]:
        return frozenset(self.columns)


def _dialect_name(dialect: str) -> str | None:
    try:
        return Dialect(dialect).sqlglot_name
    except ValueError:
        return Dialect.from_db_type(dialect).sqlglot_name


def sql_facts(sql: str, dialect: str = "sqlite") -> SqlFacts:
    """Parse ``sql`` and report what it references. Never raises."""
    text = (sql or "").strip()
    if not text:
        return SqlFacts(parsed=False, parse_error="empty statement")
    name = _dialect_name(dialect)
    try:
        statements = sqlglot.parse(text, read=name)
    except Exception as exc:  # sqlglot raises several unrelated error classes
        return SqlFacts(parsed=False, parse_error=f"{type(exc).__name__}: {exc}"[:200])
    trees = [s for s in statements if s is not None]
    if not trees:
        return SqlFacts(parsed=False, parse_error="no statement parsed")
    root = trees[0]

    cte_names = {c.alias_or_name.lower() for c in root.find_all(exp.CTE) if c.alias_or_name}
    # Names that stand for a computed result rather than a stored table: a CTE, an alias of one, or
    # a derived table. A column qualified by one of these is not a schema reference at all - its
    # source columns are validated where the CTE or subquery is defined.
    derived_aliases: set[str] = set(cte_names)
    derived_aliases.update(
        subquery.alias.lower() for subquery in root.find_all(exp.Subquery) if subquery.alias
    )

    alias_to_table: dict[str, str] = {}
    tables: list[str] = []
    for node in root.find_all(exp.Table):
        table_name = (node.name or "").lower()
        if not table_name:
            continue
        alias = (node.alias or "").lower()
        if table_name in cte_names:
            if alias:
                derived_aliases.add(alias)
            continue
        if table_name not in tables:
            tables.append(table_name)
        alias_to_table[table_name] = table_name
        if alias:
            alias_to_table[alias] = table_name

    # Select-list aliases look exactly like columns when they are reused in GROUP BY / ORDER BY /
    # HAVING. Counting them as schema references would report a hallucinated column on every correct
    # aggregate query, so they are collected and skipped.
    aliases: set[str] = set()
    for select in root.find_all(exp.Select):
        for projection in select.expressions:
            if isinstance(projection, exp.Alias) and projection.alias:
                aliases.add(projection.alias.lower())

    columns: list[str] = []
    unqualified: list[str] = []
    single_table = tables[0] if len(tables) == 1 else None
    for node in root.find_all(exp.Column):
        column_name = (node.name or "").lower()
        if not column_name or column_name == "*":
            continue
        qualifier = (node.table or "").lower()
        if not qualifier and column_name in aliases:
            continue
        if qualifier and qualifier in derived_aliases:
            continue
        owner = alias_to_table.get(qualifier) if qualifier else single_table
        if owner is None:
            if column_name not in unqualified:
                unqualified.append(column_name)
            qualified = column_name
        else:
            qualified = f"{owner}.{column_name}"
        if qualified not in columns:
            columns.append(qualified)

    edges: list[str] = []
    for condition in root.find_all(exp.EQ):
        left, right = condition.this, condition.expression
        if not isinstance(left, exp.Column) or not isinstance(right, exp.Column):
            continue
        left_owner = alias_to_table.get((left.table or "").lower())
        right_owner = alias_to_table.get((right.table or "").lower())
        if not left_owner or not right_owner or left_owner == right_owner:
            continue
        sides = sorted([f"{left_owner}.{left.name.lower()}", f"{right_owner}.{right.name.lower()}"])
        edge = f"{sides[0]} -> {sides[1]}"
        if edge not in edges:
            edges.append(edge)

    return SqlFacts(
        parsed=True,
        tables=tuple(sorted(tables)),
        columns=tuple(sorted(columns)),
        join_edges=tuple(sorted(edges)),
        has_order_by=root.find(exp.Order) is not None,
        has_group_by=root.find(exp.Group) is not None,
        has_aggregate=root.find(exp.AggFunc) is not None,
        has_limit=root.args.get("limit") is not None,
        ctes=tuple(sorted(cte_names)),
        unqualified_columns=tuple(sorted(unqualified)),
    )


def order_sensitive(sql: str, dialect: str = "sqlite") -> bool:
    """Whether row order is part of the answer for this statement.

    Standard Spider execution match treats order as significant exactly when the reference query
    declares ``ORDER BY``; anything else would mark a correctly-sorted "top 5" answer as equal to an
    arbitrarily-sorted one.
    """
    return sql_facts(sql, dialect).has_order_by


__all__ = ["SqlFacts", "order_sensitive", "sql_facts"]
