"""The state a query run carries through the graph.

Two constraints shape this:

* **It is checkpointed.** Every field is persisted after each node, so the state holds no secrets.
  There is no connection string here - only ``source_id``. The execution service resolves the
  credential at the moment it is needed, from the deps object the graph was built with.
* **It is the trace.** ``steps`` accumulates one record per node, which is what the run viewer
  renders and what an evaluation report reads. Nodes append to it rather than overwriting, so a
  resumed run keeps the history of what happened before the interrupt.
"""

from __future__ import annotations

import operator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Annotated, Any, TypedDict


def merge_versions(left: dict[str, str], right: dict[str, str]) -> dict[str, str]:
    """Reducer for ``versions``: later nodes add their prompt versions without erasing earlier ones."""
    return {**(left or {}), **(right or {})}


class RunStatus(StrEnum):
    RUNNING = "running"
    AWAITING_CLARIFICATION = "awaiting_clarification"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        return self in (RunStatus.COMPLETED, RunStatus.BLOCKED, RunStatus.FAILED)


class StepStatus(StrEnum):
    OK = "ok"
    SKIPPED = "skipped"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


@dataclass(slots=True)
class RunStep:
    """One node's contribution to the trace. Sanitized at write time, never after."""

    name: str
    status: StepStatus
    duration_ms: float = 0.0
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "duration_ms": round(self.duration_ms, 2),
            "detail": self.detail,
            "data": self.data,
        }


class QueryState(TypedDict, total=False):
    """LangGraph state for one Quick Query run."""

    # -- request ------------------------------------------------------------------------------
    question: str
    source_id: str
    snapshot_id: str | None
    dialect: str
    user_id: str | None
    tenant_id: str | None
    plan_only: bool

    # -- understanding ------------------------------------------------------------------------
    intent: dict[str, Any]
    clarification_question: str | None
    clarification_answer: str | None
    assumptions: list[str]

    # -- retrieval ----------------------------------------------------------------------------
    context: str
    context_summary: dict[str, Any]
    retrieval_evidence: dict[str, Any]
    tables: list[str]

    # -- generation ---------------------------------------------------------------------------
    sql: str
    rationale: str
    follow_ups: list[str]
    generation_attempts: int

    # -- policy -------------------------------------------------------------------------------
    policy: dict[str, Any]
    policy_error: str | None
    repair_attempts: int

    # -- approval -----------------------------------------------------------------------------
    approval_required: bool
    approval_reason: str
    approved: bool
    approved_fingerprint: str | None
    edited_sql: str | None

    # -- execution ----------------------------------------------------------------------------
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    execution_ms: float
    executed_sql: str | None
    read_only_enforced: bool

    # -- analysis -----------------------------------------------------------------------------
    stats: dict[str, Any]
    describe_text: str
    shape: dict[str, Any]
    chart: dict[str, Any]
    summary: dict[str, Any]

    # -- bookkeeping --------------------------------------------------------------------------
    status: str
    error: str | None
    error_category: str | None
    evidence: dict[str, Any]
    versions: Annotated[dict[str, str], merge_versions]
    routing: Annotated[list[dict[str, Any]], operator.add]
    steps: Annotated[list[dict[str, Any]], operator.add]


def initial_state(
    *,
    question: str,
    source_id: str,
    dialect: str = "generic",
    snapshot_id: str | None = None,
    user_id: str | None = None,
    tenant_id: str | None = None,
    plan_only: bool = False,
) -> QueryState:
    return QueryState(
        question=question,
        source_id=source_id,
        snapshot_id=snapshot_id,
        dialect=dialect,
        user_id=user_id,
        tenant_id=tenant_id,
        plan_only=plan_only,
        status=RunStatus.RUNNING.value,
        repair_attempts=0,
        generation_attempts=0,
        assumptions=[],
        follow_ups=[],
        tables=[],
        routing=[],
        steps=[],
        versions={},
        error=None,
    )


def evidence_checklist(state: QueryState) -> dict[str, bool | None]:
    """The trust signals shown next to an answer.

    A checklist rather than a confidence score: each item is a fact about what happened, which a
    reviewer can verify, instead of a number nobody can interpret. ``None`` means "not applicable
    to this run" and is rendered differently from ``False``.
    """
    policy = state.get("policy") or {}
    shape = state.get("shape") or {}
    summary = state.get("summary") or {}
    return {
        "schema_grounded": bool(state.get("tables")),
        "policy_passed": policy.get("decision") in {"allow", "needs_approval"},
        "read_only_enforced": state.get("read_only_enforced"),
        "result_shape_verified": shape.get("ok") if shape else None,
        "summary_grounded": (summary.get("grounded_by") is not None) if summary else None,
        "human_approved": state.get("approved") if state.get("approval_required") else None,
        "not_truncated": (not state.get("truncated"))
        if state.get("row_count") is not None
        else None,
    }


__all__ = [
    "QueryState",
    "RunStatus",
    "RunStep",
    "StepStatus",
    "evidence_checklist",
    "initial_state",
    "merge_versions",
]
