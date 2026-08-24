"""Metric behaviour, with the two comparisons the v1 harness got wrong pinned down first.

``docs/v2/CLAIM_AUDIT.md`` §4.4(a)(4) records that ``eval/harness.py:35-41`` compared result sets as
a ``frozenset``. Two failure modes follow from that, and the first two tests here are the regression
guards for exactly those: a duplicate-row difference must not compare equal, and a wrong ordering
must not compare equal when the reference query asked for an order.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.evaluation.metrics import (
    LatencySummary,
    MeanStat,
    Ratio,
    compare_result_sets,
    normalize_value,
    recall_at_k,
    reciprocal_rank,
)

# ---------------------------------------------------------------------------------------------
# The two regressions the old scoring hid
# ---------------------------------------------------------------------------------------------


def test_multiset_comparison_catches_a_duplicate_row_difference() -> None:
    """A query that lost a duplicate row is wrong; a set comparison would have called it right."""
    gold = [("Mumbai",), ("Mumbai",), ("Delhi",)]
    predicted = [("Mumbai",), ("Delhi",)]

    result = compare_result_sets(gold, predicted, order_sensitive=False)

    assert result.match is False
    assert result.multiset_match is False
    # The looser verdict is still computed - this is the evidence for the fix, not the metric.
    assert result.set_match is True
    assert result.duplicate_row_difference is True
    assert "multiplicities" in result.reason


def test_ordered_comparison_catches_a_wrong_order_by() -> None:
    """ "Top 3 by revenue" sorted the wrong way is a wrong answer, not a cosmetic difference."""
    gold = [("a", 30), ("b", 20), ("c", 10)]
    predicted = [("c", 10), ("b", 20), ("a", 30)]

    ordered = compare_result_sets(gold, predicted, order_sensitive=True)
    unordered = compare_result_sets(gold, predicted, order_sensitive=False)

    assert ordered.match is False
    assert ordered.ordering_difference is True
    assert "ORDER BY" in ordered.reason
    # Without ORDER BY in the reference, row order is not part of the answer.
    assert unordered.match is True


def test_a_correct_answer_matches_under_both_rules() -> None:
    rows = [("a", 1), ("b", 2)]
    assert compare_result_sets(rows, list(rows), order_sensitive=True).match is True
    assert compare_result_sets(rows, list(rows), order_sensitive=False).match is True


# ---------------------------------------------------------------------------------------------
# Projection, arity and normalisation
# ---------------------------------------------------------------------------------------------


def test_extra_columns_are_detected_but_never_counted_as_correct() -> None:
    """The v1 run's three "failures" that were right answers with one extra column (§4.4)."""
    gold = [("Mumbai",), ("Delhi",)]
    predicted = [("Mumbai", 3), ("Delhi", 1)]

    result = compare_result_sets(
        gold,
        predicted,
        order_sensitive=False,
        gold_columns=["city"],
        predicted_columns=["city", "n"],
    )

    assert result.match is False
    assert result.gold_is_projection_of_predicted is True
    assert result.column_count_match is False
    assert "extra columns" in result.reason


def test_missing_and_extra_rows_are_reported() -> None:
    result = compare_result_sets([("a",), ("b",)], [("a",), ("c",)], order_sensitive=False)
    assert result.missing_rows == (("b",),)
    assert result.extra_rows == (("c",),)


def test_column_names_are_not_part_of_the_primary_metric() -> None:
    """A correct answer under a different alias is still a correct answer."""
    result = compare_result_sets(
        [(4,)],
        [(4,)],
        order_sensitive=False,
        gold_columns=["customer_count"],
        predicted_columns=["total"],
    )
    assert result.match is True
    assert result.column_names_match is False


@pytest.mark.parametrize(
    ("left", "right"),
    [
        (100, 100.0),
        (Decimal("100.00"), 100),
        (1.0000000001, 1.0),
        (date(2025, 1, 2), "2025-01-02"),
    ],
)
def test_numeric_and_date_forms_of_the_same_answer_compare_equal(left, right) -> None:
    """Two drivers returning the same value in different types is not a wrong answer."""
    assert compare_result_sets([(left,)], [(right,)], order_sensitive=False).match is True


def test_booleans_and_their_integer_form_are_the_same_answer() -> None:
    """SQLite has no boolean type; PostgreSQL does. That difference is the driver's, not the query's."""
    assert normalize_value(True) == 1
    assert normalize_value(False) == 0
    assert compare_result_sets([(True,)], [(1,)], order_sensitive=False).match is True
    assert compare_result_sets([(True,)], [(0,)], order_sensitive=False).match is False


def test_float_noise_from_a_different_summation_order_is_tolerated() -> None:
    gold = [(0.1 + 0.2,)]
    predicted = [(0.3,)]
    assert compare_result_sets(gold, predicted, order_sensitive=False).match is True


def test_empty_results_compare_equal_to_each_other_only() -> None:
    assert compare_result_sets([], [], order_sensitive=False).match is True
    assert compare_result_sets([("a",)], [], order_sensitive=False).match is False


# ---------------------------------------------------------------------------------------------
# Reporting primitives
# ---------------------------------------------------------------------------------------------


def test_a_ratio_never_renders_a_bare_percentage() -> None:
    ratio = Ratio(18, 22, "answerable")
    assert ratio.text() == "18 of 22 (81.8%)"
    assert "18" in ratio.text() and "22" in ratio.text()
    payload = ratio.as_dict()
    assert payload["numerator"] == 18
    assert payload["denominator"] == 22


def test_a_ratio_with_no_denominator_is_undefined_not_zero() -> None:
    """Reporting 0% for "0 of 0" invents a measurement that was never taken."""
    ratio = Ratio(0, 0, "nothing ran")
    assert ratio.value is None
    assert ratio.undefined is True
    assert ratio.text() == "n/a (0 cases)"


def test_a_mean_carries_its_sample_size() -> None:
    stat = MeanStat(total=12.0, count=4, label="latency")
    assert stat.mean == 3.0
    assert "n=4" in stat.text("ms")
    assert MeanStat(0.0, 0).text() == "n/a (0 samples)"


def test_percentiles_are_observed_values_and_carry_n() -> None:
    summary = LatencySummary.from_samples([10, 20, 30, 40, 100])
    assert summary.n == 5
    assert summary.max_ms == 100
    # Nearest-rank: every reported percentile is a value that was actually observed.
    assert summary.p50_ms in {10, 20, 30, 40, 100}
    assert "n=5" in summary.text()
    assert LatencySummary.from_samples([]).text() == "n/a (0 samples)"


# ---------------------------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------------------------


def test_recall_at_k_counts_items_not_cases() -> None:
    recall = recall_at_k(["orders", "customers", "products"], ["orders", "products", "regions"])
    assert (recall.numerator, recall.denominator) == (2, 3)


def test_recall_at_k_respects_the_cutoff() -> None:
    recall = recall_at_k(["c"], ["a", "b", "c"], k=2)
    assert recall.numerator == 0
    assert recall_at_k(["c"], ["a", "b", "c"], k=3).numerator == 1


def test_reciprocal_rank_is_one_over_the_first_relevant_position() -> None:
    assert reciprocal_rank(["orders"], ["customers", "orders", "products"]) == pytest.approx(0.5)
    assert reciprocal_rank(["orders"], ["orders"]) == 1.0
    assert reciprocal_rank(["orders"], ["customers"]) == 0.0
