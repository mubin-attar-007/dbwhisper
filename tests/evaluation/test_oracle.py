"""The oracle provider, and the one part of it that is a real classifier rather than a script.

:func:`app.evaluation.oracle.looks_ambiguous` decides whether to ask a clarifying question from the
question text alone. It is what makes clarification precision and recall a measurement instead of a
tautology, so it gets its own tests - including a test that it is *not* perfect against the corpus,
because a classifier that agrees with every label is reading them.
"""

from __future__ import annotations

import json

import pytest

from app.evaluation.oracle import ORACLE_PROFILE_LABEL, OracleProvider, looks_ambiguous
from app.evaluation.schema import EvalCase, ExpectedBehavior
from app.llm.registry import FAKE_PROFILE
from app.llm.types import ModelRequest


@pytest.mark.parametrize(
    "question",
    [
        "Show me the best products.",
        "What were our sales last quarter?",
        "How many active users do we have?",
        "What is our return rate?",
        "How is the North doing?",
    ],
)
def test_vague_questions_are_flagged(question: str) -> None:
    verdict = looks_ambiguous(question)
    assert verdict.ambiguous is True
    assert verdict.trigger
    assert verdict.trigger in verdict.clarifying_question()


@pytest.mark.parametrize(
    "question",
    [
        "How many customers do we have?",
        "How much revenue did each product category generate?",
        "How many orders were placed in 2025?",
        "Break churned revenue down by industry.",
        "What share of claims was paid in full, by payer?",
    ],
)
def test_concrete_questions_are_not_flagged(question: str) -> None:
    """A named aggregation, an explicit period or an explicit grouping resolves a vague measure."""
    assert looks_ambiguous(question).ambiguous is False


def test_a_definitional_term_is_not_rescued_by_a_named_aggregation() -> None:
    """ "How many" fixes the arithmetic; it does not say which users count as active."""
    assert looks_ambiguous("How many active users do we have?").ambiguous is True


def test_the_word_generate_is_not_read_as_a_rate() -> None:
    """Substring matching on "rate" would fire on "generate" - and once did."""
    verdict = looks_ambiguous("How much revenue did each product category generate?")
    assert verdict.ambiguous is False


def test_an_empty_question_is_not_ambiguous() -> None:
    assert looks_ambiguous("").ambiguous is False


# ---------------------------------------------------------------------------------------------
# The provider contract
# ---------------------------------------------------------------------------------------------


def _case(**overrides) -> EvalCase:
    defaults = {
        "id": "t-001",
        "dataset": "retail",
        "database": "eval_retail",
        "dialect": "sqlite",
        "question": "How many customers do we have?",
        "gold_sql": "SELECT COUNT(*) AS n FROM customers",
    }
    return EvalCase(**{**defaults, **overrides})


def _request(prompt_name: str) -> ModelRequest:
    return ModelRequest(system="s", user="u", prompt_name=prompt_name)


def test_generation_replays_the_reference_query() -> None:
    oracle = OracleProvider()
    oracle.current_case = _case()
    payload = json.loads(oracle.complete(_request("generate_sql"), FAKE_PROFILE).text)
    assert payload["sql"] == "SELECT COUNT(*) AS n FROM customers"
    assert "oracle" in payload["rationale"]


def test_repair_replays_the_same_reference_query() -> None:
    """There is nothing to repair: the reference query is by definition already correct."""
    oracle = OracleProvider()
    oracle.current_case = _case()
    payload = json.loads(oracle.complete(_request("repair_sql"), FAKE_PROFILE).text)
    assert payload["sql"] == "SELECT COUNT(*) AS n FROM customers"


def test_a_case_with_no_reference_query_yields_an_empty_statement() -> None:
    """Which the graph blocks - an empty statement must never reach a database."""
    oracle = OracleProvider()
    oracle.current_case = _case(
        expected_behavior=ExpectedBehavior.CLARIFY,
        gold_sql=None,
        expected_clarification=("best",),
    )
    payload = json.loads(oracle.complete(_request("generate_sql"), FAKE_PROFILE).text)
    assert payload["sql"] == ""


def test_understanding_asks_only_when_the_classifier_says_so() -> None:
    oracle = OracleProvider()

    oracle.current_case = _case()
    concrete = json.loads(oracle.complete(_request("understand"), FAKE_PROFILE).text)
    assert concrete["requires_clarification"] is False

    oracle.current_case = _case(
        question="Show me the best products.",
        expected_behavior=ExpectedBehavior.CLARIFY,
        gold_sql=None,
        expected_clarification=("best",),
    )
    vague = json.loads(oracle.complete(_request("understand"), FAKE_PROFILE).text)
    assert vague["requires_clarification"] is True
    assert "best" in vague["clarification_question"]


def test_clarification_can_be_switched_off_entirely() -> None:
    oracle = OracleProvider(clarification_enabled=False)
    oracle.current_case = _case(
        question="Show me the best products.",
        expected_behavior=ExpectedBehavior.CLARIFY,
        gold_sql=None,
        expected_clarification=("best",),
    )
    payload = json.loads(oracle.complete(_request("understand"), FAKE_PROFILE).text)
    assert payload["requires_clarification"] is False


def test_the_summary_says_it_is_not_a_model_summary() -> None:
    oracle = OracleProvider()
    oracle.current_case = _case()
    payload = json.loads(oracle.complete(_request("summarize"), FAKE_PROFILE).text)
    assert any("oracle" in caveat for caveat in payload["caveats"])


def test_the_provider_reports_healthy_and_records_its_calls() -> None:
    oracle = OracleProvider()
    oracle.current_case = _case()
    oracle.complete(_request("generate_sql"), FAKE_PROFILE)
    assert oracle.health().available is True
    assert len(oracle.calls) == 1


def test_the_profile_label_marks_the_run_as_an_oracle_run() -> None:
    assert "oracle" in ORACLE_PROFILE_LABEL
