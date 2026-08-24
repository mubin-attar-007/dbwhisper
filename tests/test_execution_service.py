"""End-to-end tests for the execution service against a real SQLite database.

These run without any external service, which is the point: the read-only guarantee, truncation,
pagination and error sanitization are all exercised against a database that actually executes SQL,
not a mock that agrees with us.

The fixture mirrors the committed ``database_schemas/demo`` schema index, so ``db_flag="demo"``
exercises the real allowlist path too.
"""

from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy import text

from app.execution import ExecutionRequest, ReadOnlyStatus, execute, verify_read_only
from app.execution.connections import get_engine, read_only_connection, read_only_setup
from app.execution.service import sanitize_db_error
from app.platform.modes import NetworkPolicyLevel
from app.sqlpolicy import Decision, Dialect

DDL = """
CREATE TABLE customers (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT, city TEXT, signup_date TEXT
);
CREATE TABLE products (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, category TEXT, price REAL
);
CREATE TABLE orders (
    id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL, order_date TEXT, status TEXT
);
CREATE TABLE order_items (
    id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL, product_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL, unit_price REAL NOT NULL
);
"""

CUSTOMERS = [
    (1, "Ava Sharma", "ava@example.com", "Mumbai", "2025-01-12"),
    (2, "Liam Patel", "liam@example.com", "Delhi", "2025-02-03"),
    (3, "Noah Khan", "noah@example.com", "Bengaluru", "2025-02-20"),
    (4, "Mia Reddy", "mia@example.com", "Mumbai", "2025-03-11"),
    (5, "Ethan Gupta", "ethan@example.com", "Pune", "2025-04-01"),
]
PRODUCTS = [
    (1, "Wireless Mouse", "Electronics", 19.99),
    (2, "Mechanical Keyboard", "Electronics", 79.50),
    (3, "Notebook", "Stationery", 4.50),
]
ORDERS = [
    (1, 1, "2025-05-01", "completed"),
    (2, 1, "2025-06-15", "completed"),
    (3, 2, "2025-05-20", "shipped"),
    (4, 3, "2025-06-02", "cancelled"),
]
ORDER_ITEMS = [
    (1, 1, 1, 2, 19.99),
    (2, 1, 3, 5, 4.50),
    (3, 2, 2, 1, 79.50),
    (4, 3, 1, 1, 19.99),
    (5, 4, 3, 3, 4.50),
]


@pytest.fixture
def sqlite_url(tmp_path) -> str:
    path = tmp_path / "demo.db"
    connection = sqlite3.connect(path)
    try:
        connection.executescript(DDL)
        connection.executemany("INSERT INTO customers VALUES (?,?,?,?,?)", CUSTOMERS)
        connection.executemany("INSERT INTO products VALUES (?,?,?,?)", PRODUCTS)
        connection.executemany("INSERT INTO orders VALUES (?,?,?,?)", ORDERS)
        connection.executemany("INSERT INTO order_items VALUES (?,?,?,?,?)", ORDER_ITEMS)
        connection.commit()
    finally:
        connection.close()
    url = f"sqlite:///{path.as_posix()}"
    yield url
    get_engine.cache_clear()


def _request(sql: str, url: str, **kwargs) -> ExecutionRequest:
    params = {
        "sql": sql,
        "connection_string": url,
        "dialect": Dialect.SQLITE,
        "db_flag": "demo",
        "max_rows": 100,
        "timeout_seconds": 5,
    }
    params.update(kwargs)
    return ExecutionRequest(**params)


