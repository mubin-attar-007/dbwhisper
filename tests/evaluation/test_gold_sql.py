"""Every reference query must actually run. This is the test that makes the corpus a benchmark.

A case file whose gold SQL has never been executed is a wish list. Three properties are checked here,
and all three failed somewhere in the v1 harness:

* every ``ANSWER`` case's reference query **parses, passes the real policy engine against the real
  enrolled scope, executes against the generated fixture, and returns rows**;
* every ``REFUSE`` case's stored statement is **denied** by that same policy engine - a safety case
  whose statement the engine would have allowed is testing nothing;
* the reference result is **stable across two executions**, so a comparison against it means
  something.
"""

from __future__ import annotations

import pytest

from app.evaluation.datasets import SPECS
from app.evaluation.datasets.build import run_readonly
from app.evaluation.runner import load_dataset_for
from app.evaluation.schema import EvalCase, ExpectedBehavior
from app.evaluation.sqlfacts import sql_facts
from app.sqlpolicy import Decision, PolicyContext, evaluate
from app.sqlpolicy.scope_loader import scope_from_schema_index
from app.sqlpolicy.types import Dialect, PolicyLevel


def _policy_context(spec) -> PolicyContext:
    dialect = Dialect(spec.dialect)
    return PolicyContext(
        dialect=dialect,
        scope=scope_from_schema_index(spec.source_id, dialect),
        level=PolicyLevel.STANDARD,
        default_row_limit=1000,
        inject_limit=True,
        require_scope=True,
    )


def _cases(behavior: ExpectedBehavior) -> list[tuple]:
    out = []
    for spec in SPECS:
        for case in load_dataset_for(spec).cases:
            if case.expected_behavior is behavior:
                out.append(pytest.param(spec, case, id=case.id))
    return out


ANSWERABLE = _cases(ExpectedBehavior.ANSWER)
REFUSALS = [p for p in _cases(ExpectedBehavior.REFUSE) if p.values[1].gold_sql]


@pytest.mark.parametrize(("spec", "case"), ANSWERABLE)
def test_gold_sql_executes_and_returns_rows(spec, case: EvalCase) -> None:
    """The reference query runs against the fixture and produces an answer worth comparing to."""
    assert case.gold_sql, f"{case.id} is an answerable case with no reference query"

    columns, rows = run_readonly(spec, case.gold_sql)

    assert columns, f"{case.id}: the reference query returned no columns"
    assert rows, (
        f"{case.id}: the reference query returned no rows. An empty reference makes the case "
        "unfalsifiable - any query returning nothing would score as correct."
    )


@pytest.mark.parametrize(("spec", "case"), ANSWERABLE)
def test_gold_sql_passes_the_real_policy_engine(spec, case: EvalCase) -> None:
    """The reference query is allowed by the same engine that gates production traffic."""
    decision = evaluate(case.gold_sql, _policy_context(spec))
    assert decision.decision is not Decision.DENY, (
        f"{case.id}: the policy engine denied its own reference query - {decision.reason}"
    )


@pytest.mark.parametrize(("spec", "case"), ANSWERABLE)
def test_gold_sql_references_only_real_objects(spec, case: EvalCase) -> None:
    """Derived expectations name tables and columns the fixture actually has."""
    facts = sql_facts(case.gold_sql, spec.dialect)
    assert facts.parsed, f"{case.id}: reference query does not parse - {facts.parse_error}"

    known_tables = {t.lower() for t in spec.table_names}
    unknown = sorted(set(facts.tables) - known_tables)
    assert not unknown, f"{case.id}: reference query names tables that do not exist: {unknown}"

    known_columns = spec.qualified_columns()
    bad = [c for c in facts.columns if "." in c and c not in known_columns]
    assert not bad, f"{case.id}: reference query names columns that do not exist: {bad}"

    assert case.expected_tables, (
        f"{case.id}: no expected tables were derived from the reference SQL"
    )


@pytest.mark.parametrize(("spec", "case"), REFUSALS)
def test_unsafe_statements_are_denied(spec, case: EvalCase) -> None:
    """A safety case whose statement the engine allows would prove nothing."""
    decision = evaluate(case.gold_sql, _policy_context(spec))
    assert decision.decision is Decision.DENY, (
        f"{case.id}: the policy engine did not deny {case.gold_sql!r} - it returned "
        f"{decision.decision.value}. This case cannot demonstrate a refusal."
    )


@pytest.mark.parametrize(("spec", "case"), ANSWERABLE[:20])
def test_reference_results_are_stable(spec, case: EvalCase) -> None:
    """Running the reference query twice returns the same rows in the same order."""
    first_columns, first_rows = run_readonly(spec, case.gold_sql)
    second_columns, second_rows = run_readonly(spec, case.gold_sql)
    assert first_columns == second_columns
    assert first_rows == second_rows


def test_every_dataset_has_at_least_one_answerable_case() -> None:
    for spec in SPECS:
        dataset = load_dataset_for(spec)
        answerable = dataset.select(behaviors=(ExpectedBehavior.ANSWER,))
        assert answerable, f"{spec.name} has no answerable cases"
