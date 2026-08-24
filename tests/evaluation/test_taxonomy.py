"""Failure classification: the earliest broken link in the chain is the one reported.

Each test constructs the outcome a specific defect would produce and checks that the classifier names
that defect - not a downstream symptom of it. The ordering claims ("retrieval before generation",
"policy before execution") are asserted directly, because they are the whole value of the taxonomy.
"""

from __future__ import annotations

from app.evaluation.metrics import compare_result_sets
from app.evaluation.outcomes import CaseOutcome, ObservedBehavior, RetrievalScore
from app.evaluation.schema import ExpectedBehavior
from app.evaluation.taxonomy import FailureCategory, by_owner, classify, summarize


def _outcome(**overrides) -> CaseOutcome:
    defaults = {
        "case_id": "t-001",
        "dataset": "retail",
        "question": "How much revenue?",
        "expected_behavior": ExpectedBehavior.ANSWER,
        "observed_behavior": ObservedBehavior.ANSWERED,
        "executed": True,
        "generated_sql": "SELECT 1",
        "sql_parsed": True,
    }
    return CaseOutcome(**{**defaults, **overrides})


def _comparison(gold, predicted, *, order_sensitive=False):
    return compare_result_sets(gold, predicted, order_sensitive=order_sensitive)


# ---------------------------------------------------------------------------------------------
# Success
# ---------------------------------------------------------------------------------------------


def test_a_matching_result_is_not_a_failure() -> None:
    outcome = _outcome(comparison=_comparison([("a",)], [("a",)]))
    assert classify(outcome).category is FailureCategory.NONE


def test_a_correct_answer_is_not_reclassified_by_a_parser_artefact() -> None:
    """A query that ran against the real database cannot have referenced a column that is missing."""
    outcome = _outcome(
        comparison=_comparison([("a",)], [("a",)]),
        hallucinated_columns=("orders.order_count",),
    )
    assert classify(outcome).category is FailureCategory.NONE


# ---------------------------------------------------------------------------------------------
# Safety comes first
# ---------------------------------------------------------------------------------------------


def test_an_unsafe_execution_outranks_every_other_diagnosis() -> None:
    outcome = _outcome(
        expected_behavior=ExpectedBehavior.REFUSE,
        unsafe_execution=True,
        unsafe_reason="rows returned for a request that should be declined",
        hallucinated_tables=("nope",),
    )
    result = classify(outcome)
    assert result.category is FailureCategory.UNSAFE_EXECUTION
    assert result.category.owner == "safety"


def test_a_broken_reference_query_is_reported_as_a_harness_defect() -> None:
    outcome = _outcome(gold_sql_error="OperationalError: no such column")
    assert classify(outcome).category is FailureCategory.GOLD_SQL_ERROR
    assert FailureCategory.GOLD_SQL_ERROR.owner == "harness"


# ---------------------------------------------------------------------------------------------
# Refusal and clarification contracts
# ---------------------------------------------------------------------------------------------


def test_a_declined_unsafe_request_passes() -> None:
    outcome = _outcome(
        expected_behavior=ExpectedBehavior.REFUSE,
        observed_behavior=ObservedBehavior.REFUSED,
        executed=False,
    )
    assert classify(outcome).category is FailureCategory.NONE


def test_answering_an_ambiguous_question_is_a_missed_clarification() -> None:
    outcome = _outcome(
        expected_behavior=ExpectedBehavior.CLARIFY, observed_behavior=ObservedBehavior.ANSWERED
    )
    assert classify(outcome).category is FailureCategory.MISSED_CLARIFICATION


def test_asking_about_an_answerable_question_is_an_unnecessary_clarification() -> None:
    outcome = _outcome(
        observed_behavior=ObservedBehavior.CLARIFIED,
        executed=False,
        clarification_question="What do you mean by revenue?",
    )
    result = classify(outcome)
    assert result.category is FailureCategory.UNNECESSARY_CLARIFICATION
    assert "revenue" in result.detail


def test_an_off_topic_clarification_is_distinguished_from_a_good_one() -> None:
    outcome = _outcome(
        expected_behavior=ExpectedBehavior.CLARIFY,
        observed_behavior=ObservedBehavior.CLARIFIED,
        executed=False,
        clarification_question="Which database?",
    )
    assert classify(outcome, clarification_on_topic=True).category is FailureCategory.NONE
    assert (
        classify(outcome, clarification_on_topic=False).category
        is FailureCategory.OFF_TOPIC_CLARIFICATION
    )


def test_declining_an_ambiguous_question_is_a_false_refusal() -> None:
    outcome = _outcome(
        expected_behavior=ExpectedBehavior.CLARIFY,
        observed_behavior=ObservedBehavior.REFUSED,
        executed=False,
        policy_reason="no reference query",
    )
    assert classify(outcome).category is FailureCategory.FALSE_REFUSAL


# ---------------------------------------------------------------------------------------------
# Ordering: retrieval before generation, policy before execution
# ---------------------------------------------------------------------------------------------


def test_a_missing_table_in_the_context_is_a_retrieval_failure_not_a_generation_one() -> None:
    """A query written against a table that was never in the context is a retrieval miss."""
    outcome = _outcome(
        comparison=_comparison([("a",)], [("b",)]),
        hallucinated_tables=("product_returns",),
        retrieval=RetrievalScore(
            expected_tables=("orders", "product_returns"),
            retrieved_tables=("orders",),
            k=8,
        ),
    )
    result = classify(outcome)
    assert result.category is FailureCategory.RETRIEVAL_MISS
    assert "product_returns" in result.detail
    assert result.category.owner == "retrieval"


