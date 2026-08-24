"""What one evaluation case produced, recorded once and scored many times.

The runner writes a :class:`CaseOutcome` per case; every metric in :mod:`app.evaluation.metrics` is a
pure function of a list of them, and the report renders them. Keeping the record separate from both
the producer and the consumers means a stored run can be re-scored after a metric is fixed - which is
exactly the capability the v1 harness lacked when its scoring predicate turned out to be wrong
(``docs/v2/CLAIM_AUDIT.md`` §4.4(c)).

Two fields deserve their own note:

* ``observed_behavior`` is what the pipeline actually did, derived from the terminal run state -
  never from what the case hoped for. Scoring compares it to ``expected_behavior``.
* ``unsafe_execution`` is a *fact about the run*, not a judgement: it is true only when rows were
  actually fetched for a case whose contract says the request must be declined. A run that failed
  for an unrelated reason is not an unsafe execution, and a refusal that happened for the wrong
  reason is still not one - it is a taxonomy question.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.evaluation.schema import ExpectedBehavior


class ObservedBehavior(StrEnum):
    """What the pipeline did with the question."""

    #: SQL was generated, passed policy, ran, and returned a result frame.
    ANSWERED = "answered"
    #: The run paused to ask the user something.
    CLARIFIED = "clarified"
    #: The run stopped deliberately - policy denial, unknown source, out of scope.
    REFUSED = "refused"
    #: The run stopped because something broke (model unavailable, database error, crash).
    ERRORED = "errored"

    @property
    def is_terminalish(self) -> bool:
        return self is not ObservedBehavior.CLARIFIED


@dataclass(slots=True)
class RetrievalScore:
    """How much of the reference query's schema the retriever actually surfaced."""

    expected_tables: tuple[str, ...] = ()
    retrieved_tables: tuple[str, ...] = ()
    expected_columns: tuple[str, ...] = ()
    retrieved_columns: tuple[str, ...] = ()
    expected_relationships: tuple[str, ...] = ()
    retrieved_relationships: tuple[str, ...] = ()
    #: Ranked document ids as returned by the retriever, for MRR.
    ranked_document_ids: tuple[str, ...] = ()
    #: 1-based rank of the first document belonging to an expected table, or ``None``.
    first_relevant_rank: int | None = None
    k: int = 0

    def _hits(self, expected: tuple[str, ...], retrieved: tuple[str, ...]) -> int:
        got = {v.lower() for v in retrieved}
        return sum(1 for v in expected if v.lower() in got)

    @property
    def table_hits(self) -> int:
        return self._hits(self.expected_tables, self.retrieved_tables)

    @property
    def column_hits(self) -> int:
        return self._hits(self.expected_columns, self.retrieved_columns)

    @property
    def relationship_hits(self) -> int:
        return self._hits(self.expected_relationships, self.retrieved_relationships)

    @property
    def reciprocal_rank(self) -> float:
        return 1.0 / self.first_relevant_rank if self.first_relevant_rank else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "k": self.k,
            "expected_tables": list(self.expected_tables),
            "retrieved_tables": list(self.retrieved_tables),
            "table_hits": self.table_hits,
            "column_hits": self.column_hits,
            "expected_columns": len(self.expected_columns),
            "relationship_hits": self.relationship_hits,
            "expected_relationships": len(self.expected_relationships),
            "first_relevant_rank": self.first_relevant_rank,
            "reciprocal_rank": round(self.reciprocal_rank, 4),
        }


@dataclass(slots=True)
class RunCost:
    """What the run spent. Latency is wall-clock for the whole graph invocation."""

    latency_ms: float = 0.0
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    fallback_used: bool = False
    repair_attempts: int = 0
    profiles_used: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "latency_ms": round(self.latency_ms, 2),
            "model_calls": self.model_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "fallback_used": self.fallback_used,
            "repair_attempts": self.repair_attempts,
            "profiles_used": list(self.profiles_used),
        }


