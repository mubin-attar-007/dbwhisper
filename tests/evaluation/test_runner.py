"""The runner, driven through the real graph with the offline oracle.

The assertion this suite exists for is ``unsafe_executions == 0``. Everything else is supporting
evidence that the run was real: that SQL actually reached a database, that rows actually came back,
that the policy engine actually refused the unsafe cases, and that the run's own provenance block
admits the SQL was replayed rather than generated.
"""

from __future__ import annotations

import pytest

from app.evaluation.oracle import looks_ambiguous
from app.evaluation.outcomes import ObservedBehavior
from app.evaluation.runner import RunnerConfig, run
from app.evaluation.schema import ExpectedBehavior


@pytest.fixture(scope="module")
def smoke_run():
    """One retail run, reused across the assertions below - it takes about a second."""
    return run(
        RunnerConfig(
            datasets=("retail",),
            provider="oracle",
            run_label="test-smoke",
            reproduce_command="uv run pytest tests/evaluation/test_runner.py",
        )
    )


@pytest.fixture(scope="module")
def safety_run():
    return run(
        RunnerConfig(
            provider="oracle",
            behaviors=(ExpectedBehavior.REFUSE,),
            run_label="test-safety",
            reproduce_command="uv run python -m app.evaluation.cli safety",
        )
    )


# ---------------------------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------------------------


def test_no_unsafe_execution_across_the_whole_corpus(safety_run) -> None:
    """The non-negotiable invariant: nothing whose contract is to be declined ever returns rows."""
    assert safety_run.scorecard.safety.unsafe_executions == 0, (
        f"unsafe executions in {safety_run.scorecard.safety.unsafe_case_ids}"
    )
    assert safety_run.scorecard.safety.clean is True


def test_every_unsafe_request_is_actually_declined(safety_run) -> None:
    refusals = safety_run.scorecard.safety.correct_refusals
    assert refusals.denominator >= 15, "the safety corpus is too small to mean anything"
    assert refusals.numerator == refusals.denominator, "not declined: " + ", ".join(
        o.case_id for o in safety_run.outcomes if not o.behaviour_matched
    )


def test_unsafe_cases_never_reach_the_database(safety_run) -> None:
    for outcome in safety_run.outcomes:
        assert outcome.executed is False, f"{outcome.case_id} executed {outcome.executed_sql!r}"
        assert outcome.row_count in (None, 0)


def test_the_smoke_run_is_also_clean(smoke_run) -> None:
    assert smoke_run.unsafe_executions == 0


# ---------------------------------------------------------------------------------------------
# The run was real
# ---------------------------------------------------------------------------------------------


def test_answerable_cases_actually_executed_sql(smoke_run) -> None:
    answered = [
        o
        for o in smoke_run.outcomes
        if o.expected_behavior is ExpectedBehavior.ANSWER
        and o.observed_behavior is ObservedBehavior.ANSWERED
    ]
    assert len(answered) >= 40, "too few cases reached the database for this to be a real run"
    for outcome in answered:
        assert outcome.executed_sql, f"{outcome.case_id} claims to have answered without SQL"
        assert outcome.row_count and outcome.row_count > 0
        assert outcome.policy_decision in {"allow", "needs_approval"}
        assert outcome.comparison is not None


def test_the_oracle_reproduces_the_reference_results(smoke_run) -> None:
    """Replayed reference SQL must score as correct - if it does not, the harness is broken."""
    answerable = [o for o in smoke_run.outcomes if o.expected_behavior is ExpectedBehavior.ANSWER]
    mismatched = [o for o in answerable if not o.result_matched]
    assert not mismatched, "the reference query did not reproduce its own result for: " + ", ".join(
        f"{o.case_id} ({o.failure_category})" for o in mismatched
    )


def test_retrieval_evidence_is_recorded_per_case(smoke_run) -> None:
    scored = [o for o in smoke_run.outcomes if o.retrieval.expected_tables]
    assert scored
    assert all(o.retrieval.retrieved_tables for o in scored)
    assert smoke_run.scorecard.retrieval.table_recall.denominator > 0


def test_costs_are_recorded(smoke_run) -> None:
    assert smoke_run.scorecard.ops.latency.n == len(smoke_run.outcomes)
    assert smoke_run.scorecard.ops.model_calls.total > 0
    assert all(o.cost.latency_ms > 0 for o in smoke_run.outcomes)


def test_every_outcome_carries_a_failure_category(smoke_run) -> None:
    assert all(o.failure_category for o in smoke_run.outcomes)


def test_every_outcome_is_json_serialisable(smoke_run) -> None:
    import json

    payload = json.dumps([o.as_dict() for o in smoke_run.outcomes], default=str)
    assert len(payload) > 1000


# ---------------------------------------------------------------------------------------------
# Honesty
# ---------------------------------------------------------------------------------------------


def test_an_oracle_run_declares_that_it_did_not_measure_a_model(smoke_run) -> None:
    provenance = smoke_run.provenance
    assert provenance.measures_model is False
    assert provenance.publishable is False, "an oracle run must never be marked publishable"
    assert any("NOT GENERATED BY A MODEL" in limit for limit in provenance.limitations)
    assert "oracle" in provenance.model_profile


def test_the_provenance_block_is_otherwise_complete(smoke_run) -> None:
    assert smoke_run.provenance.missing_fields() == []
    assert smoke_run.provenance.n == len(smoke_run.outcomes)
    assert smoke_run.provenance.dataset_hash.startswith("sha256:")
    assert smoke_run.provenance.component_versions["sql_policy"].startswith("sql_policy@")
    assert smoke_run.provenance.fixture_digests["retail"].startswith("sha256:")


def test_filtering_is_recorded_as_an_exclusion() -> None:
    """A partial run that did not say it was partial is how an accidental cherry-pick gets published."""
    result = run(RunnerConfig(datasets=("retail",), limit_per_dataset=3, provider="oracle"))
    assert result.provenance.exclusions
    assert any("limited to 3" in exclusion for exclusion in result.provenance.exclusions)
    assert result.provenance.n == 3


# ---------------------------------------------------------------------------------------------
# Clarification behaviour is measured, not assumed
# ---------------------------------------------------------------------------------------------


def test_the_ambiguity_classifier_never_sees_the_expected_label(all_cases) -> None:
    """It reads the question and nothing else, so its precision and recall mean something."""
    verdicts = {case.id: looks_ambiguous(case.question).ambiguous for case in all_cases}
    ambiguous = [c for c in all_cases if c.expected_behavior is ExpectedBehavior.CLARIFY]
    assert any(not verdicts[c.id] for c in ambiguous), (
        "the classifier agrees with every label, which would mean it is reading them"
    )


def test_clarification_metrics_are_computed_over_the_real_corpus(smoke_run) -> None:
    clarification = smoke_run.scorecard.clarification
    assert clarification.recall.denominator >= 5
    assert clarification.unnecessary_rate.denominator >= 40
    # A clarification that did happen must have recorded the question it asked.
    asked = [o for o in smoke_run.outcomes if o.observed_behavior is ObservedBehavior.CLARIFIED]
    assert all(o.clarification_question for o in asked)


def test_resuming_a_clarification_changes_what_is_measured_and_says_so() -> None:
    result = run(
        RunnerConfig(
            datasets=("retail",),
            provider="oracle",
            behaviors=(ExpectedBehavior.CLARIFY,),
            resume_clarifications=True,
        )
    )
    assert any("resumed" in limit for limit in result.provenance.limitations)
