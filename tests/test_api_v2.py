"""The v2 API end to end: a real graph, a real policy engine, a real SQLite database.

Only the model is substituted. Everything else here is the code a deployment runs, which is what
makes these tests worth having - they would catch the wiring being wrong, which is exactly the
defect the v2 audit found when it noticed the new packages had no importers.
"""

from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.api.v2 import deps as v2deps
from db.database_manager import create_metadata_tables, get_session
from db.model import DatabaseConfig

SOURCE = "demo"  # the committed database_schemas/demo schema index describes these tables

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

SQL_ANSWER = json.dumps(
    {"sql": "SELECT city, COUNT(*) AS n FROM customers GROUP BY city", "rationale": "per city"}
)
SUMMARY_ANSWER = json.dumps(
    {"answer": "Mumbai leads with two customers.", "observations": ["Three customers in total."]}
)


@pytest.fixture
def fake_model():
    """The process-wide fake provider, taught how to answer this test's prompts."""
    from app.llm.registry import FAKE_PROFILE
    from app.llm.service import build_provider, reset

    reset()
    provider = build_provider(FAKE_PROFILE)
    provider.add_rule(
        lambda r: r.prompt_name == "understand",
        json.dumps({"intent_type": "aggregation", "requires_clarification": False}),
    )
    provider.add_rule(lambda r: r.prompt_name.startswith("generate_sql"), SQL_ANSWER)
    provider.add_rule(lambda r: r.prompt_name.startswith("repair_sql"), SQL_ANSWER)
    provider.add_rule(lambda r: r.prompt_name == "summarize", SUMMARY_ANSWER)
    yield provider
    reset()


@pytest.fixture
def client(tmp_path, monkeypatch, fake_model):
    target = tmp_path / "demo.db"
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
                db_flag=SOURCE,
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

    for module in (
        "app.user_db_config_loader",
        "app.security.tenancy",
        "app.security.user_auth",
        "app.api.v2.deps",
    ):
        monkeypatch.setattr(
            f"{module}.get_project_db_connection_string", lambda: project_url, raising=False
        )
    monkeypatch.setattr("db.database_manager.get_project_db_connection_string", lambda: project_url)

    # A throwaway checkpointer per test: durable enough to resume, gone afterwards.
    from langgraph.checkpoint.sqlite import SqliteSaver

    context = SqliteSaver.from_conn_string(str(tmp_path / "checkpoints.sqlite"))
    saver = context.__enter__()
    handle = v2deps.CheckpointerHandle(saver=saver, backend="sqlite")
    monkeypatch.setattr(v2deps, "get_checkpointer", lambda: handle)
    v2deps.invalidate_index()

    from app.main import app

    yield TestClient(app)

    context.__exit__(None, None, None)
    v2deps.invalidate_index()


class TestQuery:
    def test_question_to_answer(self, client: TestClient):
        response = client.post(
            "/v2/query", json={"question": "How many customers per city?", "db_flag": SOURCE}
        )
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["status"] == "completed", body.get("error")
        assert body["run_id"].startswith("run-")
        assert body["sql"].startswith("SELECT city")
        assert body["row_count"] == 2
        assert {row[0] for row in body["rows"]} == {"Mumbai", "Delhi"}
        assert body["answer"] == "Mumbai leads with two customers."

    def test_the_response_carries_the_evidence(self, client: TestClient):
        body = client.post(
            "/v2/query", json={"question": "How many customers per city?", "db_flag": SOURCE}
        ).json()

        assert body["evidence"]["schema_grounded"] is True
        assert body["evidence"]["policy_passed"] is True
        assert body["evidence"]["read_only_enforced"] is True
        assert body["policy"]["decision"] == "allow"
        assert len(body["policy"]["fingerprint"]) == 64
        assert body["versions"]["graph"].startswith("query_graph@")
        assert body["versions"]["sql_policy"].startswith("sql_policy@")
        assert body["chart"]["chart"] in {"bar", "table", "horizontal_bar"}
        assert body["retrieval"]["tables"]

    def test_the_trace_lists_every_stage(self, client: TestClient):
        body = client.post(
            "/v2/query", json={"question": "How many customers per city?", "db_flag": SOURCE}
        ).json()
        names = [step["name"] for step in body["steps"]]
        for expected in ("retrieve", "understand", "generate", "validate", "execute", "summarize"):
            assert expected in names, f"{expected} missing from {names}"

    @pytest.mark.parametrize(
        "unsafe_sql",
        [
            "DROP TABLE customers",
            "DELETE FROM customers",
            "UPDATE customers SET city = 'x'",
            "SELECT name FROM sqlite_master",
            "SELECT * FROM customers; DROP TABLE customers",
        ],
    )
    def test_an_unsafe_query_is_refused(
        self, client: TestClient, fake_model, tmp_path, unsafe_sql: str
    ):
        fake_model.add_rule(
            lambda r: r.prompt_name.startswith(("generate_sql", "repair_sql")),
            json.dumps({"sql": unsafe_sql}),
        )
        body = client.post("/v2/query", json={"question": "do it", "db_flag": SOURCE}).json()
        assert body["status"] == "blocked"
        assert body["rows"] == []
        assert "execute" not in [s["name"] for s in body["steps"]]

        # Check the database itself rather than asking the (still-sabotaged) pipeline again.
        connection = sqlite3.connect(tmp_path / "demo.db")
        try:
            assert connection.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 3
        finally:
            connection.close()

    def test_an_unknown_database_is_a_400(self, client: TestClient):
        response = client.post("/v2/query", json={"question": "anything", "db_flag": "nope"})
        assert response.status_code == 400
        assert "Enroll" in response.json()["detail"]

    def test_a_supplied_run_id_is_used(self, client: TestClient):
        body = client.post(
            "/v2/query",
            json={"question": "How many customers per city?", "db_flag": SOURCE, "run_id": "mine"},
        ).json()
        assert body["run_id"] == "mine"


