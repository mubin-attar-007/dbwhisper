"""The case contract: what a well-formed case looks like, and what happens to a broken one.

A half-specified case is worse than a missing one - it runs, it scores, and the score is meaningless.
So :class:`~app.evaluation.schema.EvalCase` validates on construction, and these tests pin down each
rule with the failure it prevents.
"""

from __future__ import annotations

import pytest

from app.evaluation.datasets import case_file
from app.evaluation.schema import (
    ConversationTurn,
    EvalCase,
    EvalCaseError,
    EvalDataset,
    ExpectedBehavior,
    SafetyCategory,
    TurnRole,
    dump_dataset,
    load_dataset,
)


def _case(**overrides) -> EvalCase:
    defaults = {
        "id": "t-001",
        "dataset": "retail",
        "database": "eval_retail",
        "dialect": "sqlite",
        "question": "How many customers are there?",
        "gold_sql": "SELECT COUNT(*) FROM customers",
    }
    return EvalCase(**{**defaults, **overrides})


# ---------------------------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------------------------


def test_an_answerable_case_without_reference_sql_is_rejected() -> None:
    """Without a reference query there is nothing to be right about, so nothing to measure."""
    with pytest.raises(EvalCaseError, match="gold_sql"):
        _case(gold_sql=None)


def test_a_clarify_case_without_expected_phrases_is_rejected() -> None:
    """Otherwise any question at all would count as an on-topic clarification."""
    with pytest.raises(EvalCaseError, match="expected_clarification"):
        _case(expected_behavior=ExpectedBehavior.CLARIFY, gold_sql=None)


def test_a_refuse_case_without_a_safety_category_is_rejected() -> None:
    with pytest.raises(EvalCaseError, match="safety_category"):
        _case(expected_behavior=ExpectedBehavior.REFUSE, gold_sql=None)


def test_a_case_without_an_id_is_rejected() -> None:
    with pytest.raises(EvalCaseError, match="id"):
        _case(id="")


def test_a_well_formed_refusal_case_is_accepted() -> None:
    case = _case(
        expected_behavior=ExpectedBehavior.REFUSE,
        gold_sql="DROP TABLE customers",
        safety_category=SafetyCategory.SCHEMA_DESTRUCTION,
        expected_refusal=("permitted",),
    )
    assert case.is_answerable is False
    assert case.safety_category is SafetyCategory.SCHEMA_DESTRUCTION


# ---------------------------------------------------------------------------------------------
# Derived expectations
# ---------------------------------------------------------------------------------------------


def test_expectations_are_derived_from_the_reference_query() -> None:
    """Hand-maintaining a copy of what the SQL says is how a case file drifts."""
    case = _case(
        gold_sql=(
            "SELECT c.customer_name, o.order_date FROM customers c "
            "JOIN orders o ON o.customer_id = c.customer_id"
        )
    ).with_derived_expectations()

    assert case.expected_tables == ("customers", "orders")
    assert "customers.customer_name" in case.expected_columns
    assert "orders.order_date" in case.expected_columns
    assert case.expected_relationships == ("customers.customer_id -> orders.customer_id",)


def test_explicit_expectations_win_over_derived_ones() -> None:
    """A case may legitimately expect retrieval to surface a table the reference query skips."""
    case = _case(expected_tables=("customers", "regions")).with_derived_expectations()
    assert case.expected_tables == ("customers", "regions")


def test_select_aliases_are_not_mistaken_for_columns() -> None:
    """`ORDER BY order_count` refers to an alias; counting it would report a phantom column."""
    case = _case(
        gold_sql=(
            "SELECT channel, COUNT(*) AS order_count FROM orders "
            "GROUP BY channel ORDER BY order_count DESC"
        )
    ).with_derived_expectations()
    assert case.expected_columns == ("orders.channel",)


# ---------------------------------------------------------------------------------------------
# Conversation rendering
# ---------------------------------------------------------------------------------------------


def test_a_single_turn_question_is_passed_through_unchanged() -> None:
    case = _case()
    assert case.contextual_question() == case.question


def test_prior_turns_are_rendered_in_front_of_the_question() -> None:
    case = _case(
        question="And for 2024?",
        conversation_context=(
            ConversationTurn(TurnRole.USER, "How many orders in 2025?"),
            ConversationTurn(TurnRole.ASSISTANT, "Count of orders in 2025.", sql="SELECT 1"),
        ),
    )
    rendered = case.contextual_question()
    assert "How many orders in 2025?" in rendered
    assert "SELECT 1" in rendered
    assert rendered.strip().endswith("And for 2024?")


# ---------------------------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------------------------


def test_a_committed_case_file_round_trips_through_yaml(tmp_path) -> None:
    original = load_dataset(case_file("retail"), derive=False)
    written = dump_dataset(original, tmp_path / "retail.yaml")
    reloaded = load_dataset(written, derive=False)

    assert reloaded.name == original.name
    assert reloaded.version == original.version
    assert [c.id for c in reloaded.cases] == [c.id for c in original.cases]
    assert reloaded.content_hash() == original.content_hash()


def test_the_corpus_hash_changes_when_a_case_changes() -> None:
    """A report cites this hash; if it did not move on an edit, it would be decoration."""
    original = load_dataset(case_file("retail"), derive=False)
    first = original.cases[0]
    edited = EvalCase(
        id=first.id,
        dataset=first.dataset,
        database=first.database,
        dialect=first.dialect,
        question=first.question,
        gold_sql="SELECT COUNT(*) AS n FROM customers WHERE is_active = 1",
    )
    mutated = EvalDataset(
        name=original.name,
        version=original.version,
        database=original.database,
        dialect=original.dialect,
        description=original.description,
        cases=(edited, *original.cases[1:]),
        synthetic=original.synthetic,
    )
    assert mutated.content_hash() != original.content_hash()


def test_duplicate_case_ids_are_rejected(tmp_path) -> None:
    path = tmp_path / "dupes.yaml"
    path.write_text(
        "dataset: x\nversion: '1'\ndatabase: eval_retail\ncases:\n"
        "  - {id: a, question: q, gold_sql: SELECT 1}\n"
        "  - {id: a, question: q2, gold_sql: SELECT 2}\n",
        encoding="utf-8",
    )
    with pytest.raises(EvalCaseError, match="duplicate case id"):
        load_dataset(path)


def test_selection_filters_compose(datasets) -> None:
    dataset = datasets["retail"]
    refusals = dataset.select(behaviors=(ExpectedBehavior.REFUSE,))
    assert refusals
    assert all(c.expected_behavior is ExpectedBehavior.REFUSE for c in refusals)

    joins = dataset.select(tags=("multi_table",), limit=3)
    assert len(joins) == 3
    assert all(c.has_tag("multi_table") for c in joins)

    by_id = dataset.select(ids=(dataset.cases[0].id,))
    assert [c.id for c in by_id] == [dataset.cases[0].id]


def test_dataset_identity_is_name_and_version(datasets) -> None:
    assert datasets["retail"].identity == "retail@1.0.0"


def test_trailing_semicolons_are_stripped_from_reference_sql() -> None:
    case = EvalCase.from_dict(
        {
            "id": "t",
            "dataset": "d",
            "database": "b",
            "question": "q",
            "gold_sql": "SELECT 1;",
        }
    )
    assert case.gold_sql == "SELECT 1"
