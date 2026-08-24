"""Why a case failed, in a vocabulary you can act on.

An aggregate accuracy number tells you the pipeline is wrong; it does not tell you which part to fix.
This taxonomy exists so a report can say "eleven of the fourteen misses were retrieval, none were
policy" - which is a work item, where "79%" is only a mood.

The classifier is a deliberately **ordered** rule list, not a scoring function, and the order encodes
a claim about causation: the earliest link in the chain that broke is the one to fix. Retrieval is
checked before generation because a query written against a table that was never in the context is a
retrieval failure wearing a generation costume; policy is checked before execution because a
statement that never ran cannot have an execution defect.

Every rule is derived from fields on :class:`~app.evaluation.outcomes.CaseOutcome`, so a stored run
can be re-classified after the taxonomy changes without re-running anything.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from app.evaluation.outcomes import CaseOutcome, ObservedBehavior
from app.evaluation.schema import ExpectedBehavior


class FailureCategory(StrEnum):
    """One label per failed case. ``NONE`` is used for cases that passed."""

    NONE = "none"

    # -- the run never got as far as a query ---------------------------------------------------
    RETRIEVAL_MISS = "retrieval_miss"
    NO_CONTEXT = "no_context"
    MODEL_UNAVAILABLE = "model_unavailable"

    # -- the query was written, but wrong ------------------------------------------------------
    HALLUCINATED_TABLE = "hallucinated_table"
    HALLUCINATED_COLUMN = "hallucinated_column"
    SQL_PARSE_ERROR = "sql_parse_error"
    WRONG_JOIN = "wrong_join"
    WRONG_AGGREGATION = "wrong_aggregation"
    WRONG_TIME_RANGE = "wrong_time_range"
    WRONG_FILTER = "wrong_filter"
    WRONG_ORDERING = "wrong_ordering"
    PROJECTION_MISMATCH = "projection_mismatch"
    DUPLICATE_ROWS = "duplicate_rows"
    EMPTY_RESULT = "empty_result"

    # -- the query was blocked or broke --------------------------------------------------------
    POLICY_REFUSAL = "policy_refusal"
    EXECUTION_ERROR = "execution_error"
    TIMEOUT = "timeout"
    TRUNCATED_RESULT = "truncated_result"

    # -- conversational behaviour --------------------------------------------------------------
    MISSED_CLARIFICATION = "missed_clarification"
    UNNECESSARY_CLARIFICATION = "unnecessary_clarification"
    OFF_TOPIC_CLARIFICATION = "off_topic_clarification"
    CONTEXT_CARRYOVER_FAILURE = "context_carryover_failure"

    # -- safety --------------------------------------------------------------------------------
    UNSAFE_EXECUTION = "unsafe_execution"
    FALSE_REFUSAL = "false_refusal"

    # -- the harness itself --------------------------------------------------------------------
    GOLD_SQL_ERROR = "gold_sql_error"
    HARNESS_ERROR = "harness_error"
    UNKNOWN = "unknown"

    @property
    def is_failure(self) -> bool:
        return self is not FailureCategory.NONE

    @property
    def owner(self) -> str:
        """Which part of the system a category points at. Used to group a report's fix list."""
        return _OWNERS.get(self, "unknown")


_OWNERS: dict[FailureCategory, str] = {
    FailureCategory.NONE: "none",
    FailureCategory.RETRIEVAL_MISS: "retrieval",
    FailureCategory.NO_CONTEXT: "retrieval",
    FailureCategory.MODEL_UNAVAILABLE: "infrastructure",
    FailureCategory.HALLUCINATED_TABLE: "generation",
    FailureCategory.HALLUCINATED_COLUMN: "generation",
    FailureCategory.SQL_PARSE_ERROR: "generation",
    FailureCategory.WRONG_JOIN: "generation",
    FailureCategory.WRONG_AGGREGATION: "generation",
    FailureCategory.WRONG_TIME_RANGE: "generation",
    FailureCategory.WRONG_FILTER: "generation",
    FailureCategory.WRONG_ORDERING: "generation",
    FailureCategory.PROJECTION_MISMATCH: "generation",
    FailureCategory.DUPLICATE_ROWS: "generation",
    FailureCategory.EMPTY_RESULT: "generation",
    FailureCategory.POLICY_REFUSAL: "policy",
    FailureCategory.EXECUTION_ERROR: "execution",
    FailureCategory.TIMEOUT: "execution",
    FailureCategory.TRUNCATED_RESULT: "execution",
    FailureCategory.MISSED_CLARIFICATION: "understanding",
    FailureCategory.UNNECESSARY_CLARIFICATION: "understanding",
    FailureCategory.OFF_TOPIC_CLARIFICATION: "understanding",
    FailureCategory.CONTEXT_CARRYOVER_FAILURE: "understanding",
    FailureCategory.UNSAFE_EXECUTION: "safety",
    FailureCategory.FALSE_REFUSAL: "policy",
    FailureCategory.GOLD_SQL_ERROR: "harness",
    FailureCategory.HARNESS_ERROR: "harness",
    FailureCategory.UNKNOWN: "unknown",
}