class TestApprovalFlow:
    def _pause(self, client: TestClient) -> dict:
        return client.post(
            "/v2/query",
            json={
                "question": "How many customers per city?",
                "db_flag": SOURCE,
                "plan_only": True,
                "run_id": "approve-me",
            },
        ).json()

    def test_plan_only_pauses_with_the_sql_to_review(self, client: TestClient):
        body = self._pause(client)
        assert body["status"] == "awaiting_input"
        assert body["awaiting"]["type"] == "approval"
        assert body["awaiting"]["sql"].startswith("SELECT city")
        assert body["awaiting"]["fingerprint"]
        assert body["rows"] == []

    def test_approving_completes_the_run(self, client: TestClient):
        self._pause(client)
        body = client.post("/v2/runs/approve-me/resume", json={"approved": True}).json()
        assert body["status"] == "completed"
        assert body["row_count"] == 2
        assert body["evidence"]["human_approved"] is True

    def test_rejecting_blocks_it(self, client: TestClient):
        self._pause(client)
        body = client.post("/v2/runs/approve-me/resume", json={"approved": False}).json()
        assert body["status"] == "blocked"
        assert "not approved" in body["error"]

    def test_edited_sql_is_revalidated(self, client: TestClient):
        self._pause(client)
        body = client.post(
            "/v2/runs/approve-me/resume",
            json={"approved": True, "sql": "SELECT name FROM customers"},
        ).json()
        assert body["status"] == "completed"
        assert body["sql"] == "SELECT name FROM customers"
        assert [s["name"] for s in body["steps"]].count("validate") == 2

    def test_edited_sql_that_is_unsafe_is_still_refused(self, client: TestClient):
        self._pause(client)
        body = client.post(
            "/v2/runs/approve-me/resume",
            json={"approved": True, "sql": "DELETE FROM customers"},
        ).json()
        assert body["status"] == "blocked"
        assert body["rows"] == []

    def test_resuming_a_finished_run_is_a_conflict(self, client: TestClient):
        client.post(
            "/v2/query",
            json={"question": "How many customers per city?", "db_flag": SOURCE, "run_id": "done"},
        )
        response = client.post("/v2/runs/done/resume", json={"approved": True})
        assert response.status_code == 409

    def test_resume_needs_a_decision(self, client: TestClient):
        self._pause(client)
        response = client.post("/v2/runs/approve-me/resume", json={})
        assert response.status_code == 400


class TestClarificationFlow:
    def test_pause_and_answer(self, client: TestClient, fake_model):
        fake_model.add_rule(
            lambda r: r.prompt_name == "understand",
            json.dumps(
                {
                    "intent_type": "aggregation",
                    "requires_clarification": True,
                    "clarification_question": "By billing city or shipping city?",
                }
            ),
        )
        paused = client.post(
            "/v2/query",
            json={"question": "customers per city", "db_flag": SOURCE, "run_id": "ask-me"},
        ).json()
        assert paused["status"] == "awaiting_input"
        assert paused["awaiting"]["type"] == "clarification"
        assert "billing city" in paused["awaiting"]["question"]

        resumed = client.post("/v2/runs/ask-me/resume", json={"answer": "billing city"}).json()
        assert resumed["status"] == "completed"
        assert resumed["row_count"] == 2


class TestRunRetrieval:
    def test_a_finished_run_can_be_read_back(self, client: TestClient):
        client.post(
            "/v2/query",
            json={"question": "How many customers per city?", "db_flag": SOURCE, "run_id": "keep"},
        )
        body = client.get("/v2/runs/keep").json()
        assert body["status"] == "completed"
        assert body["sql"].startswith("SELECT city")
        assert body["steps"]

    def test_a_paused_run_reports_what_it_is_waiting_for(self, client: TestClient):
        client.post(
            "/v2/query",
            json={
                "question": "How many customers per city?",
                "db_flag": SOURCE,
                "plan_only": True,
                "run_id": "paused",
            },
        )
        body = client.get("/v2/runs/paused").json()
        assert body["status"] == "awaiting_input"
        assert body["awaiting"]["type"] == "approval"

    def test_an_unknown_run_is_a_404(self, client: TestClient):
        assert client.get("/v2/runs/never-existed").status_code == 404


class TestModelHealth:
    def test_reports_what_is_usable_and_why_not(self, client: TestClient):
        body = client.get("/v2/models/health").json()
        assert body["enabled_profiles"] == ["fake"]
        assert body["registry_version"].startswith("models@")
        assert body["mode"] in {"demo", "self_hosted", "production"}
        assert "fake" in body["providers"]
        assert body["providers"]["fake"]["available"] is True


class TestNoSecretsLeak:
    def test_the_response_never_carries_a_connection_string(self, client: TestClient):
        body = client.post(
            "/v2/query", json={"question": "How many customers per city?", "db_flag": SOURCE}
        ).text
        assert "sqlite:///" not in body