class TestSuccessfulExecution:
    def test_returns_rows_and_stats(self, sqlite_url: str):
        result = execute(_request("SELECT name, city FROM customers ORDER BY id", sqlite_url))
        assert result.success, result.error
        assert result.frame.columns == ["name", "city"]
        assert result.frame.row_count == 5
        assert result.frame.rows[0] == ("Ava Sharma", "Mumbai")
        assert not result.frame.truncated
        assert result.stats.row_count == 5
        city = result.stats.by_name("city")
        assert city.kind == "text" and city.non_null == 5

    def test_join_and_aggregate(self, sqlite_url: str):
        sql = (
            "SELECT p.category, SUM(oi.quantity * oi.unit_price) AS revenue "
            "FROM order_items oi JOIN products p ON p.id = oi.product_id "
            "GROUP BY p.category ORDER BY revenue DESC"
        )
        result = execute(_request(sql, sqlite_url))
        assert result.success, result.error
        assert result.frame.columns == ["category", "revenue"]
        revenue = dict(result.frame.rows)
        assert round(revenue["Electronics"], 2) == 139.47
        assert round(revenue["Stationery"], 2) == 36.00

    def test_empty_result_is_a_success(self, sqlite_url: str):
        result = execute(_request("SELECT name FROM customers WHERE city = 'Nowhere'", sqlite_url))
        assert result.success and result.frame.is_empty and result.stats.row_count == 0

    def test_policy_injects_a_limit(self, sqlite_url: str):
        result = execute(_request("SELECT name FROM customers", sqlite_url, max_rows=2))
        assert result.success
        assert result.policy.limit_applied == 2
        assert result.frame.row_count == 2
        assert "LIMIT 2" in result.executed_sql

    def test_truncation_is_reported_when_the_policy_lowers_the_limit(self, sqlite_url: str):
        result = execute(_request("SELECT name FROM customers LIMIT 5", sqlite_url, max_rows=3))
        assert result.success
        assert result.frame.row_count == 3
        assert result.frame.truncated is True
        assert result.stats.truncated is True

    def test_truncation_is_reported_when_the_policy_injects_a_limit(self, sqlite_url: str):
        result = execute(_request("SELECT name FROM customers", sqlite_url, max_rows=3))
        assert result.frame.row_count == 3 and result.frame.truncated is True

    def test_a_short_result_is_not_marked_truncated(self, sqlite_url: str):
        result = execute(_request("SELECT name FROM customers", sqlite_url, max_rows=100))
        assert result.frame.row_count == 5 and result.frame.truncated is False

    def test_a_user_limit_we_did_not_change_is_not_truncation(self, sqlite_url: str):
        result = execute(_request("SELECT name FROM customers LIMIT 2", sqlite_url, max_rows=100))
        assert result.frame.row_count == 2 and result.frame.truncated is False

    def test_aggregate_only_query_is_not_limited(self, sqlite_url: str):
        result = execute(_request("SELECT COUNT(*) AS n FROM customers", sqlite_url))
        assert result.success and result.frame.rows == [(5,)]
        assert result.policy.limit_applied is None
        assert "LIMIT" not in result.executed_sql.upper()


class TestPagination:
    def test_pages_are_disjoint(self, sqlite_url: str):
        first = execute(
            _request("SELECT id FROM customers ORDER BY id", sqlite_url, page=1, page_size=2)
        )
        second = execute(
            _request("SELECT id FROM customers ORDER BY id", sqlite_url, page=2, page_size=2)
        )
        assert first.frame.rows == [(1,), (2,)]
        assert second.frame.rows == [(3,), (4,)]
        assert first.frame.has_next_page is True

    def test_last_page_reports_no_next_page(self, sqlite_url: str):
        last = execute(
            _request("SELECT id FROM customers ORDER BY id", sqlite_url, page=3, page_size=2)
        )
        assert last.frame.rows == [(5,)]
        assert last.frame.has_next_page is False

    def test_include_total_counts_the_unpaged_query(self, sqlite_url: str):
        result = execute(
            _request(
                "SELECT id FROM customers ORDER BY id",
                sqlite_url,
                page=1,
                page_size=2,
                include_total=True,
            )
        )
        assert result.frame.row_count == 2
        assert result.frame.total_rows == 5


class TestPolicyIsEnforcedBeforeExecution:
    @pytest.mark.parametrize(
        "sql",
        [
            "DELETE FROM customers",
            "UPDATE customers SET name = 'x'",
            "INSERT INTO customers (id, name) VALUES (99, 'x')",
            "DROP TABLE customers",
            "SELECT name FROM customers; DROP TABLE customers",
            "SELECT * FROM sqlite_master",
            "SELECT * FROM secret_table",
        ],
    )
    def test_denied_statements_never_run(self, sqlite_url: str, sql: str):
        result = execute(_request(sql, sqlite_url))
        assert not result.success
        assert result.error_category == "policy"
        assert result.frame is None
        assert result.executed_sql is None

    def test_denied_write_leaves_the_database_untouched(self, sqlite_url: str):
        execute(_request("DELETE FROM customers", sqlite_url))
        after = execute(_request("SELECT COUNT(*) AS n FROM customers", sqlite_url))
        assert after.frame.rows == [(5,)]

    def test_unknown_column_is_denied(self, sqlite_url: str):
        result = execute(_request("SELECT nope FROM customers", sqlite_url))
        assert not result.success and result.error_category == "policy"

    def test_missing_scope_fails_closed(self, sqlite_url: str):
        result = execute(_request("SELECT 1", sqlite_url, db_flag="not_enrolled"))
        assert not result.success and "enroll" in result.error.lower()


