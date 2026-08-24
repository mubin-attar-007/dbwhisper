"""The Quick Query graph: one controlled LangGraph workflow, not a swarm of agents.

```
retrieve -> understand -> clarify? -> generate -> validate -> approve? -> execute -> verify -> summarize -> finalize
                                          ^           |                      |
                                          +-- repair -+                      +-- repair --+
```

Three design choices are worth stating plainly:

* **Retrieval runs before understanding.** Classifying a question is far more accurate when the model
  can see which tables actually exist, and it means a question about data this database does not hold
  is caught before any model call is spent on it.
* **Repair re-enters validation.** Corrected SQL is not trusted because it came from a repair; it goes
  through the identical policy path. The loop is bounded, and only *mistakes* are repaired - a policy
  refusal ends the run.
* **Interrupts are real.** Clarification and approval use LangGraph's ``interrupt`` with a durable
  checkpointer, so a run genuinely pauses, survives a restart, and resumes with the user's answer.
"""

from __future__ import annotations

from typing import Any, Literal

from langgraph.graph import END, START, StateGraph

from app.graph.deps import GraphDeps
from app.graph.nodes import (
    make_approve_node,
    make_clarify_node,
    make_execute_node,
    make_finalize_node,
    make_generate_node,
    make_retrieve_node,
    make_summarize_node,
    make_understand_node,
    make_validate_node,
    make_verify_node,
)
from app.graph.state import QueryState, RunStatus

GRAPH_VERSION = "query_graph@2.0"


def _halted(state: QueryState) -> bool:
    return RunStatus(state.get("status", RunStatus.RUNNING.value)).is_terminal


def _after_retrieve(state: QueryState) -> Literal["understand", "__end__"]:
    return END if _halted(state) else "understand"


def _after_understand(state: QueryState) -> Literal["clarify", "generate"]:
    return "clarify" if state.get("clarification_question") else "generate"


def _after_generate(state: QueryState) -> Literal["validate", "__end__"]:
    return END if _halted(state) else "validate"


def _after_validate(state: QueryState) -> Literal["generate", "approve", "execute", "__end__"]:
    if _halted(state):
        return END
    if state.get("policy_error"):
        return "generate"  # bounded repair; the validator already decided it is worth retrying
    return "approve" if state.get("approval_required") else "execute"


def _after_approve(state: QueryState) -> Literal["validate", "execute", "__end__"]:
    if _halted(state):
        return END
    # Edited SQL must be validated again before it can run.
    return "validate" if state.get("edited_sql") else "execute"


def _after_execute(state: QueryState) -> Literal["generate", "verify", "__end__"]:
    if state.get("policy_error"):
        return "generate"
    return END if _halted(state) else "verify"


def build_query_graph(deps: GraphDeps) -> StateGraph:
    """Wire the nodes. Compile it yourself with a checkpointer, or use :func:`compile_query_graph`."""
    graph = StateGraph(QueryState)

    graph.add_node("retrieve", make_retrieve_node(deps))
    graph.add_node("understand", make_understand_node(deps))
    graph.add_node("clarify", make_clarify_node(deps))
    graph.add_node("generate", make_generate_node(deps))
    graph.add_node("validate", make_validate_node(deps))
    graph.add_node("approve", make_approve_node(deps))
    graph.add_node("execute", make_execute_node(deps))
    graph.add_node("verify", make_verify_node(deps))
    graph.add_node("summarize", make_summarize_node(deps))
    graph.add_node("finalize", make_finalize_node(deps))

    graph.add_edge(START, "retrieve")
    graph.add_conditional_edges("retrieve", _after_retrieve, {"understand": "understand", END: END})
    graph.add_conditional_edges(
        "understand", _after_understand, {"clarify": "clarify", "generate": "generate"}
    )
    graph.add_edge("clarify", "generate")
    graph.add_conditional_edges("generate", _after_generate, {"validate": "validate", END: END})
    graph.add_conditional_edges(
        "validate",
        _after_validate,
        {"generate": "generate", "approve": "approve", "execute": "execute", END: END},
    )
    graph.add_conditional_edges(
        "approve", _after_approve, {"validate": "validate", "execute": "execute", END: END}
    )
    graph.add_conditional_edges(
        "execute", _after_execute, {"generate": "generate", "verify": "verify", END: END}
    )
    graph.add_edge("verify", "summarize")
    graph.add_edge("summarize", "finalize")
    graph.add_edge("finalize", END)
    return graph


def compile_query_graph(deps: GraphDeps, checkpointer: Any = None) -> Any:
    """Compile with a checkpointer. Without one, interrupts cannot resume - so tests pass one too."""
    return build_query_graph(deps).compile(checkpointer=checkpointer)


def answer_payload(state: QueryState) -> dict[str, Any]:
    """The API-facing view of a finished (or halted) run."""
    summary = state.get("summary") or {}
    return {
        "status": state.get("status"),
        "question": state.get("question"),
        "sql": state.get("sql"),
        "executed_sql": state.get("executed_sql"),
        "columns": state.get("columns") or [],
        "rows": state.get("rows") or [],
        "row_count": state.get("row_count"),
        "truncated": state.get("truncated"),
        "tables": state.get("tables") or [],
        "answer": summary.get("answer"),
        "observations": summary.get("observations") or [],
        "caveats": summary.get("caveats") or [],
        "follow_ups": state.get("follow_ups") or [],
        "assumptions": state.get("assumptions") or [],
        "chart": state.get("chart"),
        "shape": state.get("shape"),
        "evidence": state.get("evidence"),
        "policy": state.get("policy"),
        "retrieval": state.get("retrieval_evidence"),
        "routing": state.get("routing") or [],
        "versions": {**(state.get("versions") or {}), "graph": GRAPH_VERSION},
        "steps": state.get("steps") or [],
        "error": state.get("error"),
        "error_category": state.get("error_category"),
    }


def pending_interrupt(result: dict[str, Any]) -> dict[str, Any] | None:
    """The interrupt payload from an invoke result, or ``None`` if the run did not pause."""
    interrupts = result.get("__interrupt__") if isinstance(result, dict) else None
    if not interrupts:
        return None
    first = interrupts[0]
    value = getattr(first, "value", first)
    return dict(value) if isinstance(value, dict) else {"value": value}


__all__ = [
    "GRAPH_VERSION",
    "answer_payload",
    "build_query_graph",
    "compile_query_graph",
    "pending_interrupt",
]
