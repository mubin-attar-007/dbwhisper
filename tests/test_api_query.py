"""API-level tests for /query and /run_sql against a real SQLite target database.

The model is replaced by a deterministic stub, so what is under test is the pipeline the API
actually runs: policy engine, network policy, read-only execution, result formatting and the
metadata the response now carries. No network, no LLM key, no Postgres.
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from db.database_manager import create_metadata_tables, get_session
from db.model import DatabaseConfig

DDL = """
CREATE TABLE customers (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT, city TEXT, signup_date TEXT
);
CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT, category TEXT, price REAL);
CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER, order_date TEXT, status TEXT);
CREATE TABLE order_items (
    id INTEGER PRIMARY KEY, order_id INTEGER, product_id INTEGER, quantity INTEGER, unit_price REAL
);
INSERT INTO customers VALUES
    (1, 'Ava Sharma', 'ava@example.com', 'Mumbai', '2025-01-12'),
    (2, 'Liam Patel', 'liam@example.com', 'Delhi', '2025-02-03'),
    (3, 'Noah Khan', 'noah@example.com', 'Mumbai', '2025-02-20');
INSERT INTO products VALUES (1, 'Mouse', 'Electronics', 19.99), (2, 'Pen', 'Stationery', 1.5);
"""


@pytest.fixture
def api(tmp_path, monkeypatch):
    """A TestClient whose 'demo' database points at a throwaway SQLite file."""
    target = tmp_path / "target.db"
    connection = sqlite3.connect(target)
    try:
        connection.executescript(DDL)
        connection.commit()
    finally:
        connection.close()

    project_url = f"sqlite:///{(tmp_path / 'project.db').as_posix()}"
    create_metadata_tables(project_url)
    session = get_session(project_url)
    try:
        session.add(
            DatabaseConfig(
                db_flag="demo",
                db_type="sqlite",
                connection_string=f"sqlite:///{target.as_posix()}",
                description="Bundled retail demo",
                max_rows=100,
                query_timeout=10,
                owner_id=None,
            )
        )
        session.commit()
    finally:
        session.close()

    for module in ("app.user_db_config_loader", "app.security.tenancy", "app.security.user_auth"):
        monkeypatch.setattr(f"{module}.get_project_db_connection_string", lambda: project_url)

    from app.execution.connections import get_engine
    from app.main import app

    yield TestClient(app)
    get_engine.cache_clear()


def _stub_agent(monkeypatch, sql: str, follow_ups: list[str] | None = None) -> None:
    """Replace the model with a fixed answer so the pipeline, not the LLM, is under test."""
    from app.agent.chain import LLMResponse

    class _Agent:
        def invoke(self, _payload):
            return {
                "structured_response": LLMResponse(
                    sql_query=sql, follow_up_questions=follow_ups or []
                )
            }

    monkeypatch.setattr("app.main.get_available_providers", lambda: ["fake"])
    monkeypatch.setattr("app.main.get_cached_agent", lambda *a, **k: _Agent())
    monkeypatch.setattr("app.main.get_cached_agent_with_context", lambda *a, **k: _Agent())
    monkeypatch.setattr("app.main.get_collected_tables", lambda: ["customers"])
    monkeypatch.setattr("app.main.summarize_query_results", lambda *a, **k: "Three customers.")


class TestRunSql:
    def test_select_returns_rows_and_metadata(self, api: TestClient):
        response = api.post(
            "/run_sql",
            json={"sql": "SELECT name, city FROM customers ORDER BY id", "db_flag": "demo"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "success"
        assert body["validation_passed"] is True
        assert [row["name"] for row in body["data"]["results"]] == [
            "Ava Sharma",
            "Liam Patel",
            "Noah Khan",
        ]
        assert body["data"]["row_count"] == 3
        assert body["data"]["csv"].startswith("name,city")
        meta = body["metadata"]
        assert meta["policy_decision"] == "allow"
        assert meta["policy_version"].startswith("sql_policy@")
        assert len(meta["sql_fingerprint"]) == 64
        assert meta["tables_used"] == ["demo.customers"]
        assert meta["read_only_enforced"] is True

    @pytest.mark.parametrize(
        "sql",
        [
            "DELETE FROM customers",
            "UPDATE customers SET name = 'x'",
            "DROP TABLE customers",
            "SELECT * FROM sqlite_master",
            "SELECT * FROM other_table",
            "SELECT 1; DROP TABLE customers",
        ],
    )
    def test_unsafe_sql_is_refused_without_touching_the_database(self, api: TestClient, sql: str):
        response = api.post("/run_sql", json={"sql": sql, "db_flag": "demo"})
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "error"
        assert body["validation_passed"] is False
        assert body["data"] is None
        assert body["metadata"]["error_category"] == "policy"

        still_there = api.post(
            "/run_sql", json={"sql": "SELECT COUNT(*) AS n FROM customers", "db_flag": "demo"}
        ).json()
        assert still_there["data"]["results"][0]["n"] == 3

    def test_csv_output_format(self, api: TestClient):
        body = api.post(
            "/run_sql",
            json={
                "sql": "SELECT name FROM customers ORDER BY id",
                "db_flag": "demo",
                "output_format": "csv",
            },
        ).json()
        assert body["data"]["results"].splitlines()[0] == "name"
        assert "Ava Sharma" in body["data"]["results"]

    def test_pagination(self, api: TestClient):
        body = api.post(
            "/run_sql",
            json={
                "sql": "SELECT id FROM customers ORDER BY id",
                "db_flag": "demo",
                "page": 2,
                "page_size": 1,
                "include_total": True,
            },
        ).json()
        assert body["data"]["results"] == [{"id": 2}]
        assert body["data"]["page"] == 2 and body["data"]["total_rows"] == 3
        assert body["data"]["has_next"] is True

    def test_unknown_database_is_a_400(self, api: TestClient):
        response = api.post("/run_sql", json={"sql": "SELECT 1", "db_flag": "nope"})
        assert response.status_code == 400


class TestQuery:
    def test_end_to_end_with_a_stubbed_model(self, api: TestClient, monkeypatch):
        _stub_agent(monkeypatch, "SELECT city, COUNT(*) AS n FROM customers GROUP BY city")
        body = api.post(
            "/query", json={"query": "How many customers per city?", "db_flag": "demo"}
        ).json()
        assert body["status"] == "success", body.get("error")
        assert body["sql"].startswith("SELECT city")
        assert {row["city"] for row in body["data"]["results"]} == {"Mumbai", "Delhi"}
        assert body["natural_summary"] == "Three customers."
        assert body["selected_tables"] == ["customers"]
        assert body["metadata"]["policy_decision"] == "allow"
        assert body["data"]["describe"]["city"]["kind"] == "text"

    def test_a_model_that_emits_a_write_is_refused(self, api: TestClient, monkeypatch):
        _stub_agent(monkeypatch, "DELETE FROM customers")
        body = api.post("/query", json={"query": "remove everyone", "db_flag": "demo"}).json()
        assert body["status"] == "error"
        assert body["validation_passed"] is False
        assert body["metadata"]["error_category"] == "policy"
        assert body["data"] is None

    def test_a_model_that_hallucinates_a_table_is_refused(self, api: TestClient, monkeypatch):
        _stub_agent(monkeypatch, "SELECT * FROM invoices")
        body = api.post("/query", json={"query": "show invoices", "db_flag": "demo"}).json()
        assert body["status"] == "error" and body["validation_passed"] is False
        assert "invoices" in body["error"]

    def test_follow_up_questions_are_passed_through(self, api: TestClient, monkeypatch):
        _stub_agent(monkeypatch, "SELECT name FROM customers", ["Break down by city?"])
        body = api.post("/query", json={"query": "list customers", "db_flag": "demo"}).json()
        assert body["follow_up_questions"] == ["Break down by city?"]

    def test_row_cap_is_reported_as_truncated(self, api: TestClient, monkeypatch):
        _stub_agent(monkeypatch, "SELECT name FROM customers")
        session = get_session
        # Lower the cap for this database so three rows overflow it.
        from app.user_db_config_loader import get_project_db_connection_string

        db = session(get_project_db_connection_string())
        try:
            row = db.query(DatabaseConfig).filter_by(db_flag="demo").first()
            row.max_rows = 2
            db.commit()
        finally:
            db.close()

        body = api.post("/query", json={"query": "list customers", "db_flag": "demo"}).json()
        assert body["data"]["row_count"] == 2
        assert body["data"]["truncated"] is True
        assert body["metadata"]["truncated"] is True