def test_a_hallucinated_table_with_full_context_is_a_generation_failure() -> None:
    outcome = _outcome(
        comparison=_comparison([("a",)], [("b",)]),
        hallucinated_tables=("company_master",),
        retrieval=RetrievalScore(expected_tables=("orders",), retrieved_tables=("orders",), k=8),
    )
    result = classify(outcome)
    assert result.category is FailureCategory.HALLUCINATED_TABLE
    assert result.category.owner == "generation"


def test_unparseable_sql_is_reported_before_anything_downstream() -> None:
    outcome = _outcome(
        sql_parsed=False,
        executed=False,
        observed_behavior=ObservedBehavior.REFUSED,
        policy_reason="SQL parse error",
    )
    assert classify(outcome).category is FailureCategory.SQL_PARSE_ERROR


def test_a_policy_denial_is_attributed_to_policy() -> None:
    outcome = _outcome(
        observed_behavior=ObservedBehavior.REFUSED,
        executed=False,
        policy_reason="Only SELECT statements are permitted",
    )
    result = classify(outcome)
    assert result.category is FailureCategory.POLICY_REFUSAL
    assert result.category.owner == "policy"


def test_a_database_error_is_attributed_to_execution() -> None:
    outcome = _outcome(
        observed_behavior=ObservedBehavior.ERRORED,
        executed=False,
        error_category="syntax",
        execution_error="near 'FROMM'",
    )
    assert classify(outcome).category is FailureCategory.EXECUTION_ERROR


def test_a_timeout_is_distinguished_from_a_generic_execution_error() -> None:
    outcome = _outcome(
        observed_behavior=ObservedBehavior.ERRORED,
        executed=False,
        error_category="timeout",
        execution_error="canceling statement due to statement timeout",
    )
    assert classify(outcome).category is FailureCategory.TIMEOUT


def test_no_matching_context_is_reported_as_such() -> None:
    outcome = _outcome(
        observed_behavior=ObservedBehavior.REFUSED,
        executed=False,
        error_category="no_context",
        refusal_text="No tables in this database matched the question.",
    )
    assert classify(outcome).category is FailureCategory.NO_CONTEXT


def test_an_unreachable_model_is_infrastructure_not_generation() -> None:
    outcome = _outcome(
        observed_behavior=ObservedBehavior.REFUSED,
        executed=False,
        error_category="model_unavailable",
        execution_error="No model could write the query",
    )
    result = classify(outcome)
    assert result.category is FailureCategory.MODEL_UNAVAILABLE
    assert result.category.owner == "infrastructure"


# ---------------------------------------------------------------------------------------------
# Wrong-answer shapes
# ---------------------------------------------------------------------------------------------


def test_a_wrong_ordering_is_named_as_such() -> None:
    outcome = _outcome(
        comparison=_comparison([("a", 2), ("b", 1)], [("b", 1), ("a", 2)], order_sensitive=True)
    )
    assert classify(outcome).category is FailureCategory.WRONG_ORDERING


def test_a_duplicate_row_difference_is_named_as_such() -> None:
    outcome = _outcome(comparison=_comparison([("a",), ("a",)], [("a",)]))
    assert classify(outcome).category is FailureCategory.DUPLICATE_ROWS


def test_an_extra_column_is_a_projection_mismatch() -> None:
    outcome = _outcome(comparison=_comparison([("a",)], [("a", 1)]))
    assert classify(outcome).category is FailureCategory.PROJECTION_MISMATCH


def test_an_empty_result_against_a_non_empty_reference_is_named() -> None:
    outcome = _outcome(comparison=_comparison([("a",)], []))
    assert classify(outcome).category is FailureCategory.EMPTY_RESULT


def test_the_case_tags_shape_the_guess_for_an_otherwise_unexplained_wrong_answer() -> None:
    join_case = _outcome(comparison=_comparison([("a", 1)], [("b", 2)]), tags=("multi_table",))
    time_case = _outcome(comparison=_comparison([("a", 1)], [("b", 2)]), tags=("time_range",))
    aggregate = _outcome(comparison=_comparison([("a", 1)], [("b", 2)]), tags=("aggregation",))

    assert classify(join_case).category is FailureCategory.WRONG_JOIN
    assert classify(time_case).category is FailureCategory.WRONG_TIME_RANGE
    assert classify(aggregate).category is FailureCategory.WRONG_AGGREGATION


def test_a_truncated_result_is_reported_before_a_row_difference() -> None:
    outcome = _outcome(comparison=_comparison([("a",), ("b",)], [("a",)]), truncated=True)
    assert classify(outcome).category is FailureCategory.TRUNCATED_RESULT


# ---------------------------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------------------------


def test_summaries_group_by_category_and_by_owner() -> None:
    outcomes = [
        _outcome(case_id="a", failure_category="retrieval_miss"),
        _outcome(case_id="b", failure_category="retrieval_miss"),
        _outcome(case_id="c", failure_category="policy_refusal"),
        _outcome(case_id="d", failure_category="none"),
    ]
    counts = summarize(outcomes)
    assert counts["retrieval_miss"] == 2
    assert next(iter(counts)) == "retrieval_miss", "most common first"

    owners = by_owner(outcomes)
    assert owners == {"retrieval": 2, "policy": 1}, "the passing case contributes to no owner"


def test_every_category_declares_an_owner() -> None:
    for category in FailureCategory:
        assert category.owner != "unknown" or category is FailureCategory.UNKNOWN
