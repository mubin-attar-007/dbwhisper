"""Seed a small, self-contained demo dataset into the project's Postgres (Neon).

Creates a ``demo`` schema with a tiny retail model (customers / products / orders /
order_items) so the live ``/query`` endpoint works out of the box. Idempotent.

Usage:
    uv run python scripts/seed_demo_db.py
Reads POSTGRES_CONNECTION_STRING from the environment (.env).
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

DDL = """
CREATE SCHEMA IF NOT EXISTS demo;

CREATE TABLE IF NOT EXISTS demo.customers (
    id          integer PRIMARY KEY,
    name        text NOT NULL,
    email       text,
    city        text,
    signup_date date
);

CREATE TABLE IF NOT EXISTS demo.products (
    id       integer PRIMARY KEY,
    name     text NOT NULL,
    category text,
    price    numeric(10, 2)
);

CREATE TABLE IF NOT EXISTS demo.orders (
    id          integer PRIMARY KEY,
    customer_id integer NOT NULL REFERENCES demo.customers (id),
    order_date  date,
    status      text
);

CREATE TABLE IF NOT EXISTS demo.order_items (
    id         integer PRIMARY KEY,
    order_id   integer NOT NULL REFERENCES demo.orders (id),
    product_id integer NOT NULL REFERENCES demo.products (id),
    quantity   integer NOT NULL,
    unit_price numeric(10, 2) NOT NULL
);
"""

CUSTOMERS = [
    (1, "Ava Sharma", "ava@example.com", "Mumbai", "2024-01-12"),
    (2, "Liam Patel", "liam@example.com", "Delhi", "2024-02-03"),
    (3, "Noah Khan", "noah@example.com", "Bengaluru", "2024-02-20"),
    (4, "Mia Reddy", "mia@example.com", "Hyderabad", "2024-03-11"),
    (5, "Ethan Gupta", "ethan@example.com", "Pune", "2024-04-01"),
    (6, "Zoe Mehta", "zoe@example.com", "Mumbai", "2024-04-18"),
    (7, "Kai Nair", "kai@example.com", "Chennai", "2024-05-09"),
    (8, "Aria Bose", "aria@example.com", "Kolkata", "2024-06-22"),
]

PRODUCTS = [
    (1, "Wireless Mouse", "Electronics", 19.99),
    (2, "Mechanical Keyboard", "Electronics", 79.50),
    (3, "USB-C Hub", "Electronics", 34.00),
    (4, "Notebook", "Stationery", 4.50),
    (5, "Desk Lamp", "Home", 24.75),
    (6, "Coffee Mug", "Home", 9.25),
]

ORDERS = [
    (1, 1, "2024-05-01", "completed"),
    (2, 1, "2024-06-15", "completed"),
    (3, 2, "2024-05-20", "completed"),
    (4, 3, "2024-06-02", "shipped"),
    (5, 4, "2024-06-10", "completed"),
    (6, 5, "2024-06-18", "cancelled"),
    (7, 6, "2024-07-01", "completed"),
    (8, 6, "2024-07-03", "shipped"),
    (9, 7, "2024-07-09", "completed"),
    (10, 8, "2024-07-12", "completed"),
    (11, 2, "2024-07-15", "completed"),
    (12, 3, "2024-07-20", "completed"),
]

ORDER_ITEMS = [
    (1, 1, 1, 2, 19.99),
    (2, 1, 4, 5, 4.50),
    (3, 2, 2, 1, 79.50),
    (4, 3, 3, 1, 34.00),
    (5, 3, 1, 1, 19.99),
    (6, 4, 5, 2, 24.75),
    (7, 5, 2, 1, 79.50),
    (8, 5, 6, 4, 9.25),
    (9, 6, 1, 1, 19.99),
    (10, 7, 3, 2, 34.00),
    (11, 7, 4, 10, 4.50),
    (12, 8, 5, 1, 24.75),
    (13, 9, 2, 1, 79.50),
    (14, 9, 1, 1, 19.99),
    (15, 10, 6, 6, 9.25),
    (16, 10, 4, 3, 4.50),
    (17, 11, 3, 1, 34.00),
    (18, 11, 5, 2, 24.75),
    (19, 12, 1, 4, 19.99),
    (20, 12, 2, 1, 79.50),
]


def main() -> None:
    url = os.environ["POSTGRES_CONNECTION_STRING"]
    engine = create_engine(url, pool_pre_ping=True)
    with engine.begin() as conn:
        for stmt in filter(None, (s.strip() for s in DDL.split(";"))):
            conn.execute(text(stmt))
        # Idempotent reseed.
        conn.execute(
            text(
                "TRUNCATE demo.order_items, demo.orders, demo.products, demo.customers "
                "RESTART IDENTITY CASCADE"
            )
        )
        conn.execute(
            text(
                "INSERT INTO demo.customers (id, name, email, city, signup_date) "
                "VALUES (:id, :name, :email, :city, :signup_date)"
            ),
            [
                dict(zip(("id", "name", "email", "city", "signup_date"), row, strict=True))
                for row in CUSTOMERS
            ],
        )
        conn.execute(
            text(
                "INSERT INTO demo.products (id, name, category, price) "
                "VALUES (:id, :name, :category, :price)"
            ),
            [dict(zip(("id", "name", "category", "price"), row, strict=True)) for row in PRODUCTS],
        )
        conn.execute(
            text(
                "INSERT INTO demo.orders (id, customer_id, order_date, status) "
                "VALUES (:id, :customer_id, :order_date, :status)"
            ),
            [
                dict(zip(("id", "customer_id", "order_date", "status"), row, strict=True))
                for row in ORDERS
            ],
        )
        conn.execute(
            text(
                "INSERT INTO demo.order_items (id, order_id, product_id, quantity, unit_price) "
                "VALUES (:id, :order_id, :product_id, :quantity, :unit_price)"
            ),
            [
                dict(zip(("id", "order_id", "product_id", "quantity", "unit_price"), row, strict=True))
                for row in ORDER_ITEMS
            ],
        )
    with engine.connect() as conn:
        counts = {
            t: conn.execute(text(f"SELECT count(*) FROM demo.{t}")).scalar()
            for t in ("customers", "products", "orders", "order_items")
        }
    print("Seeded demo schema:", counts)


if __name__ == "__main__":
    main()
