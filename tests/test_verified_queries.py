"""The verified-query flywheel: approval, staleness on schema drift, and export to evaluation.

The behaviour under test is the one that was missing: a pair approved against one schema must stop
being offered as a verified example when that schema changes. A "verified" example that is quietly
wrong is worse than no example at all.
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
    url = f"sqlite:///{tmp_path.as_posix()}/verified.db"
    create_metadata_tables(url)
    monkeypatch.setattr("db.verified_queries.get_project_db_connection_string", lambda: url)
    # The vector store needs Postgres; embedding is best-effort, so make it a no-op here.
    monkeypatch.setattr("db.verified_queries._embed_pair", lambda *_a, **_k: "embed-1")
    monkeypatch.setattr("db.verified_queries._delete_embedding", lambda *_a, **_k: None)
    return url


class TestSaving:
    def test_an_approved_pair_records_its_provenance(self):
        pair = vq.save_verified_query(
            DB_FLAG,
            "How many customers per city?",
            GOOD_SQL,
            owner_id=None,
            reviewer="ana@example.com",
            snapshot_id="snap-1",
            prompt_version="generate_sql@2.0",
            model_profile="local-small",
        )
        assert pair["status"] == VerifiedStatus.APPROVED.value
        assert len(pair["sql_fingerprint"]) == 64
        assert pair["tables"] == ["demo.customers"]
        assert pair["reviewer"] == "ana@example.com"
        assert pair["snapshot_id"] == "snap-1"
        assert pair["prompt_version"] == "generate_sql@2.0"
        assert pair["reviewed_at"]

    def test_non_read_only_sql_is_refused(self):
        with pytest.raises(ValueError, match="not read-only"):
            vq.save_verified_query(DB_FLAG, "delete them", "DELETE FROM customers", owner_id=None)

    def test_sql_referencing_an_unknown_table_is_refused(self):
        with pytest.raises(ValueError):
            vq.save_verified_query(DB_FLAG, "q", "SELECT * FROM ghosts", owner_id=None)

    def test_blank_input_is_refused(self):
        with pytest.raises(ValueError, match="required"):
            vq.save_verified_query(DB_FLAG, "  ", GOOD_SQL, owner_id=None)

    def test_two_pairs_differing_only_in_a_literal_share_a_fingerprint(self):
        a = vq.save_verified_query(
            DB_FLAG, "Mumbai?", "SELECT name FROM customers WHERE city = 'Mumbai'", owner_id=None
        )
        b = vq.save_verified_query(
            DB_FLAG, "Delhi?", "SELECT name FROM customers WHERE city = 'Delhi'", owner_id=None
        )
        assert a["sql_fingerprint"] == b["sql_fingerprint"]


class TestLifecycle:
    def _pair(self, **kwargs):
        return vq.save_verified_query(DB_FLAG, "q", GOOD_SQL, owner_id=None, **kwargs)

    def test_only_approved_pairs_are_offered_to_the_model(self):
        approved = self._pair()
        draft = self._pair(status=VerifiedStatus.DRAFT)
        usable = {p["id"] for p in vq.usable_examples(DB_FLAG, None)}
        assert approved["id"] in usable
        assert draft["id"] not in usable

    def test_status_changes_are_recorded_with_a_reason(self):
        pair = self._pair()
        updated = vq.set_status(
            pair["id"],
            None,
            VerifiedStatus.NEEDS_REVIEW,
            reason="column renamed",
            reviewer="bo@x.io",
        )
        assert updated["status"] == VerifiedStatus.NEEDS_REVIEW.value
        assert updated["staleness_reason"] == "column renamed"
        assert updated["reviewer"] == "bo@x.io"
        assert vq.usable_examples(DB_FLAG, None) == []

    def test_a_pair_belonging_to_someone_else_cannot_be_changed(self):
        pair = vq.save_verified_query(DB_FLAG, "q", GOOD_SQL, owner_id=7)
        assert vq.set_status(pair["id"], 8, VerifiedStatus.REJECTED) is None
        assert vq.delete_verified_query(pair["id"], 8) is False
        assert vq.delete_verified_query(pair["id"], 7) is True

    def test_usage_is_counted(self):
        pair = self._pair()
        vq.record_use(pair["id"])
        vq.record_use(pair["id"])
        assert vq.list_verified_queries(DB_FLAG, None)[0]["usage_count"] == 2


class TestDrift:
    def test_a_pair_touching_a_changed_table_goes_stale(self):
        pair = vq.save_verified_query(DB_FLAG, "q", GOOD_SQL, owner_id=None)
        affected = vq.mark_stale_for_tables(DB_FLAG, ["customers"], "customers.city was renamed")
        assert affected == 1

        listed = vq.list_verified_queries(DB_FLAG, None)[0]
        assert listed["status"] == VerifiedStatus.STALE.value
        assert listed["staleness_reason"] == "customers.city was renamed"
        assert vq.usable_examples(DB_FLAG, None) == []
        assert listed["id"] == pair["id"]

    def test_a_pair_touching_an_unrelated_table_is_untouched(self):
        vq.save_verified_query(DB_FLAG, "q", GOOD_SQL, owner_id=None)
        assert vq.mark_stale_for_tables(DB_FLAG, ["products"], "products changed") == 0
        assert vq.usable_examples(DB_FLAG, None)

    def test_a_schema_qualified_change_still_matches(self):
        vq.save_verified_query(DB_FLAG, "q", GOOD_SQL, owner_id=None)
        assert vq.mark_stale_for_tables(DB_FLAG, ["demo.customers"], "changed") == 1

    def test_a_pair_with_no_recorded_tables_is_marked_conservatively(self, project_db):
        """We cannot prove it is unaffected, so it is flagged rather than left in circulation."""
        from db.database_manager import get_session
        from db.model import VerifiedQuery

        vq.save_verified_query(DB_FLAG, "q", GOOD_SQL, owner_id=None)
        session = get_session(project_db)
        try:
            row = session.query(VerifiedQuery).first()
            row.tables = None  # a pair saved before tables were recorded
            session.commit()
        finally:
            session.close()

        assert vq.mark_stale_for_tables(DB_FLAG, ["something_else"], "unknown impact") == 1

    def test_nothing_changed_means_nothing_marked(self):
        vq.save_verified_query(DB_FLAG, "q", GOOD_SQL, owner_id=None)
        assert vq.mark_stale_for_tables(DB_FLAG, [], "no-op") == 0
        assert vq.usable_examples(DB_FLAG, None)


class TestEvalExport:
    def test_approved_pairs_become_evaluation_cases(self):
        vq.save_verified_query(
            DB_FLAG, "How many customers per city?", GOOD_SQL, owner_id=None, reviewer="ana@x.io"
        )
        vq.save_verified_query(DB_FLAG, "draft one", GOOD_SQL, owner_id=None, status="draft")

        cases = vq.export_as_eval_cases(DB_FLAG, None)
        assert len(cases) == 1, "only approved pairs are regression-worthy"
        case = cases[0]
        assert case["question"] == "How many customers per city?"
        assert case["gold_sql"] == GOOD_SQL
        assert case["expected_tables"] == ["demo.customers"]
        assert case["dataset"] == "verified:demo"
        assert "ana@x.io" in case["notes"]

    def test_export_is_empty_when_nothing_is_approved(self):
        assert vq.export_as_eval_cases(DB_FLAG, None) == []


class TestReinstatement:
    """Drift retires a pair; a human puts it back. Retrieval has to follow in both directions."""

    def test_a_retired_pair_loses_its_embedding_and_a_restored_one_regains_it(self):
        pair = vq.save_verified_query(DB_FLAG, "q", GOOD_SQL, owner_id=None)
        assert pair["status"] == VerifiedStatus.APPROVED.value

        vq.mark_stale_for_tables(DB_FLAG, ["customers"], "customers.city was renamed")
        stale = vq.list_verified_queries(DB_FLAG, None)[0]
        assert stale["status"] == VerifiedStatus.STALE.value
        assert vq.usable_examples(DB_FLAG, None) == []

        restored = vq.set_status(
            pair["id"], None, VerifiedStatus.APPROVED, reviewer="ana@x.io", reason=None
        )
        assert restored["status"] == VerifiedStatus.APPROVED.value
        assert restored["staleness_reason"] is None
        assert [p["id"] for p in vq.usable_examples(DB_FLAG, None)] == [pair["id"]]

    def test_reinstating_re_embeds_rather_than_only_relabelling(self, monkeypatch):
        embedded: list[str] = []
        monkeypatch.setattr(
            "db.verified_queries._embed_pair",
            lambda flag, question, sql: embedded.append(question) or "embed-2",
        )
        deleted: list[str] = []
        monkeypatch.setattr(
            "db.verified_queries._delete_embedding",
            lambda flag, eid: deleted.append(eid),
        )

        pair = vq.save_verified_query(DB_FLAG, "how many?", GOOD_SQL, owner_id=None)
        embedded.clear()

        vq.set_status(pair["id"], None, VerifiedStatus.STALE, reason="drift")
        assert deleted == ["embed-2"], "a retired pair must leave the index"
        assert embedded == []

        vq.set_status(pair["id"], None, VerifiedStatus.APPROVED)
        assert embedded == ["how many?"], "a reinstated pair must return to the index"

    def test_an_already_approved_pair_is_not_re_embedded_needlessly(self, monkeypatch):
        pair = vq.save_verified_query(DB_FLAG, "q", GOOD_SQL, owner_id=None)
        monkeypatch.setattr(
            "db.verified_queries._embed_pair",
            lambda *_a, **_k: pytest.fail("it is already in the index"),
        )
        assert vq.set_status(pair["id"], None, VerifiedStatus.APPROVED) is not None
