"""Scope-aware object resolution: every physical table and column must exist in the snapshot."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlglot import exp
from sqlglot.errors import OptimizeError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import traverse_scope

from app.sqlpolicy.rules import is_system_object
from app.sqlpolicy.types import ColumnRef, Dialect, RuleResult, SchemaScope, TableRef


@dataclass(slots=True)
class ObjectReport:
    tables: list[TableRef] = field(default_factory=list)
    columns: list[ColumnRef] = field(default_factory=list)
    ctes: list[str] = field(default_factory=list)
    sensitive: list[ColumnRef] = field(default_factory=list)
    rules: list[RuleResult] = field(default_factory=list)

    @property
    def denied(self) -> bool:
        return any(not r.passed and r.severity == "deny" for r in self.rules)


def _cte_names(expression: exp.Expression) -> set[str]:
    return {cte.alias_or_name.lower() for cte in expression.find_all(exp.CTE) if cte.alias_or_name}


def _physical_tables(expression: exp.Expression) -> list[exp.Table]:
    """Every ``Table`` node that is not a reference to a CTE defined in the statement."""
    ctes = _cte_names(expression)
    seen: set[tuple[str, str, str]] = set()
    out: list[exp.Table] = []
    # traverse_scope gives us tables in source position (FROM/JOIN); find_all catches the rest
    # (e.g. table-valued function arguments, LATERAL) so nothing slips through.
    candidates: list[exp.Table] = []
    try:
        for scope in traverse_scope(expression):
            candidates.extend(t for t in scope.sources.values() if isinstance(t, exp.Table))
    except Exception:  # pragma: no cover - scope building can fail on exotic syntax
        pass
    candidates.extend(expression.find_all(exp.Table))
    for table in candidates:
        name = table.name
        if not name:
            continue  # table-valued function or unnamed source
        if not table.db and name.lower() in ctes:
            continue
        key = (table.catalog.lower(), table.db.lower(), name.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(table)
    return out


def _lowercase_identifiers(expression: exp.Expression) -> exp.Expression:
    copy = expression.copy()
    for ident in copy.find_all(exp.Identifier):
        ident.set("this", ident.this.lower())
        ident.set("quoted", False)
    return copy


def collect_objects(
    expression: exp.Expression,
    dialect: Dialect,
    scope: SchemaScope | None,
    *,
    require_scope: bool,
) -> ObjectReport:
    report = ObjectReport(ctes=sorted(_cte_names(expression)))
    tables = _physical_tables(expression)

    # 1. system catalogs and cross-database references are denied regardless of scope
    for table in tables:
        ref = TableRef(schema=table.db or None, table=table.name)
        if table.catalog:
            report.rules.append(
                RuleResult(
                    "objects.cross_database",
                    False,
                    "deny",
                    f"Cross-database reference is not permitted: {table.catalog}.{ref}",
                    {"catalog": table.catalog, "table": str(ref)},
                )
            )
            continue
        if is_system_object(ref.schema, ref.table):
            report.rules.append(
                RuleResult(
                    "objects.system_catalog",
                    False,
                    "deny",
                    f"Access to system catalogs is not permitted: {ref}",
                    {"table": str(ref)},
                )
            )
            continue
        report.tables.append(ref)

    if report.denied:
        return report

    # 2. allowlist against the snapshot
    if scope is None:
        if require_scope:
            report.rules.append(
                RuleResult(
                    "objects.no_scope",
                    False,
                    "deny",
                    "No schema snapshot is available for this database; enroll it before querying.",
                )
            )
        else:
            report.rules.append(
                RuleResult(
                    "objects.no_scope",
                    True,
                    "info",
                    "No schema scope supplied; table/column allowlisting skipped.",
                )
            )
        return report

    resolved: list[tuple[TableRef, tuple[str, str]]] = []
    unknown: list[str] = []
    for ref in report.tables:
        hit = scope.resolve(ref)
        if hit is None:
            unknown.append(str(ref))
        else:
            resolved.append((ref, hit))
    if unknown:
        report.rules.append(
            RuleResult(
                "objects.unknown_table",
                False,
                "deny",
                "Unknown or unauthorized tables referenced: " + ", ".join(sorted(unknown)),
                {"tables": sorted(unknown)},
            )
        )
        return report
    report.tables = [TableRef(schema=hit[0] or None, table=hit[1]) for _, hit in resolved]
    report.rules.append(
        RuleResult(
            "objects.tables",
            True,
            "info",
            f"{len(report.tables)} enrolled table(s) referenced",
            {"tables": [str(t) for t in report.tables]},
        )
    )

    # 3. column resolution through sqlglot's qualifier (CTE/alias/derived-table aware)
    tables_with_columns = [hit for _, hit in resolved if scope.columns_for(*hit)]
    if not tables_with_columns:
        report.rules.append(
            RuleResult(
                "objects.columns",
                True,
                "info",
                "Snapshot has no column metadata for the referenced tables; column check skipped.",
            )
        )
        return report

    default_schema = (scope.default_schema or dialect.default_schema or "").lower() or None
    if default_schema is None and len(scope.all_schemas) == 1:
        default_schema = next(iter(scope.all_schemas))
    if scope.is_schemaless:
        default_schema = None
    try:
        qualified = qualify(
            _lowercase_identifiers(expression),
            schema=scope.to_mapping_schema(),
            db=default_schema,
            dialect=dialect.sqlglot_name,
            validate_qualify_columns=True,
            infer_schema=False,
            identify=False,
            quote_identifiers=False,
        )
    except OptimizeError as exc:
        report.rules.append(
            RuleResult(
                "objects.unknown_column",
                False,
                "deny",
                f"Column reference could not be resolved against the enrolled schema: {_clean(exc)}",
            )
        )
        return report
    except Exception as exc:  # fail closed, but tell the caller why
        report.rules.append(
            RuleResult(
                "objects.column_resolution_failed",
                False,
                "deny",
                f"Column resolution failed ({type(exc).__name__}); refusing to execute unverified SQL.",
            )
        )
        return report

    alias_to_table: dict[str, tuple[str | None, str]] = {}
    for sc in traverse_scope(qualified):
        for alias, source in sc.sources.items():
            if isinstance(source, exp.Table) and source.name:
                alias_to_table[alias.lower()] = (source.db or default_schema, source.name)

    seen: set[tuple[str, str, str]] = set()
    for col in qualified.find_all(exp.Column):
        if not col.table:
            continue
        mapped = alias_to_table.get(col.table.lower())
        if mapped is None:
            continue  # column of a CTE / derived table; its source columns are validated separately
        schema, table = mapped
        key = ((schema or "").lower(), table.lower(), col.name.lower())
        if key in seen:
            continue
        seen.add(key)
        ref = ColumnRef(schema=schema, table=table, column=col.name)
        report.columns.append(ref)
        if scope.is_sensitive(*key):
            report.sensitive.append(ref)
    report.rules.append(
        RuleResult(
            "objects.columns",
            True,
            "info",
            f"{len(report.columns)} column reference(s) resolved",
        )
    )
    return report


def _clean(exc: Exception, limit: int = 140) -> str:
    text = " ".join(str(exc).split())
    return text[:limit]


__all__ = ["ObjectReport", "collect_objects"]
