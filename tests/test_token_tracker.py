"""Tests for the naive token tracker / cost estimator."""

from __future__ import annotations

from app.utils.token_tracker import TokenTracker


def test_count_tokens():
    tracker = TokenTracker()
    assert tracker.count_tokens("a b c") == 3
    assert tracker.count_tokens("") == 0
    assert tracker.count_tokens(None) == 0


def test_track_request_totals_and_cost():
    tracker = TokenTracker()
    usage = tracker.track_request(
        query="a b",
        schema_text="c d e",
        generated_sql="f",
        response_text="g h",
        db_flag="demo",
    )
    assert usage.total_input_tokens == 5  # 2 query + 3 schema
    assert usage.total_output_tokens == 3  # 1 sql + 2 response
    assert usage.cost_usd > 0
    assert tracker.history and tracker.history[-1] is usage
