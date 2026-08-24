"""The verified-pair endpoints, including the review route that closes the drift loop.

Enrollment retires pairs automatically when a schema changes; without a way to put one back, drift
would be a one-way ratchet that empties the flywheel. These tests cover that round trip over HTTP,
and the ownership boundary that stops one caller reviewing another's pairs.
"""

from __future__ import annotations

import pytest

from db import verified_queries as vq
from db.database_manager import create_metadata_tables
from db.verified_queries import VerifiedStatus

DB_FLAG = "demo"
GOOD_SQL = "SELECT city, COUNT(*) AS n FROM customers GROUP BY city"


@pytest.fixture(autouse=True)
def project_db(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path.as_posix()}/pairs.db"
    create_metadata_tables(url)
    monkeypatch.setattr("db.verified_queries.get_project_db_connection_string", lambda: url)
    monkeypatch.setattr("db.verified_queries._embed_pair", lambda *_a, **_k: "embed-1")
    monkeypatch.setattr("db.verified_queries._delete_embedding", lambda *_a, **_k: None)
    return url


@pytest.fixture
def saved(client):
    response = client.post(
        "/training/pairs",
        json={"db_flag": DB_FLAG, "question": "How many customers per city?", "sql": GOOD_SQL},
    )
    assert response.status_code == 200, response.text
    return response.json()


class TestSaving:
    def test_a_saved_pair_returns_its_provenance(self, saved):
        assert saved["status"] == VerifiedStatus.APPROVED.value
        assert saved["tables"] == ["demo.customers"]
        assert saved["dialect"]
        assert len(saved["sql_fingerprint"]) == 64
        assert saved["usage_count"] == 0

    @pytest.mark.parametrize(
        "sql",
        [
            "DELETE FROM customers",
            "UPDATE customers SET city = 'x'",
            "DROP TABLE customers",
            "SELECT * FROM customers; DROP TABLE customers",
        ],
        ids=["delete", "update", "drop", "stacked"],
    )
    def test_non_read_only_sql_is_rejected_with_a_reason(self, client, sql):
        response = client.post(
            "/training/pairs", json={"db_flag": DB_FLAG, "question": "q", "sql": sql}
        )
        assert response.status_code == 400
        assert response.json()["detail"]

    def test_a_pair_referencing_an_unknown_table_is_rejected(self, client):
        response = client.post(
            "/training/pairs",
            json={"db_flag": DB_FLAG, "question": "q", "sql": "SELECT * FROM ghosts"},
        )
        assert response.status_code == 400


class TestListing:
    def test_saved_pairs_are_listed(self, client, saved):
        pairs = client.get("/training/pairs", params={"db_flag": DB_FLAG}).json()["pairs"]
        assert [p["id"] for p in pairs] == [saved["id"]]

    def test_listing_is_scoped_to_the_data_source(self, client, saved):
        pairs = client.get("/training/pairs", params={"db_flag": "some_other_db"}).json()["pairs"]
        assert pairs == []


class TestReview:
    def test_a_stale_pair_can_be_put_back_into_circulation(self, client, saved):
        vq.mark_stale_for_tables(DB_FLAG, ["customers"], "customers.city was renamed")
        listed = client.get("/training/pairs", params={"db_flag": DB_FLAG}).json()["pairs"][0]
        assert listed["status"] == VerifiedStatus.STALE.value
        assert listed["staleness_reason"] == "customers.city was renamed"

        response = client.post(
            f"/training/pairs/{saved['id']}/review",
            json={"status": "approved", "reviewer": "ana@example.com"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == VerifiedStatus.APPROVED.value
        assert body["reviewer"] == "ana@example.com"
        assert body["staleness_reason"] is None
        assert vq.usable_examples(DB_FLAG, None), "it must be offered to the model again"

    def test_rejecting_a_pair_takes_it_out_of_circulation(self, client, saved):
        response = client.post(
            f"/training/pairs/{saved['id']}/review",
            json={"status": "rejected", "reason": "the join is wrong"},
        )
        assert response.status_code == 200
        assert response.json()["staleness_reason"] == "the join is wrong"
        assert vq.usable_examples(DB_FLAG, None) == []

    def test_an_unknown_status_is_refused_by_validation(self, client, saved):
        response = client.post(
            f"/training/pairs/{saved['id']}/review", json={"status": "definitely_fine"}
        )
        assert response.status_code == 422

    def test_reviewing_a_pair_that_does_not_exist_is_a_404(self, client):
        response = client.post("/training/pairs/999999/review", json={"status": "approved"})
        assert response.status_code == 404

    def test_an_overlong_reason_is_refused(self, client, saved):
        response = client.post(
            f"/training/pairs/{saved['id']}/review",
            json={"status": "stale", "reason": "x" * 501},
        )
        assert response.status_code == 422


class TestDeletion:
    def test_a_pair_can_be_deleted(self, client, saved):
        assert client.delete(f"/training/pairs/{saved['id']}").status_code == 200
        assert client.get("/training/pairs", params={"db_flag": DB_FLAG}).json()["pairs"] == []

    def test_deleting_a_pair_that_does_not_exist_is_a_404(self, client):
        assert client.delete("/training/pairs/999999").status_code == 404