@dataclass(slots=True)
class CaseOutcome:
    """One case's complete record. Everything a metric or a report needs is here."""

    case_id: str
    dataset: str
    question: str
    expected_behavior: ExpectedBehavior
    observed_behavior: ObservedBehavior
    tags: tuple[str, ...] = ()
    difficulty: str = "medium"

    # -- what the pipeline produced ------------------------------------------------------------
    run_status: str = ""
    generated_sql: str | None = None
    executed_sql: str | None = None
    sql_parsed: bool = False
    policy_decision: str | None = None
    policy_reason: str | None = None
    executed: bool = False
    row_count: int | None = None
    truncated: bool = False
    execution_error: str | None = None
    error_category: str | None = None
    clarification_question: str | None = None
    refusal_text: str | None = None

    # -- grounding -----------------------------------------------------------------------------
    hallucinated_tables: tuple[str, ...] = ()
    hallucinated_columns: tuple[str, ...] = ()
    retrieval: RetrievalScore = field(default_factory=RetrievalScore)

    # -- correctness ---------------------------------------------------------------------------
    #: ``None`` when there was nothing to compare (no gold SQL, or nothing executed).
    comparison: Any = None
    gold_row_count: int | None = None
    gold_sql_error: str | None = None

    # -- safety --------------------------------------------------------------------------------
    unsafe_execution: bool = False
    unsafe_reason: str = ""

    # -- classification ------------------------------------------------------------------------
    failure_category: str | None = None
    failure_detail: str = ""

    # -- cost ----------------------------------------------------------------------------------
    cost: RunCost = field(default_factory=RunCost)
    steps: tuple[str, ...] = ()
    versions: dict[str, str] = field(default_factory=dict)

    # -- derived -------------------------------------------------------------------------------
    @property
    def behaviour_matched(self) -> bool:
        """Did the pipeline do the *kind* of thing the case asked for?"""
        mapping = {
            ExpectedBehavior.ANSWER: ObservedBehavior.ANSWERED,
            ExpectedBehavior.CLARIFY: ObservedBehavior.CLARIFIED,
            ExpectedBehavior.REFUSE: ObservedBehavior.REFUSED,
        }
        return self.observed_behavior is mapping[self.expected_behavior]

    @property
    def result_matched(self) -> bool:
        """Execution accuracy for this case: the result set matched the reference."""
        return bool(getattr(self.comparison, "match", False))

    @property
    def set_only_matched(self) -> bool:
        """What a duplicate-collapsing set comparison would have said. Never the headline metric."""
        return bool(getattr(self.comparison, "set_match", False))

    @property
    def hallucinated(self) -> bool:
        return bool(self.hallucinated_tables or self.hallucinated_columns)

    def as_dict(self) -> dict[str, Any]:
        comparison = self.comparison.as_dict() if self.comparison is not None else None
        return {
            "case_id": self.case_id,
            "dataset": self.dataset,
            "question": self.question,
            "expected_behavior": self.expected_behavior.value,
            "observed_behavior": self.observed_behavior.value,
            "behaviour_matched": self.behaviour_matched,
            "tags": list(self.tags),
            "difficulty": self.difficulty,
            "run_status": self.run_status,
            "generated_sql": self.generated_sql,
            "executed_sql": self.executed_sql,
            "sql_parsed": self.sql_parsed,
            "policy_decision": self.policy_decision,
            "policy_reason": self.policy_reason,
            "executed": self.executed,
            "row_count": self.row_count,
            "truncated": self.truncated,
            "execution_error": self.execution_error,
            "error_category": self.error_category,
            "clarification_question": self.clarification_question,
            "refusal_text": self.refusal_text,
            "hallucinated_tables": list(self.hallucinated_tables),
            "hallucinated_columns": list(self.hallucinated_columns),
            "retrieval": self.retrieval.as_dict(),
            "comparison": comparison,
            "gold_row_count": self.gold_row_count,
            "gold_sql_error": self.gold_sql_error,
            "unsafe_execution": self.unsafe_execution,
            "unsafe_reason": self.unsafe_reason,
            "failure_category": self.failure_category,
            "failure_detail": self.failure_detail,
            "cost": self.cost.as_dict(),
            "steps": list(self.steps),
            "versions": dict(self.versions),
        }


__all__ = ["CaseOutcome", "ObservedBehavior", "RetrievalScore", "RunCost"]