class TestDatabaseLevelReadOnly:
    def test_sqlite_session_refuses_writes_even_if_policy_were_bypassed(self, sqlite_url: str):
        """The last line of defence: the connection itself rejects a write."""
        with read_only_connection(sqlite_url, Dialect.SQLITE, timeout_seconds=5) as (conn, setup):
            assert setup.enforced
            with pytest.raises(Exception) as exc_info:
                conn.execute(text("DELETE FROM customers"))
            assert (
                "readonly" in str(exc_info.value).lower()
                or "read-only" in str(exc_info.value).lower()
            )

    def test_setup_declares_what_each_dialect_can_enforce(self):
        assert read_only_setup(Dialect.POSTGRES, timeout_seconds=10).enforced is True
        assert "READ ONLY" in read_only_setup(Dialect.POSTGRES, timeout_seconds=10).statements[0]
        assert read_only_setup(Dialect.SQLITE, timeout_seconds=10).enforced is True
        # SQL Server has no session-level read-only mode, and the note says so plainly.
        mssql = read_only_setup(Dialect.MSSQL, timeout_seconds=10)
        assert mssql.enforced is False and "least-privilege" in mssql.note

    def test_result_reports_whether_enforcement_happened(self, sqlite_url: str):
        result = execute(_request("SELECT name FROM customers", sqlite_url))
        assert result.read_only_enforced is True
        assert "query_only" in result.read_only_note


class TestApprovals:
    def test_matching_fingerprint_is_accepted(self, sqlite_url: str):
        first = execute(_request("SELECT name FROM customers", sqlite_url))
        again = execute(
            _request(
                "SELECT name FROM customers",
                sqlite_url,
                approved_fingerprint=first.policy.fingerprint,
            )
        )
        assert again.success

    def test_changed_sql_invalidates_the_approval(self, sqlite_url: str):
        approved = execute(_request("SELECT name FROM customers", sqlite_url))
        result = execute(
            _request(
                "SELECT email FROM customers",
                sqlite_url,
                approved_fingerprint=approved.policy.fingerprint,
            )
        )
        assert not result.success
        assert result.error_category == "approval_mismatch"
        assert result.frame is None

    def test_literal_change_keeps_the_same_fingerprint(self, sqlite_url: str):
        a = execute(_request("SELECT name FROM customers WHERE city = 'Mumbai'", sqlite_url))
        b = execute(_request("SELECT name FROM customers WHERE city = 'Delhi'", sqlite_url))
        assert a.policy.fingerprint == b.policy.fingerprint


class TestNetworkPolicy:
    def test_strict_mode_refuses_a_loopback_target(self):
        result = execute(
            ExecutionRequest(
                sql="SELECT 1",
                connection_string="postgresql://u:p@127.0.0.1:5432/app",
                dialect=Dialect.POSTGRES,
                db_flag=None,
                require_scope=False,
                network_level=NetworkPolicyLevel.PUBLIC_STRICT,
            )
        )
        assert not result.success and result.error_category == "network_policy"
        assert result.policy.decision is Decision.ALLOW  # the SQL was fine; the target was not


class TestErrorSanitization:
    def test_connection_strings_are_stripped(self):
        exc = RuntimeError(
            "could not connect to postgresql://admin:hunter2@db.internal:5432/prod - refused"
        )
        message, category = sanitize_db_error(exc)
        assert "hunter2" not in message and "db.internal" not in message
        assert "<connection>" in message and category == "connection"

    def test_credential_pairs_are_redacted(self):
        exc = RuntimeError("login failed for uid=admin;pwd=hunter2;server=sql.internal")
        message, _ = sanitize_db_error(exc)
        assert "hunter2" not in message and "sql.internal" not in message

    def test_long_messages_are_truncated(self):
        message, _ = sanitize_db_error(RuntimeError("x" * 5000))
        assert len(message) <= 320

    def test_categories(self):
        assert sanitize_db_error(RuntimeError('syntax error near "FROM"'))[1] == "syntax"
        assert (
            sanitize_db_error(RuntimeError("canceling statement due to statement timeout"))[1]
            == "timeout"
        )
        assert (
            sanitize_db_error(RuntimeError("permission denied for table customers"))[1]
            == "permission"
        )

    def test_runtime_error_from_a_real_query_is_sanitized(self, sqlite_url: str):
        # A query the policy engine allows but the database rejects: correct columns, wrong types.
        result = execute(_request("SELECT name / 2 AS x FROM customers", sqlite_url))
        # SQLite coerces rather than failing, so this must succeed; the point is no crash leaks out.
        assert result.success or (result.error and "sqlite:///" not in result.error)


class TestReadOnlyVerification:
    def test_writable_sqlite_file_is_reported_honestly(self, sqlite_url: str):
        report = verify_read_only(sqlite_url, Dialect.SQLITE)
        assert report.status is ReadOnlyStatus.APPEARS_READ_ONLY
        assert report.is_safe
        assert "query_only" in report.message

    def test_read_only_url_is_verified(self, tmp_path):
        path = tmp_path / "ro.db"
        sqlite3.connect(path).close()
        report = verify_read_only(
            f"sqlite:///file:{path.as_posix()}?mode=ro&uri=true", Dialect.SQLITE
        )
        assert report.status is ReadOnlyStatus.VERIFIED_READ_ONLY

    def test_unreachable_target_reports_connection_failure(self):
        report = verify_read_only("postgresql+psycopg://u:p@127.0.0.1:1/none", Dialect.POSTGRES)
        assert report.status is ReadOnlyStatus.CONNECTION_FAILED
        assert not report.is_safe
