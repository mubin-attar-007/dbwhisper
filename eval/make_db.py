"""Build a small, deterministic SQLite store for the golden-query eval.

Reproducible: fixed rows, no randomness. Schema is a tiny e-commerce store
(customers, products, orders, order_items) — enough for joins, aggregates,
filters, and group-bys so the golden set can exercise real NL→SQL.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB = Path(__file__).with_name("eval_store.sqlite")


def build() -> None:
    if DB.exists():
        DB.unlink()
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.executescript(
        """
        CREATE TABLE customers (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, city TEXT NOT NULL, signup_date TEXT NOT NULL
        );
        CREATE TABLE products (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL, price REAL NOT NULL
        );
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL, order_date TEXT NOT NULL,
            status TEXT NOT NULL, FOREIGN KEY(customer_id) REFERENCES customers(id)
        );
        CREATE TABLE order_items (
            id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL, product_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL,
            FOREIGN KEY(order_id) REFERENCES orders(id),
            FOREIGN KEY(product_id) REFERENCES products(id)
        );
        """
    )
    customers = [
        (1, "Aisha Khan", "Mumbai", "2025-01-05"),
        (2, "Rohan Mehta", "Delhi", "2025-01-20"),
        (3, "Priya Nair", "Bengaluru", "2025-02-11"),
        (4, "Vikram Rao", "Mumbai", "2025-03-02"),
        (5, "Sara Ali", "Hyderabad", "2025-03-28"),
        (6, "Karan Shah", "Delhi", "2025-04-15"),
        (7, "Meera Iyer", "Bengaluru", "2025-05-09"),
        (8, "Dev Patel", "Mumbai", "2025-06-01"),
    ]
    products = [
        (1, "Wireless Mouse", "Electronics", 799.0),
        (2, "Mechanical Keyboard", "Electronics", 3499.0),
        (3, "USB-C Hub", "Electronics", 1999.0),
        (4, "Notebook A5", "Stationery", 149.0),
        (5, "Gel Pen Pack", "Stationery", 99.0),
        (6, "Desk Lamp", "Home", 1299.0),
        (7, "Coffee Mug", "Home", 349.0),
        (8, "Standing Desk", "Furniture", 15999.0),
        (9, "Office Chair", "Furniture", 8999.0),
        (10, "Monitor Stand", "Electronics", 1499.0),
    ]
    # 15 orders across the 8 customers, mixed statuses
    orders = [
        (1, 1, "2025-02-01", "shipped"),
        (2, 1, "2025-03-10", "delivered"),
        (3, 2, "2025-02-15", "delivered"),
        (4, 3, "2025-03-05", "shipped"),
        (5, 3, "2025-04-01", "cancelled"),
        (6, 4, "2025-03-20", "delivered"),
        (7, 4, "2025-05-02", "shipped"),
        (8, 5, "2025-04-10", "delivered"),
        (9, 6, "2025-05-01", "shipped"),
        (10, 6, "2025-05-18", "delivered"),
        (11, 7, "2025-05-20", "shipped"),
        (12, 8, "2025-06-05", "delivered"),
        (13, 8, "2025-06-12", "cancelled"),
        (14, 2, "2025-06-15", "shipped"),
        (15, 1, "2025-06-20", "delivered"),
    ]
    # order_items (id, order_id, product_id, quantity)
    order_items = [
        (1, 1, 1, 2), (2, 1, 4, 3), (3, 2, 2, 1), (4, 3, 8, 1), (5, 3, 9, 2),
        (6, 4, 3, 1), (7, 4, 7, 4), (8, 5, 5, 5), (9, 6, 6, 2), (10, 6, 1, 1),
        (11, 7, 2, 1), (12, 7, 10, 1), (13, 8, 4, 10), (14, 9, 9, 1), (15, 9, 6, 1),
        (16, 10, 7, 3), (17, 11, 1, 2), (18, 11, 5, 4), (19, 12, 8, 1), (20, 12, 3, 2),
        (21, 13, 2, 1), (22, 14, 10, 2), (23, 15, 6, 1), (24, 15, 7, 2), (25, 2, 5, 3),
    ]
    cur.executemany("INSERT INTO customers VALUES (?,?,?,?)", customers)
    cur.executemany("INSERT INTO products VALUES (?,?,?,?)", products)
    cur.executemany("INSERT INTO orders VALUES (?,?,?,?)", orders)
    cur.executemany("INSERT INTO order_items VALUES (?,?,?,?)", order_items)
    con.commit()
    con.close()
    print(f"built {DB}  (customers=8 products=10 orders=15 order_items=25)")


if __name__ == "__main__":
    build()
