"""Typed building blocks of the SQL policy engine.

Nothing in this module touches a database or a model. It defines what a *decision* looks like, what
the engine needs to know about the enrolled schema (:class:`SchemaScope`), and the knobs a caller can
turn (:class:`PolicyContext`, :class:`ComplexityLimits`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

from sqlglot.schema import MappingSchema


class Dialect(StrEnum):
    """SQL dialects the policy engine can parse. Values are sqlglot dialect names."""

    POSTGRES = "postgres"
    MYSQL = "mysql"
    MSSQL = "tsql"
    SQLITE = "sqlite"
    DUCKDB = "duckdb"
    GENERIC = "generic"

    @classmethod
    def from_db_type(cls, value: str | None) -> Dialect:
        """Map the free-form ``db_type`` strings used across the codebase to a dialect."""
        v = (value or "").strip().lower().replace("_", "").replace("-", "").replace(" ", "")
        if not v:
            return cls.GENERIC
        if v in {"postgres", "postgresql", "pg", "psycopg", "psycopg2", "neon"} or "postgre" in v:
            return cls.POSTGRES
        if v in {"mysql", "mariadb", "maria", "pymysql"} or "mysql" in v or "maria" in v:
            return cls.MYSQL
        if v in {"mssql", "sqlserver", "tsql", "pyodbc", "azuresql"} or "sqlserver" in v:
            return cls.MSSQL
        if "sqlite" in v:
            return cls.SQLITE
        if "duckdb" in v:
            return cls.DUCKDB
        return cls.GENERIC

    @property
    def sqlglot_name(self) -> str | None:
        return None if self is Dialect.GENERIC else self.value

    @property
    def default_schema(self) -> str | None:
        return {
            Dialect.POSTGRES: "public",
            Dialect.MSSQL: "dbo",
            Dialect.SQLITE: "main",
            Dialect.DUCKDB: "main",
        }.get(self)

    @property
    def supports_limit_keyword(self) -> bool:
        return self is not Dialect.MSSQL


class PolicyLevel(StrEnum):
    STANDARD = "standard"
    STRICT = "strict"
    ADVANCED = "advanced"
    ADMIN_REVIEWED = "admin_reviewed"


class Decision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    NEEDS_APPROVAL = "needs_approval"


Severity = Literal["deny", "approval", "info"]


@dataclass(slots=True)
class RuleResult:
    rule_id: str
    passed: bool
    severity: Severity
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TableRef:
    schema: str | None
    table: str

    @property
    def key(self) -> tuple[str, str]:
        return ((self.schema or "").lower(), self.table.lower())

    def __str__(self) -> str:
        return f"{self.schema}.{self.table}" if self.schema else self.table


@dataclass(frozen=True, slots=True)
class ColumnRef:
    schema: str | None
    table: str
    column: str

    def __str__(self) -> str:
        prefix = f"{self.schema}." if self.schema else ""
        return f"{prefix}{self.table}.{self.column}"


@dataclass(slots=True)
class SchemaScope:
    """The exact set of objects a query may reference (one schema snapshot, one data source).

    ``tables`` maps ``schema -> table -> columns`` with every key **lower-cased**; an empty column set
    means "columns unknown" (column validation is then skipped for that table, table validation still
    applies).
    """

    dialect: Dialect
    tables: dict[str, dict[str, set[str]]]
    default_schema: str | None = None
    sensitive_columns: set[tuple[str, str, str]] = field(default_factory=set)
    snapshot_id: str | None = None
    source_id: str | None = None

    @classmethod
    def from_tables(
        cls,
        dialect: Dialect,
        tables: dict[str, list[str] | set[str]],
        *,
        default_schema: str | None = None,
        **kwargs: Any,
    ) -> SchemaScope:
        """Build from ``{"schema.table": [columns]}`` (or ``"table"`` for the default schema)."""
        out: dict[str, dict[str, set[str]]] = {}
        for qualified, cols in tables.items():
            schema, _, table = qualified.rpartition(".")
            schema = (schema or default_schema or "").lower()
            out.setdefault(schema, {})[table.lower()] = {c.lower() for c in cols}
        if default_schema is None and len(out) == 1:
            default_schema = next(iter(out)) or None
        return cls(dialect=dialect, tables=out, default_schema=default_schema, **kwargs)

    # -- resolution ---------------------------------------------------------------------------
    def resolve(self, ref: TableRef) -> tuple[str, str] | None:
        """Return the canonical ``(schema, table)`` for a reference, or ``None`` if not enrolled."""
        table = ref.table.lower()
        if ref.schema:
            schema = ref.schema.lower()
            if table in self.tables.get(schema, {}):
                return (schema, table)
            return None
        if self.default_schema and table in self.tables.get(self.default_schema.lower(), {}):
            return (self.default_schema.lower(), table)
        matches = [s for s, ts in self.tables.items() if table in ts]
        if len(matches) == 1:
            return (matches[0], table)
        return None

    def columns_for(self, schema: str, table: str) -> set[str]:
        return self.tables.get(schema.lower(), {}).get(table.lower(), set())

    def is_sensitive(self, schema: str, table: str, column: str) -> bool:
        return (schema.lower(), table.lower(), column.lower()) in self.sensitive_columns

    @property
    def all_schemas(self) -> set[str]:
        return {s for s in self.tables if s}

    def to_mapping_schema(self) -> MappingSchema:
        """A sqlglot schema with lower-cased keys (callers lower-case identifiers before qualify).

        Schema-less scopes (MySQL-style, every table under the ``""`` key) produce a flat
        ``{table: {column: type}}`` mapping; otherwise ``{schema: {table: {...}}}``.
        """
        if set(self.tables) == {""}:
            flat: dict[str, dict[str, str]] = {}
            for table, cols in self.tables[""].items():
                if cols:
                    flat[table] = dict.fromkeys(sorted(cols), "unknown")
            return MappingSchema(flat, normalize=False)
        mapping: dict[str, dict[str, dict[str, str]]] = {}
        for schema, tables in self.tables.items():
            bucket = mapping.setdefault(schema or (self.default_schema or "").lower(), {})
            for table, cols in tables.items():
                if cols:
                    bucket[table] = dict.fromkeys(sorted(cols), "unknown")
        return MappingSchema(mapping, normalize=False)

    @property
    def is_schemaless(self) -> bool:
        return set(self.tables) == {""}


@dataclass(slots=True)
class ComplexityLimits:
    max_sql_length: int = 5000
    max_joins: int = 8
    max_subquery_depth: int = 4
    max_cte_count: int = 6
    max_set_operations: int = 4
    max_selected_columns: int = 60
    max_row_limit: int = 10_000
    allow_recursive_cte: bool = False
    allow_cartesian: bool = False
    allow_window_functions: bool = True

    @classmethod
    def for_level(cls, level: PolicyLevel) -> ComplexityLimits:
        if level is PolicyLevel.STRICT:
            return cls(
                max_sql_length=3000,
                max_joins=4,
                max_subquery_depth=2,
                max_cte_count=3,
                max_set_operations=1,
                max_selected_columns=30,
                max_row_limit=1_000,
                allow_window_functions=False,
            )
        if level is PolicyLevel.ADVANCED:
            return cls(
                max_sql_length=12_000,
                max_joins=16,
                max_subquery_depth=8,
                max_cte_count=16,
                max_set_operations=8,
                max_selected_columns=150,
                max_row_limit=50_000,
                allow_recursive_cte=True,
            )
        if level is PolicyLevel.ADMIN_REVIEWED:
            return cls(
                max_sql_length=50_000,
                max_joins=64,
                max_subquery_depth=16,
                max_cte_count=64,
                max_set_operations=32,
                max_selected_columns=500,
                max_row_limit=200_000,
                allow_recursive_cte=True,
                allow_cartesian=True,
            )
        return cls()


SensitiveAction = Literal["deny", "approval", "allow"]


@dataclass(slots=True)
class PolicyContext:
    dialect: Dialect = Dialect.GENERIC
    scope: SchemaScope | None = None
    level: PolicyLevel = PolicyLevel.STANDARD
    limits: ComplexityLimits | None = None
    default_row_limit: int = 1000
    inject_limit: bool = True
    parameterize: bool = False
    sensitive_action: SensitiveAction = "approval"
    require_scope: bool = True
    run_legacy_layer: bool = True

    def effective_limits(self) -> ComplexityLimits:
        return self.limits or ComplexityLimits.for_level(self.level)


@dataclass(slots=True)
class PolicyDecision:
    decision: Decision
    policy_version: str
    dialect: Dialect
    original_sql: str
    reasons: list[str] = field(default_factory=list)
    rules: list[RuleResult] = field(default_factory=list)
    normalized_sql: str | None = None
    transformed_sql: str | None = None
    fingerprint: str | None = None
    statement_type: str | None = None
    tables: list[TableRef] = field(default_factory=list)
    columns: list[ColumnRef] = field(default_factory=list)
    ctes: list[str] = field(default_factory=list)
    complexity: dict[str, Any] = field(default_factory=dict)
    existing_limit: int | None = None
    limit_applied: int | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    parameterized: bool = False
    sensitive_columns_touched: list[ColumnRef] = field(default_factory=list)
    legacy_agrees: bool | None = None

    @property
    def valid(self) -> bool:
        return self.decision is Decision.ALLOW

    @property
    def reason(self) -> str:
        if self.reasons:
            return "; ".join(self.reasons)
        return "SQL passed read-only policy"

    @property
    def execution_sql(self) -> str:
        return self.transformed_sql or self.original_sql

    def as_legacy_dict(self) -> dict[str, Any]:
        """The ``{"valid", "reason"}`` shape v1 callers expect, plus v2 details."""
        return {
            "valid": self.valid,
            "reason": self.reason,
            "decision": self.decision.value,
            "policy_version": self.policy_version,
            "fingerprint": self.fingerprint,
            "statement_type": self.statement_type,
            "tables": [str(t) for t in self.tables],
            "transformed_sql": self.transformed_sql,
            "limit_applied": self.limit_applied,
            "complexity": dict(self.complexity),
            "rules": [
                {
                    "rule": r.rule_id,
                    "passed": r.passed,
                    "severity": r.severity,
                    "message": r.message,
                }
                for r in self.rules
            ],
        }


__all__ = [
    "ColumnRef",
    "ComplexityLimits",
    "Decision",
    "Dialect",
    "PolicyContext",
    "PolicyDecision",
    "PolicyLevel",
    "RuleResult",
    "SchemaScope",
    "TableRef",
]
