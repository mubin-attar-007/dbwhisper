"""Tests that the metadata extractor is dialect-agnostic (verified via SQLite reflection).

Previously the extractor hard-coded `SELECT DB_NAME()` (MSSQL-only); these tests confirm
enrollment now works against a non-SQL-Server dialect end-to-end at the extraction stage.
"""

from __future__ import annotations

from sqlalchemy import create_engine, text

from app.schema_pipeline.introspector import SQLServerMetadataExtractor


def _make_sqlite_db(path) -> None:
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as conn:
        conn.execute(
            text("CREATE TABLE customers ( id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT)")
        )
        conn.execute(
            text(
                "CREATE TABLE orders ("
                " id INTEGER PRIMARY KEY,"
                " customer_id INTEGER NOT NULL REFERENCES customers(id),"
                " total NUMERIC,"
                " created_at TEXT)"
            )
        )
    engine.dispose()


def test_sqlite_extraction(tmp_path):
    db_file = tmp_path / "demo.db"
    _make_sqlite_db(db_file)

    meta = SQLServerMetadataExtractor(f"sqlite:///{db_file}").extract()

    table_names = {t["table_name"] for t in meta.tables}
    assert {"customers", "orders"} <= table_names

    customer_cols = {c["column_name"] for c in meta.columns if c["table_name"] == "customers"}
    assert {"id", "name", "email"} <= customer_cols

    pk_tables = {pk["table_name"] for pk in meta.primary_keys}
    assert {"customers", "orders"} <= pk_tables

    order_fks = [f for f in meta.foreign_keys if f["table_name"] == "orders"]
    assert any(f["referenced_table"] == "customers" for f in order_fks)


def test_database_name_from_url(tmp_path):
    db_file = tmp_path / "demo.db"
    _make_sqlite_db(db_file)
    meta = SQLServerMetadataExtractor(f"sqlite:///{db_file}").extract()
    assert meta.database_name == "demo.db"
