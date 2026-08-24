"""Schema drift: noticing that a verified query's schema moved underneath it.

The case that matters is the quiet one. Nobody deletes a table by accident, but columns get renamed
and retyped constantly, and a verified pair approved against the old shape keeps being offered to the
model as ground truth long after it stopped being true.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.schema_pipeline import drift


def write_table(root: Path, source: str, schema: str, table: str, columns: list[dict]) -> Path:
    directory = root / source / "schema" / schema
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{table}.yaml"
    path.write_text(
        yaml.safe_dump({"table_name": table, "schema": schema, "columns": columns}),
        encoding="utf-8",
    )
    return path


COLUMNS = [
    {"name": "id", "type": "integer", "is_nullable": False, "description": ""},
    {"name": "city", "type": "text", "is_nullable": True, "description": ""},
]


@pytest.fixture
def root(tmp_path) -> Path:
    return tmp_path


@pytest.fixture
def rooted(root, monkeypatch) -> Path:
    """Point the drift module at a temporary artefact tree."""
    monkeypatch.setattr(drift, "schema_dir", lambda s, r=None: root / s / "schema")
    return root


class TestFingerprinting:
    def test_an_unextracted_source_has_no_fingerprints(self, root):
        assert drift.fingerprint_schema("nothing-here", root=root) == {}

    def test_tables_are_keyed_by_qualified_name(self, root):
        write_table(root, "demo", "demo", "customers", COLUMNS)
        assert list(drift.fingerprint_schema("demo", root=root)) == ["demo.customers"]

    def test_the_index_file_is_not_mistaken_for_a_table(self, root):
        write_table(root, "demo", "demo", "customers", COLUMNS)
        index = root / "demo" / "schema" / "schema_index.yaml"
        index.write_text(yaml.safe_dump({"columns": ["not a table"]}), encoding="utf-8")
        assert list(drift.fingerprint_schema("demo", root=root)) == ["demo.customers"]

    def test_an_unreadable_artefact_is_skipped_rather_than_fatal(self, root):
        write_table(root, "demo", "demo", "customers", COLUMNS)
        (root / "demo" / "schema" / "demo" / "broken.yaml").write_text("{[bad", encoding="utf-8")
        assert list(drift.fingerprint_schema("demo", root=root)) == ["demo.customers"]

    def test_a_description_edit_is_not_drift(self, root):
        write_table(root, "demo", "demo", "customers", COLUMNS)
        before = drift.fingerprint_schema("demo", root=root)

        described = [dict(c, description="now documented") for c in COLUMNS]
        write_table(root, "demo", "demo", "customers", described)
        assert drift.fingerprint_schema("demo", root=root) == before

    def test_reordering_columns_is_not_drift(self, root):
        write_table(root, "demo", "demo", "customers", COLUMNS)
        before = drift.fingerprint_schema("demo", root=root)
        write_table(root, "demo", "demo", "customers", list(reversed(COLUMNS)))
        assert drift.fingerprint_schema("demo", root=root) == before

    @pytest.mark.parametrize(
        "mutation",
        [{"name": "town"}, {"type": "integer"}, {"is_nullable": False}],
        ids=["renamed", "retyped", "nullability"],
    )
    def test_a_semantic_column_change_is_drift(self, root, mutation):
        write_table(root, "demo", "demo", "customers", COLUMNS)
        before = drift.fingerprint_schema("demo", root=root)

        write_table(root, "demo", "demo", "customers", [COLUMNS[0], {**COLUMNS[1], **mutation}])
        assert drift.fingerprint_schema("demo", root=root) != before


class TestDiff:
    def test_a_first_enrollment_is_never_drift(self):
        report = drift.diff({}, {"demo.customers": "abc"})
        assert not report.has_drift, "day one must not mark every pair stale"
        assert report.affected_tables == []

    def test_a_changed_table_is_affected(self):
        report = drift.diff({"demo.customers": "aaa"}, {"demo.customers": "bbb"})
        assert report.changed == ["demo.customers"]
        assert report.affected_tables == ["demo.customers"]

    def test_a_dropped_table_is_affected(self):
        report = drift.diff({"demo.customers": "aaa"}, {})
        assert report.removed == ["demo.customers"]
        assert report.affected_tables == ["demo.customers"]

    def test_a_new_table_is_reported_but_invalidates_nothing(self):
        report = drift.diff({"demo.customers": "a"}, {"demo.customers": "a", "demo.orders": "b"})
        assert report.added == ["demo.orders"]
        assert report.has_drift
        assert report.affected_tables == [], "an added table cannot break an existing query"

    def test_an_unchanged_schema_reports_nothing(self):
        same = {"demo.customers": "aaa"}
        report = drift.diff(same, dict(same))
        assert not report.has_drift
        assert report.describe() == "no schema change detected"

    def test_the_description_names_what_moved(self):
        report = drift.diff({"a": "1", "b": "2"}, {"a": "9"})
        described = report.describe()
        assert "1 changed" in described and "1 removed" in described


class TestRetiringVerifiedPairs:
    def test_nothing_affected_means_no_database_call(self, monkeypatch):
        def boom(*_a, **_k):
            pytest.fail("no drift must not touch the database")

        monkeypatch.setattr("db.verified_queries.mark_stale_for_tables", boom)
        assert drift.apply_to_verified_queries("demo", drift.DriftReport()) == 0

    def test_affected_tables_are_passed_through_with_a_reason(self, monkeypatch):
        seen: dict = {}

        def capture(source_id, tables, reason):
            seen.update(source_id=source_id, tables=tables, reason=reason)
            return len(tables)

        monkeypatch.setattr("db.verified_queries.mark_stale_for_tables", capture)
        report = drift.DriftReport(changed=["demo.customers"], removed=["demo.orders"])

        assert drift.apply_to_verified_queries("demo", report) == 2
        assert seen["source_id"] == "demo"
        assert seen["tables"] == ["demo.customers", "demo.orders"]
        assert "Schema changed during enrollment" in seen["reason"]


class TestEnrollmentStages:
    """The stages as the job runner sees them, without running a real enrollment."""

    def test_the_snapshot_stage_reads_what_is_already_on_disk(self, rooted):
        from app.jobs import handlers

        write_table(rooted, "demo", "demo", "customers", COLUMNS)
        output = handlers._snapshot_schema({"source_id": "demo"}, {})
        assert list(output["fingerprints"]) == ["demo.customers"]
        assert "1 table(s) previously known" in output["detail"]

    def test_drift_between_the_two_stages_retires_pairs(self, rooted, monkeypatch):
        from app.jobs import handlers

        write_table(rooted, "demo", "demo", "customers", COLUMNS)
        snapshot = handlers._snapshot_schema({"source_id": "demo"}, {})

        # Extraction happens here: a column is renamed.
        write_table(rooted, "demo", "demo", "customers", [COLUMNS[0], {**COLUMNS[1], "name": "t"}])

        retired: list = []
        monkeypatch.setattr(
            "db.verified_queries.mark_stale_for_tables",
            lambda s, t, r: (retired.append((s, t, r)), 1)[1],
        )

        output = handlers._detect_drift({"source_id": "demo"}, {"snapshot_schema": snapshot})
        assert output["changed"] == ["demo.customers"]
        assert output["verified_pairs_retired"] == 1
        assert retired[0][1] == ["demo.customers"]

    def test_a_failure_to_retire_does_not_fail_the_enrollment(self, rooted, monkeypatch):
        from app.jobs import handlers

        write_table(rooted, "demo", "demo", "customers", COLUMNS)
        snapshot = handlers._snapshot_schema({"source_id": "demo"}, {})
        write_table(rooted, "demo", "demo", "customers", [COLUMNS[0]])

        def explode(*_a, **_k):
            raise RuntimeError("the project database is down")

        monkeypatch.setattr("db.verified_queries.mark_stale_for_tables", explode)

        output = handlers._detect_drift({"source_id": "demo"}, {"snapshot_schema": snapshot})
        assert output["verified_pairs_retired"] == 0
        assert output["changed"] == ["demo.customers"]

    def test_a_first_enrollment_retires_nothing(self, rooted, monkeypatch):
        from app.jobs import handlers

        snapshot = handlers._snapshot_schema({"source_id": "demo"}, {})
        write_table(rooted, "demo", "demo", "customers", COLUMNS)

        monkeypatch.setattr(
            "db.verified_queries.mark_stale_for_tables",
            lambda *_a, **_k: pytest.fail("a first enrollment must not retire anything"),
        )
        output = handlers._detect_drift({"source_id": "demo"}, {"snapshot_schema": snapshot})
        assert output["verified_pairs_retired"] == 0
