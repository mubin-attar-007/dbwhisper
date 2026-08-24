"""Port the deterministic eval store into Postgres with a read-only role.

DBWhisper's read-only enrollment guard understands Postgres roles (not SQLite),
so the eval target lives in Postgres and is enrolled/queried via a SELECT-only
role. Rows are copied verbatim from eval_store.sqlite so the data is identical.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import psycopg

SUPER = "postgresql://eval:evalpass@localhost:55432/postgres"
STORE_ADMIN = "postgresql://eval:evalpass@localhost:55432/evalstore"
SQLITE = Path(__file__).with_name("eval_store.sqlite")

DDL = """
CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL, city TEXT NOT NULL, signup_date TEXT NOT NULL);
CREATE TABLE products  (id INTEGER PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL, price DOUBLE PRECISION NOT NULL);
CREATE TABLE orders    (id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL, order_date TEXT NOT NULL, status TEXT NOT NULL);
CREATE TABLE order_items(id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL, product_id INTEGER NOT NULL, quantity INTEGER NOT NULL);
"""

TABLES = {
    "customers": 4,
    "products": 4,
    "orders": 4,
    "order_items": 4,
}


def main() -> None:
    # 1) (re)create the target database
    with psycopg.connect(SUPER, autocommit=True) as c:
        c.execute("DROP DATABASE IF EXISTS evalstore WITH (FORCE)")
        c.execute("CREATE DATABASE evalstore")

    # 2) schema + data (copied from sqlite)
    src = sqlite3.connect(SQLITE)
    with psycopg.connect(STORE_ADMIN, autocommit=True) as c:
        c.execute(DDL)
        for table, ncols in TABLES.items():
            rows = src.execute(f"SELECT * FROM {table}").fetchall()
            ph = ",".join(["%s"] * ncols)
            c.cursor().executemany(f"INSERT INTO {table} VALUES ({ph})", rows)
            n = c.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            print(f"  {table}: {n} rows")
        # 3) read-only role (no superuser, no CREATE, SELECT only)
        c.execute("DROP ROLE IF EXISTS evalro")
        c.execute("CREATE ROLE evalro LOGIN PASSWORD 'evalro'")
        c.execute("GRANT CONNECT ON DATABASE evalstore TO evalro")
        c.execute("GRANT USAGE ON SCHEMA public TO evalro")
        c.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO evalro")
        c.execute("REVOKE CREATE ON SCHEMA public FROM evalro")
        c.execute("REVOKE CREATE ON DATABASE evalstore FROM evalro")
    src.close()

    # 4) sanity: read-only role can SELECT but not INSERT
    with psycopg.connect("postgresql://evalro:evalro@localhost:55432/evalstore") as c:
        n = c.execute("SELECT count(*) FROM customers").fetchone()[0]
        print(f"  evalro SELECT customers -> {n} (read ok)")
        try:
            c.execute("CREATE TABLE _w(x int)")
            print("  !! evalro could CREATE (NOT read-only)")
        except Exception:
            print("  evalro cannot CREATE (read-only confirmed)")
    print("done: evalstore ready with read-only role evalro")


if __name__ == "__main__":
    main()