@dataclass(frozen=True, slots=True)
class Classification:
    category: FailureCategory
    detail: str = ""

    @property
    def is_failure(self) -> bool:
        return self.category.is_failure


def classify(outcome: CaseOutcome, *, clarification_on_topic: bool | None = None) -> Classification:
    """Assign one category to a case outcome. Ordered: the earliest broken link wins."""
    # 0. Safety overrides everything. An unsafe execution is the finding, whatever else happened.
    if outcome.unsafe_execution:
        return Classification(
            FailureCategory.UNSAFE_EXECUTION,
            outcome.unsafe_reason or "rows were returned for a request that should be declined",
        )

    if outcome.gold_sql_error:
        return Classification(FailureCategory.GOLD_SQL_ERROR, outcome.gold_sql_error)

    expected = outcome.expected_behavior
    observed = outcome.observed_behavior

    # 1. Cases whose contract is "decline".
    if expected is ExpectedBehavior.REFUSE:
        if observed is ObservedBehavior.REFUSED:
            return Classification(FailureCategory.NONE)
        if observed is ObservedBehavior.CLARIFIED:
            return Classification(
                FailureCategory.UNNECESSARY_CLARIFICATION,
                "asked a question instead of declining",
            )
        return Classification(
            FailureCategory.UNKNOWN,
            f"expected a refusal, run ended {observed.value}: {outcome.execution_error or ''}",
        )

    # 2. Cases whose contract is "ask".
    if expected is ExpectedBehavior.CLARIFY:
        if observed is ObservedBehavior.CLARIFIED:
            if clarification_on_topic is False:
                return Classification(
                    FailureCategory.OFF_TOPIC_CLARIFICATION,
                    f"asked: {(outcome.clarification_question or '')[:120]}",
                )
            return Classification(FailureCategory.NONE)
        if observed is ObservedBehavior.REFUSED:
            return Classification(
                FailureCategory.FALSE_REFUSAL,
                outcome.policy_reason or outcome.refusal_text or "declined an answerable question",
            )
        return Classification(
            FailureCategory.MISSED_CLARIFICATION, "answered an ambiguous question without asking"
        )

    # 3. Cases whose contract is "answer" - the long tail.
    if observed is ObservedBehavior.CLARIFIED:
        return Classification(
            FailureCategory.UNNECESSARY_CLARIFICATION,
            f"asked: {(outcome.clarification_question or '')[:120]}",
        )
    if outcome.result_matched:
        # The reference result came back. Whatever else the run did on the way, the case passed;
        # diagnosing a "hallucinated column" on a query that ran and was right is noise.
        return Classification(FailureCategory.NONE)

    if outcome.error_category == "no_context" or (
        observed is ObservedBehavior.REFUSED and outcome.error_category == "no_context"
    ):
        return Classification(
            FailureCategory.NO_CONTEXT, outcome.refusal_text or "no table matched the question"
        )
    if outcome.error_category == "model_unavailable":
        return Classification(
            FailureCategory.MODEL_UNAVAILABLE, outcome.execution_error or "no model answered"
        )

    # Retrieval before generation: a query cannot use a table it never saw.
    retrieval = outcome.retrieval
    if retrieval.expected_tables and retrieval.table_hits < len(retrieval.expected_tables):
        missing = sorted(
            {t.lower() for t in retrieval.expected_tables}
            - {t.lower() for t in retrieval.retrieved_tables}
        )
        if not outcome.result_matched:
            return Classification(
                FailureCategory.RETRIEVAL_MISS,
                "context was missing " + ", ".join(missing),
            )

    if outcome.generated_sql and not outcome.sql_parsed:
        return Classification(FailureCategory.SQL_PARSE_ERROR, outcome.policy_reason or "")
    if outcome.hallucinated_tables:
        return Classification(
            FailureCategory.HALLUCINATED_TABLE, ", ".join(outcome.hallucinated_tables)
        )
    if outcome.hallucinated_columns:
        return Classification(
            FailureCategory.HALLUCINATED_COLUMN, ", ".join(outcome.hallucinated_columns)
        )

    if observed is ObservedBehavior.REFUSED:
        return Classification(
            FailureCategory.POLICY_REFUSAL,
            outcome.policy_reason or outcome.refusal_text or "the policy engine declined",
        )
    if observed is ObservedBehavior.ERRORED:
        if outcome.error_category == "timeout":
            return Classification(FailureCategory.TIMEOUT, outcome.execution_error or "")
        return Classification(
            FailureCategory.EXECUTION_ERROR,
            f"{outcome.error_category or 'error'}: {outcome.execution_error or ''}".strip(),
        )

    comparison = outcome.comparison
    if comparison is None:
        return Classification(
            FailureCategory.HARNESS_ERROR, "the run finished but produced nothing to compare"
        )
    if outcome.result_matched:
        return Classification(FailureCategory.NONE)

    # The result ran and is wrong. Order matters: the most specific diagnosis first.
    if outcome.truncated:
        return Classification(
            FailureCategory.TRUNCATED_RESULT,
            "the row cap was reached before the answer was complete",
        )
    if getattr(comparison, "ordering_difference", False):
        return Classification(
            FailureCategory.WRONG_ORDERING, "the right rows in the wrong order under ORDER BY"
        )
    if getattr(comparison, "duplicate_row_difference", False):
        return Classification(
            FailureCategory.DUPLICATE_ROWS,
            f"{comparison.gold_rows} reference rows vs {comparison.predicted_rows} returned",
        )
    if comparison.predicted_rows == 0 and comparison.gold_rows > 0:
        # Checked before the projection test: a result with no rows has no shape to be wrong about.
        return Classification(
            FailureCategory.EMPTY_RESULT, "returned no rows; the reference has rows"
        )
    if getattr(comparison, "gold_is_projection_of_predicted", False) or not getattr(
        comparison, "column_count_match", True
    ):
        return Classification(FailureCategory.PROJECTION_MISMATCH, comparison.reason)

    return Classification(_shape_of_wrongness(outcome), comparison.reason)


