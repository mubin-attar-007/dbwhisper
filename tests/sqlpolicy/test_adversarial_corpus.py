"""Runs every case in the adversarial corpus against the policy engine (no database involved).

The corpus is the regression record for bypasses and false positives. A failing case here blocks
merge; fixing it means changing the engine (or, for a deliberate policy change, the corpus with a
version bump).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from tests.sqlpolicy.conftest import make_ctx

from app.sqlpolicy import Decision, Dialect, PolicyLevel, evaluate

CORPUS = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "evaluation"
    / "datasets"
    / "adversarial"
    / "sql_policy_cases.yaml"
)
_DIALECTS = {
    "postgres": Dialect.POSTGRES,
    "mysql": Dialect.MYSQL,
    "tsql": Dialect.MSSQL,
    "sqlite": Dialect.SQLITE,
}


def _load_cases() -> list[dict]:
    data = yaml.safe_load(CORPUS.read_text(encoding="utf-8"))
    expanded = []
    for case in data["cases"]:
        for dialect in case["dialects"]:
            expanded.append({**case, "dialect": dialect})
    return expanded


CASES = _load_cases()


def test_corpus_is_large_enough():
    unique = {c["id"] for c in CASES}
    assert len(unique) >= 100, "the adversarial corpus must keep at least 100 distinct cases"
    assert sum(1 for c in CASES if c["expect"] == "deny") >= 100
    assert sum(1 for c in CASES if c["expect"] == "allow") >= 40


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"{c['id']}[{c['dialect']}]")
def test_policy_case(case: dict):
    if case.get("skip_reason"):
        pytest.skip(case["skip_reason"])
    dialect = _DIALECTS[case["dialect"]]
    ctx = make_ctx(dialect, level=PolicyLevel(case.get("level", "standard")))
    decision = evaluate(case["sql"], ctx)
    expected = Decision(case["expect"])
    assert decision.decision is expected, (
        f"{case['id']} ({case['dialect']}, {case['category']}): expected {expected.value}, "
        f"got {decision.decision.value} - {decision.reason}"
    )
    if expected is Decision.DENY:
        assert decision.transformed_sql is None or decision.decision is not Decision.ALLOW
    if case.get("reason_contains"):
        assert case["reason_contains"].lower() in decision.reason.lower()
    if case.get("transformed_contains"):
        assert case["transformed_contains"] in (decision.transformed_sql or "")
    if expected is Decision.ALLOW:
        assert decision.fingerprint and decision.transformed_sql
        assert decision.legacy_agrees is True


def test_unsafe_cases_never_produce_execution_sql():
    """Regardless of expectation, a denied decision never carries SQL meant for execution."""
    for case in CASES:
        d = evaluate(case["sql"], make_ctx(_DIALECTS[case["dialect"]]))
        if d.decision is Decision.DENY:
            assert d.transformed_sql is None, case["id"]
