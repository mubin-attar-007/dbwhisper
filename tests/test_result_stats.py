"""Deterministic result statistics - the numbers a grounded summary is allowed to cite."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from app.execution.results import ResultFrame, compute_stats


def frame(columns: list[str], rows: list[tuple], **kwargs) -> ResultFrame:
    return ResultFrame(columns=columns, rows=rows, **kwargs)


class TestClassification:
    def test_numeric_measure(self):
        stats = compute_stats(frame(["revenue"], [(10.0,), (20.0,), (30.5,), (7.25,), (99.0,)]))
        revenue = stats.by_name("revenue")
        assert revenue.kind == "numeric"
        assert revenue.minimum == 7.25 and revenue.maximum == 99.0
        assert revenue.mean == 33.35
        assert revenue.median == 20.0
        assert revenue.total == 166.75

    def test_identifier_by_name_is_not_averaged(self):
        stats = compute_stats(frame(["customer_id"], [(101,), (102,), (103,)]))
        column = stats.by_name("customer_id")
        assert column.kind == "identifier"
        assert column.mean is None and column.total is None
        assert "aggregates are not meaningful" in column.note or "distinct" in column.note

    def test_identifier_by_shape_is_not_averaged(self):
        # No id-ish name, but every value distinct across enough rows: a surrogate key.
        stats = compute_stats(frame(["ref"], [(1,), (2,), (3,), (4,), (5,)]))
        assert stats.by_name("ref").kind == "identifier"
        assert stats.by_name("ref").mean is None

    def test_low_cardinality_numeric_is_categorical(self):
        rows = [(1,), (2,), (1,), (3,), (2,), (1,), (2,), (3,)]
        stats = compute_stats(frame(["star_rating"], rows))
        column = stats.by_name("star_rating")
        assert column.kind == "numeric"
        assert column.mean is None, "a rating code must not be presented as an average"
        assert column.top_values[0] == (1, 3) or column.top_values[0] == (2, 3)
        assert "categorical" in column.note

    def test_temporal_range(self):
        rows = [(date(2025, 1, 5),), (date(2025, 3, 9),), (date(2024, 12, 1),)]
        column = compute_stats(frame(["order_date"], rows)).by_name("order_date")
        assert column.kind == "temporal"
        assert column.minimum == date(2024, 12, 1) and column.maximum == date(2025, 3, 9)

    def test_boolean_counts(self):
        column = compute_stats(frame(["active"], [(True,), (False,), (True,)])).by_name("active")
        assert column.kind == "boolean"
        assert dict(column.top_values) == {True: 2, False: 1}

    def test_text_reports_lengths_not_values(self):
        column = compute_stats(frame(["city"], [("Pune",), ("Mumbai",), ("Delhi",)])).by_name(
            "city"
        )
        assert column.kind == "text"
        assert column.minimum == 4 and column.maximum == 6
        assert "string lengths" in column.note

    def test_decimal_is_numeric(self):
        rows = [(Decimal("10.5"),), (Decimal("21.25"),), (Decimal("3.0"),), (Decimal("88.1"),)]
        column = compute_stats(frame(["price"], rows)).by_name("price")
        assert column.kind == "numeric" and column.total == 122.85


class TestNullsAndEmptiness:
    def test_null_counts_and_fraction(self):
        column = compute_stats(
            frame(["email"], [("a@b.c",), (None,), (None,), ("d@e.f",)])
        ).by_name("email")
        assert column.non_null == 2 and column.nulls == 2
        assert column.null_fraction == 0.5

    def test_all_null_column(self):
        column = compute_stats(frame(["note"], [(None,), (None,)])).by_name("note")
        assert column.non_null == 0 and column.nulls == 2 and column.kind == "other"

    def test_empty_frame(self):
        stats = compute_stats(frame(["a", "b"], []))
        assert stats.row_count == 0 and stats.column_count == 2
        assert all(c.non_null == 0 for c in stats.columns)


class TestRendering:
    def test_describe_text_is_compact_and_mentions_truncation(self):
        stats = compute_stats(frame(["revenue"], [(1.0,), (2.0,)], truncated=True))
        text = stats.describe_text()
        assert "truncated=true" in text
        assert "revenue [numeric]" in text
        assert len(text.splitlines()) == 2

    def test_as_dict_is_json_safe(self):
        import json

        rows = [(datetime(2025, 1, 1, 12, 0), Decimal("1.5"), "x")]
        stats = compute_stats(frame(["ts", "amount", "label"], rows))
        json.dumps(stats.as_dict())  # must not raise

    def test_stats_carry_truncation_from_the_frame(self):
        assert compute_stats(frame(["a"], [(1,)], truncated=True)).truncated is True
        assert compute_stats(frame(["a"], [(1,)])).truncated is False