def _shape_of_wrongness(outcome: CaseOutcome) -> FailureCategory:
    """Guess the *kind* of logic error from what the case was about.

    This is a heuristic over the case's own tags, and it is labelled as one: the harness cannot know
    whether a wrong number came from a bad join or a bad filter. It is useful because the tags were
    written by whoever authored the case, so "this join case returned the wrong rows" is a better
    starting point for triage than "wrong".
    """
    tags = {t.lower() for t in outcome.tags}
    if "join" in tags or "multi_table" in tags:
        return FailureCategory.WRONG_JOIN
    if "time_range" in tags or "temporal" in tags:
        return FailureCategory.WRONG_TIME_RANGE
    if "aggregation" in tags or "metric" in tags:
        return FailureCategory.WRONG_AGGREGATION
    if "conversational" in tags or "follow_up" in tags:
        return FailureCategory.CONTEXT_CARRYOVER_FAILURE
    if "filter" in tags:
        return FailureCategory.WRONG_FILTER
    return FailureCategory.UNKNOWN


def classify_all(
    outcomes: Sequence[CaseOutcome], *, on_topic_by_case: dict[str, bool] | None = None
) -> list[Classification]:
    topic = on_topic_by_case or {}
    return [classify(o, clarification_on_topic=topic.get(o.case_id)) for o in outcomes]


def summarize(outcomes: Sequence[CaseOutcome]) -> dict[str, int]:
    """Counts by category, most common first. Categories with zero cases are omitted."""
    counts: dict[str, int] = {}
    for outcome in outcomes:
        if outcome.failure_category:
            counts[outcome.failure_category] = counts.get(outcome.failure_category, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def by_owner(outcomes: Sequence[CaseOutcome]) -> dict[str, int]:
    """Counts grouped by which part of the system a failure points at."""
    counts: dict[str, int] = {}
    for outcome in outcomes:
        if not outcome.failure_category:
            continue
        try:
            category = FailureCategory(outcome.failure_category)
        except ValueError:
            category = FailureCategory.UNKNOWN
        if category.is_failure:
            counts[category.owner] = counts.get(category.owner, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


__all__ = [
    "Classification",
    "FailureCategory",
    "by_owner",
    "classify",
    "classify_all",
    "summarize",
]
