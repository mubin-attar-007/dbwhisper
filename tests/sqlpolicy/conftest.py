"""Shared scope fixtures for the SQL policy engine tests."""

from __future__ import annotations

import pytest

from app.sqlpolicy import Dialect, PolicyContext, SchemaScope

TABLES = {
    "customers": [
        "id",
        "name",
        "city",
        "email",
        "signup_date",
        "created",
        "updated_at",
        "grant_total",
        "call_count",
    ],
    "orders": ["id", "customer_id", "order_date", "status", "total", "bulk_qty"],
    "order_items": ["id", "order_id", "product_id", "quantity", "unit_price"],
    "products": ["id", "name", "category", "price", "notes"],
    "patients": ["id", "name", "ssn", "dob", "facility_id"],
}
SENSITIVE = {("ssn",), ("dob",)}
DEFAULT_SCHEMA = {
    Dialect.POSTGRES: "demo",
    Dialect.MYSQL: None,
    Dialect.MSSQL: "dbo",
    Dialect.SQLITE: "main",
    Dialect.DUCKDB: "main",
    Dialect.GENERIC: None,
}


def make_scope(dialect: Dialect) -> SchemaScope:
    schema = DEFAULT_SCHEMA[dialect]
    prefix = f"{schema}." if schema else ""
    scope = SchemaScope.from_tables(
        dialect, {f"{prefix}{t}": cols for t, cols in TABLES.items()}, default_schema=schema
    )
    scope.sensitive_columns = {
        ((schema or "").lower(), "patients", "ssn"),
        ((schema or "").lower(), "patients", "dob"),
    }
    return scope


def make_ctx(dialect: Dialect = Dialect.POSTGRES, **overrides) -> PolicyContext:
    params = {"dialect": dialect, "scope": make_scope(dialect)}
    params.update(overrides)
    return PolicyContext(**params)


@pytest.fixture
def pg_ctx() -> PolicyContext:
    return make_ctx(Dialect.POSTGRES)


@pytest.fixture
def mysql_ctx() -> PolicyContext:
    return make_ctx(Dialect.MYSQL)


@pytest.fixture
def mssql_ctx() -> PolicyContext:
    return make_ctx(Dialect.MSSQL)
