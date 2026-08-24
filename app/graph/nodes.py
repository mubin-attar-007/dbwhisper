"""The graph's nodes. Each one does a single thing and records what it did.

Every node follows the same discipline:

* it returns a *partial* state update, never a whole state;
* it appends exactly one :class:`~app.graph.state.RunStep` describing what happened, including on
  failure, so a blocked run is as traceable as a successful one;
* it never opens a database connection or constructs a provider itself - those come from
  :class:`~app.graph.deps.GraphDeps`.

The model is optional throughout. If the router has nothing to offer, ``understand`` degrades to a
deterministic classification and ``summarize`` degrades to statistics; only ``generate`` genuinely
requires a model, and its absence is reported as a blocked run with an actionable message.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.analysis.charts import choose_chart
from app.analysis.shape import verify_shape
from app.analysis.summary import (
    SUMMARY_PROMPT_VERSION,
    SUMMARY_SCHEMA,
    build_summary_prompt,
    deterministic_summary,
    sample_rows_allowed,
    summary_from_payload,
)
from app.execution.results import ResultFrame, ResultStats, compute_stats
from app.execution.service import ExecutionRequest, execute
from app.graph.deps import GraphDeps
from app.graph.prompts import (
    GENERATE_SCHEMA,
    PROMPT_VERSIONS,
    REPAIR_SYSTEM,
    UNDERSTAND_SCHEMA,
    UNDERSTAND_SYSTEM,
    generate_prompt,
    generate_system,
    repair_prompt,
    understand_prompt,
)
from app.graph.state import QueryState, RunStatus, RunStep, StepStatus
from app.llm.types import ModelCapability, ModelRequest, ProviderError
from app.retrieval.context_pack import build_context_pack
from app.retrieval.hybrid import RetrievalRequest, analyze_query, retrieve
from app.sqlpolicy.types import Dialect

logger = logging.getLogger(__name__)

#: Database failures worth one more attempt: the model can plausibly fix these from the message.
REPAIRABLE_CATEGORIES = frozenset({"syntax", "unknown_object", "data"})


class EmptySummaryError(RuntimeError):
    """The model returned nothing usable, so the deterministic summary takes over."""


def _step(name: str, status: StepStatus, started: float, detail: str = "", **data: Any) -> dict:
    return RunStep(
        name=name,
        status=status,
        duration_ms=(time.perf_counter() - started) * 1000,
        detail=detail,
        data=data,
    ).as_dict()


def _blocked(name: str, started: float, message: str, category: str = "blocked") -> QueryState:
    return QueryState(
        status=RunStatus.BLOCKED.value,
        error=message,
        error_category=category,
        steps=[_step(name, StepStatus.FAILED, started, message)],
    )


# ---------------------------------------------------------------------------------------------
# 1. Retrieval
# ---------------------------------------------------------------------------------------------


def _credit_verified_examples(deps: GraphDeps, pair_ids: list[str]) -> None:
    """Report the verified pairs that were actually put in front of the model.

    Reporting, not recording: the sink is injected, so the graph does not open a database session of
    its own. That is the same rule that keeps credentials out of checkpointed state, and an
    import-linter contract enforces it - see [tool.importlinter] in pyproject.toml.

    Best effort. A usage counter must never be the reason a query fails.
    """
    if not pair_ids or deps.on_examples_used is None:
        return
    try:
        deps.on_examples_used(list(pair_ids))
    except Exception as exc:  # pragma: no cover - telemetry must not break a query
        logger.debug("Could not report verified-example usage: %s", exc)


def make_retrieve_node(deps: GraphDeps):
    def node(state: QueryState) -> QueryState:
        started = time.perf_counter()
        question = state["question"]
        if state.get("clarification_answer"):
            question = f"{question} ({state['clarification_answer']})"

        try:
            target = deps.resolve_source(state["source_id"])
        except KeyError:
            return _blocked(
                "retrieve",
                started,
                f"Unknown database '{state['source_id']}'. Enroll it before querying.",
                "unknown_source",
            )

        embedding = None
        try:
            embedding = deps.embedder.embed_query(question)
        except Exception as exc:
            # Lexical retrieval alone is degraded but usable; a dead embedder must not fail the run.
            logger.warning("Embedding unavailable, falling back to lexical retrieval: %s", exc)

        result = retrieve(
            deps.index,
            RetrievalRequest(
                question=question,
                source_id=state["source_id"],
                snapshot_id=state.get("snapshot_id") or target.snapshot_id,
                k=deps.retrieval_k,
                embedding=embedding,
            ),
        )
        pack = build_context_pack(result.hits, budget_tokens=deps.context_budget_tokens)
        _credit_verified_examples(deps, pack.example_pair_ids)

        if not pack.tables:
            return _blocked(
                "retrieve",
                started,
                "No tables in this database matched the question. It may not be enrolled, or the "
                "question may be about data this database does not hold.",
                "no_context",
            )

        return QueryState(
            context=pack.render(),
            context_summary=pack.as_dict(),
            retrieval_evidence=result.evidence.as_dict(),
            tables=pack.table_names,
            dialect=target.dialect.value,
            snapshot_id=state.get("snapshot_id") or target.snapshot_id,
            steps=[
                _step(
                    "retrieve",
                    StepStatus.OK,
                    started,
                    f"{len(pack.tables)} table(s) selected",
                    tables=pack.table_names,
                    tokens=pack.token_estimate,
                    embedding_used=embedding is not None,
                )
            ],
        )

    return node


# ---------------------------------------------------------------------------------------------
# 2. Understanding
# ---------------------------------------------------------------------------------------------


def _deterministic_intent(question: str) -> dict[str, Any]:
    """A usable interpretation without a model, so the graph never depends on one to start."""
    analysis = analyze_query(question)
    lowered = question.lower()
    if analysis.has_time_dimension and any(w in lowered for w in ("trend", "over time", "monthly")):
        intent_type = "trend"
    elif any(w in lowered for w in ("top ", "bottom ", "rank", "most", "least", "best", "worst")):
        intent_type = "ranking"
    elif any(w in lowered for w in ("compare", "versus", " vs ", "difference")):
        intent_type = "comparison"
    elif any(w in lowered for w in ("how many", "total", "sum", "average", "count")):
        intent_type = "aggregation"
    elif any(w in lowered for w in ("why", "explain", "cause", "driver")):
        intent_type = "diagnostic"
    else:
        intent_type = "lookup"
    return {
        "intent_type": intent_type,
        "metrics": analysis.metric_terms[:5],
        "dimensions": [],
        "named_entities": analysis.identifiers[:8],
        "requires_clarification": False,
        "source": "deterministic",
    }


def make_understand_node(deps: GraphDeps):
    def node(state: QueryState) -> QueryState:
        started = time.perf_counter()
        question = state["question"]

        if deps.skip_understanding:
            intent = _deterministic_intent(question)
            return QueryState(
                intent=intent,
                steps=[_step("understand", StepStatus.SKIPPED, started, "understanding disabled")],
            )

        request = ModelRequest(
            system=UNDERSTAND_SYSTEM,
            user=understand_prompt(question, state.get("context", "")),
            capabilities=frozenset(
                {ModelCapability.STRUCTURED_JSON, ModelCapability.CLASSIFICATION}
            ),
            json_schema=UNDERSTAND_SCHEMA,
            egress_policy=deps.egress_policy,
            prompt_name="understand",
            prompt_version=PROMPT_VERSIONS["understand"],
        )
        try:
            response, routing = deps.router.complete(request)
        except ProviderError as exc:
            intent = _deterministic_intent(question)
            return QueryState(
                intent=intent,
                steps=[
                    _step(
                        "understand",
                        StepStatus.SKIPPED,
                        started,
                        f"no model available ({exc.kind.value}); used deterministic classification",
                    )
                ],
            )

        intent = dict(response.parsed or {})
        intent["source"] = response.profile
        needs = bool(intent.get("requires_clarification")) and bool(
            intent.get("clarification_question")
        )
        already_answered = bool(state.get("clarification_answer"))

        return QueryState(
            intent=intent,
            assumptions=[str(a) for a in (intent.get("assumptions") or [])],
            clarification_question=(
                str(intent["clarification_question"]) if needs and not already_answered else None
            ),
            routing=[{"node": "understand", **_routing(routing, response)}],
            versions={"understand": PROMPT_VERSIONS["understand"]},
            steps=[
                _step(
                    "understand",
                    StepStatus.OK,
                    started,
                    f"intent={intent.get('intent_type')}",
                    clarification=bool(needs and not already_answered),
                    profile=response.profile,
                )
            ],
        )

    return node


def _routing(routing: Any, response: Any) -> dict[str, Any]:
    return {
        "profile": routing.chosen,
        "provider": response.provider,
        "model": response.model,
        "fallback": routing.fallback_used,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "latency_ms": round(response.latency_ms, 2),
        "repairs": response.repair_attempts,
    }


# ---------------------------------------------------------------------------------------------
# 3. Clarification (human-in-the-loop)
# ---------------------------------------------------------------------------------------------


def make_clarify_node(deps: GraphDeps):
    def node(state: QueryState) -> QueryState:
        started = time.perf_counter()
        question = state.get("clarification_question")
        if not question:
            return QueryState(steps=[_step("clarify", StepStatus.SKIPPED, started, "not needed")])

        from langgraph.types import interrupt

        answer = interrupt(
            {
                "type": "clarification",
                "question": question,
                "original_question": state["question"],
                "tables": state.get("tables", []),
            }
        )
        return QueryState(
            clarification_answer=str(answer),
            clarification_question=None,
            steps=[_step("clarify", StepStatus.OK, started, "answered by the user")],
        )

    return node


# ---------------------------------------------------------------------------------------------
# 4. SQL generation
# ---------------------------------------------------------------------------------------------


def make_generate_node(deps: GraphDeps):
    def node(state: QueryState) -> QueryState:
        started = time.perf_counter()
        dialect = state.get("dialect", "generic")
        attempts = int(state.get("generation_attempts", 0))
        repairing = bool(state.get("policy_error")) and attempts > 0

        if repairing:
            system = REPAIR_SYSTEM
            user = repair_prompt(
                state["question"],
                state.get("context", ""),
                state.get("sql", ""),
                state.get("policy_error") or "",
                dialect=dialect,
            )
            prompt_name, version = "repair_sql", PROMPT_VERSIONS["repair_sql"]
        else:
            system = generate_system(dialect)
            clarification = None
            if state.get("clarification_answer"):
                clarification = (
                    state.get("clarification_question") or "clarification",
                    state["clarification_answer"],
                )
            user = generate_prompt(
                state["question"],
                state.get("context", ""),
                intent=state.get("intent"),
                clarification=clarification,
            )
            prompt_name, version = "generate_sql", PROMPT_VERSIONS["generate_sql"]

        request = ModelRequest(
            system=system,
            user=user,
            capabilities=frozenset(
                {ModelCapability.SQL_GENERATION, ModelCapability.STRUCTURED_JSON}
            ),
            json_schema=GENERATE_SCHEMA,
            temperature=0.0 if repairing else None,
            egress_policy=deps.egress_policy,
            prompt_name=prompt_name,
            prompt_version=version,
        )
        try:
            response, routing = deps.router.complete(request)
        except ProviderError as exc:
            return _blocked(
                "generate",
                started,
                f"No model could write the query: {exc}",
                "model_unavailable",
            )

        payload = dict(response.parsed or {})
        sql = str(payload.get("sql") or "").strip()
        if not sql:
            return _blocked("generate", started, "The model returned an empty query.", "empty_sql")

        return QueryState(
            sql=sql,
            rationale=str(payload.get("rationale") or ""),
            follow_ups=[str(f) for f in (payload.get("follow_ups") or [])][:3],
            assumptions=[
                *(state.get("assumptions") or []),
                *[str(a) for a in (payload.get("assumptions") or [])],
            ],
            generation_attempts=attempts + 1,
            policy_error=None,
            routing=[{"node": prompt_name, **_routing(routing, response)}],
            versions={prompt_name: version},
            steps=[
                _step(
                    "repair" if repairing else "generate",
                    StepStatus.OK,
                    started,
                    f"{len(sql)} chars",
                    profile=response.profile,
                    attempt=attempts + 1,
                )
            ],
        )

    return node


# ---------------------------------------------------------------------------------------------
# 5. Policy validation (no database contact)
# ---------------------------------------------------------------------------------------------


def make_validate_node(deps: GraphDeps):
    from app.sqlpolicy import Decision, PolicyContext, evaluate
    from app.sqlpolicy.scope_loader import scope_from_schema_index

    def node(state: QueryState) -> QueryState:
        started = time.perf_counter()
        sql = state.get("edited_sql") or state.get("sql", "")
        dialect = Dialect(state.get("dialect", "generic"))

        try:
            target = deps.resolve_source(state["source_id"])
        except KeyError:
            return _blocked("validate", started, "Unknown database.", "unknown_source")

        decision = evaluate(
            sql,
            PolicyContext(
                dialect=dialect,
                scope=scope_from_schema_index(state["source_id"], dialect),
                level=deps.policy_level,
                default_row_limit=target.max_rows,
                inject_limit=True,
                require_scope=True,
            ),
        )
        policy = decision.as_legacy_dict()
        repairs = int(state.get("repair_attempts", 0))

        if decision.decision is Decision.DENY:
            can_repair = repairs < deps.max_repairs and _is_repairable(decision.reason)
            return QueryState(
                policy=policy,
                policy_error=decision.reason,
                repair_attempts=repairs + 1 if can_repair else repairs,
                status=RunStatus.RUNNING.value if can_repair else RunStatus.BLOCKED.value,
                error=None if can_repair else decision.reason,
                error_category=None if can_repair else "policy",
                steps=[
                    _step(
                        "validate",
                        StepStatus.FAILED,
                        started,
                        decision.reason,
                        will_repair=can_repair,
                        policy_version=decision.policy_version,
                    )
                ],
            )

        # Already-approved runs must not be asked again - that is what makes the edited-SQL path
        # terminate, and what lets the evidence checklist record that a human did approve.
        needs_approval = (
            decision.decision is Decision.NEEDS_APPROVAL or bool(state.get("plan_only"))
        ) and not state.get("approved")
        reason = (
            decision.reason
            if decision.decision is Decision.NEEDS_APPROVAL
            else "Plan-only mode: review the query before it runs."
        )
        return QueryState(
            policy=policy,
            policy_error=None,
            sql=sql,
            tables=[str(t) for t in decision.tables] or state.get("tables", []),
            approval_required=needs_approval,
            approval_reason=reason if needs_approval else "",
            versions={"sql_policy": decision.policy_version},
            steps=[
                _step(
                    "validate",
                    StepStatus.OK,
                    started,
                    decision.decision.value,
                    tables=[str(t) for t in decision.tables],
                    limit_applied=decision.limit_applied,
                    policy_version=decision.policy_version,
                )
            ],
        )

    return node


def _is_repairable(reason: str) -> bool:
    """Only mistakes are repairable. A policy refusal is a decision, not an error to retry."""
    lowered = reason.lower()
    repairable_signals = ("parse error", "could not be resolved", "unknown or unauthorized")
    refusal_signals = (
        "not permitted",
        "system catalog",
        "multiple statements",
        "cross-database",
        "blocked function",
        "sensitive",
    )
    if any(signal in lowered for signal in refusal_signals):
        return False
    return any(signal in lowered for signal in repairable_signals)


# ---------------------------------------------------------------------------------------------
# 6. Approval (human-in-the-loop)
# ---------------------------------------------------------------------------------------------


def _audit_approval(
    *, granted: bool, fingerprint: str, state: QueryState, detail: dict[str, Any]
) -> None:
    """Record a human's decision on a statement that needed one.

    A human approving SQL against production data is the moment the system stops being automated,
    and it is exactly what an incident review asks about afterwards. The *fingerprint* identifies
    the statement without copying it, so the trail names what was approved without becoming a
    second store of query text.

    No actor is recorded: this node runs inside the graph and has no request to attribute it to.
    That is a stated limit rather than a guess - see docs/v2/THREAT_MODEL.md.
    """
    try:
        from app.platform.audit import AuditAction, AuditOutcome, record

        record(
            AuditAction.QUERY_APPROVAL_GRANTED if granted else AuditAction.QUERY_APPROVAL_REJECTED,
            subject=fingerprint,
            outcome=AuditOutcome.SUCCESS if granted else AuditOutcome.DENIED,
            reason=str(state.get("approval_reason") or ""),
            detail={"source_id": state.get("source_id"), **detail},
        )
    except Exception:  # pragma: no cover - auditing must never break a run
        logger.debug("Could not audit an approval decision", exc_info=True)


def make_approve_node(deps: GraphDeps):
    def node(state: QueryState) -> QueryState:
        started = time.perf_counter()
        if not state.get("approval_required"):
            return QueryState(steps=[_step("approve", StepStatus.SKIPPED, started, "not required")])

        from langgraph.types import interrupt

        policy = state.get("policy") or {}
        answer = interrupt(
            {
                "type": "approval",
                "reason": state.get("approval_reason", ""),
                "sql": state.get("sql", ""),
                "tables": state.get("tables", []),
                "fingerprint": policy.get("fingerprint"),
                "rationale": state.get("rationale", ""),
            }
        )

        if isinstance(answer, dict):
            approved = bool(answer.get("approved"))
            edited = answer.get("sql")
        else:
            approved = bool(answer) and str(answer).lower() not in {"no", "false", "reject"}
            edited = None

        fingerprint = str(policy.get("fingerprint") or state.get("source_id") or "")

        if not approved:
            _audit_approval(
                granted=False,
                fingerprint=fingerprint,
                state=state,
                detail={"tables": state.get("tables", [])},
            )
            return QueryState(
                approved=False,
                status=RunStatus.BLOCKED.value,
                error="The query was not approved, so it did not run.",
                error_category="rejected",
                steps=[_step("approve", StepStatus.OK, started, "rejected by the user")],
            )

        _audit_approval(
            granted=True,
            fingerprint=fingerprint,
            state=state,
            detail={"edited": bool(edited and str(edited).strip())},
        )

        if edited and str(edited).strip() and str(edited).strip() != state.get("sql"):
            # Edited SQL is a new statement: clear the approval so it is validated again from scratch.
            return QueryState(
                approved=True,
                edited_sql=str(edited).strip(),
                approved_fingerprint=None,
                steps=[
                    _step("approve", StepStatus.OK, started, "approved with edits; re-validating")
                ],
            )

        return QueryState(
            approved=True,
            approved_fingerprint=policy.get("fingerprint"),
            steps=[_step("approve", StepStatus.OK, started, "approved")],
        )

    return node


# ---------------------------------------------------------------------------------------------
# 7. Execution
# ---------------------------------------------------------------------------------------------


def make_execute_node(deps: GraphDeps):
    def node(state: QueryState) -> QueryState:
        started = time.perf_counter()
        try:
            target = deps.resolve_source(state["source_id"])
        except KeyError:
            return _blocked("execute", started, "Unknown database.", "unknown_source")

        result = execute(
            ExecutionRequest(
                sql=state.get("sql", ""),
                connection_string=target.connection_string,
                dialect=target.dialect,
                db_flag=state["source_id"],
                max_rows=target.max_rows,
                timeout_seconds=target.timeout_seconds,
                policy_level=deps.policy_level,
                network_level=deps.network_level,
                network_allowlist=deps.network_allowlist,
                bundled_hosts=deps.bundled_hosts,
                approved_fingerprint=state.get("approved_fingerprint"),
            )
        )

        if not result.success:
            repairs = int(state.get("repair_attempts", 0))
            can_repair = (
                repairs < deps.max_repairs and result.error_category in REPAIRABLE_CATEGORIES
            )
            return QueryState(
                policy_error=result.error if can_repair else None,
                repair_attempts=repairs + 1 if can_repair else repairs,
                status=RunStatus.RUNNING.value if can_repair else RunStatus.FAILED.value,
                error=None if can_repair else result.error,
                error_category=result.error_category,
                steps=[
                    _step(
                        "execute",
                        StepStatus.FAILED,
                        started,
                        result.error or "",
                        category=result.error_category,
                        will_repair=can_repair,
                    )
                ],
            )

        frame = result.frame
        return QueryState(
            columns=frame.columns,
            rows=[list(row) for row in frame.rows],
            row_count=frame.row_count,
            truncated=frame.truncated,
            execution_ms=frame.duration_ms or 0.0,
            executed_sql=result.executed_sql,
            read_only_enforced=result.read_only_enforced,
            stats=result.stats.as_dict(),
            describe_text=result.stats.describe_text(),
            policy_error=None,
            steps=[
                _step(
                    "execute",
                    StepStatus.OK,
                    started,
                    f"{frame.row_count} row(s)",
                    truncated=frame.truncated,
                    read_only_enforced=result.read_only_enforced,
                )
            ],
        )

    return node


# ---------------------------------------------------------------------------------------------
# 8. Verification and presentation
# ---------------------------------------------------------------------------------------------


def _rebuild_frame(state: QueryState) -> tuple[ResultFrame, ResultStats]:
    frame = ResultFrame(
        columns=list(state.get("columns") or []),
        rows=[tuple(row) for row in (state.get("rows") or [])],
        truncated=bool(state.get("truncated")),
        duration_ms=state.get("execution_ms"),
    )
    return frame, compute_stats(frame)


def make_verify_node(deps: GraphDeps):
    def node(state: QueryState) -> QueryState:
        started = time.perf_counter()
        frame, stats = _rebuild_frame(state)
        intent = state.get("intent") or {}
        report = verify_shape(
            question=state["question"],
            frame=frame,
            stats=stats,
            plan_limit=intent.get("requested_limit"),
            expected_dimensions=[str(d) for d in (intent.get("dimensions") or [])],
        )
        chart = choose_chart(frame, stats)
        return QueryState(
            shape=report.as_dict(),
            chart=chart.as_dict(),
            steps=[
                _step(
                    "verify",
                    StepStatus.OK if report.ok else StepStatus.FAILED,
                    started,
                    "shape verified" if report.ok else "; ".join(w.code for w in report.warnings),
                    chart=chart.chart.value,
                )
            ],
        )

    return node


def make_summarize_node(deps: GraphDeps):
    def node(state: QueryState) -> QueryState:
        started = time.perf_counter()
        frame, stats = _rebuild_frame(state)
        from app.analysis.shape import Severity, ShapeFinding, ShapeReport

        raw_shape = state.get("shape") or {}
        shape = ShapeReport(
            findings=[
                ShapeFinding(
                    f["code"],
                    Severity(f["severity"]),
                    f["message"],
                    f.get("expected"),
                    f.get("actual"),
                )
                for f in raw_shape.get("findings", [])
            ],
            checks_run=raw_shape.get("checks_run", []),
        )
        fallback = deterministic_summary(
            question=state["question"], frame=frame, stats=stats, shape=shape
        )

        try:
            candidates, _ = deps.router.candidates(
                {ModelCapability.SUMMARIZATION, ModelCapability.STRUCTURED_JSON},
                deps.egress_policy,
            )
            provider_is_local = bool(candidates) and candidates[0].is_local
            request = ModelRequest(
                system=(
                    "You summarise a query result for a business reader. Every number you state "
                    "must appear in the material provided. Never claim a cause."
                ),
                user=build_summary_prompt(
                    question=state["question"],
                    sql=state.get("sql", ""),
                    stats=stats,
                    frame=frame,
                    shape=shape,
                    sample_rows=sample_rows_allowed(
                        deps.egress_policy, provider_is_local=provider_is_local
                    ),
                ),
                capabilities=frozenset(
                    {ModelCapability.SUMMARIZATION, ModelCapability.STRUCTURED_JSON}
                ),
                json_schema=SUMMARY_SCHEMA,
                egress_policy=deps.egress_policy,
                prompt_name="summarize",
                prompt_version=SUMMARY_PROMPT_VERSION,
            )
            response, routing = deps.router.complete(request)
            summary = summary_from_payload(response.parsed or {}, grounded_by=response.profile)
            if not summary.answer:
                raise EmptySummaryError("the model returned an empty summary")
            follow_ups = summary.follow_ups or state.get("follow_ups") or []
            return QueryState(
                summary=summary.as_dict(),
                follow_ups=[str(f) for f in follow_ups][:3],
                routing=[{"node": "summarize", **_routing(routing, response)}],
                versions={"summarize": SUMMARY_PROMPT_VERSION},
                steps=[
                    _step("summarize", StepStatus.OK, started, "grounded", profile=response.profile)
                ],
            )
        except Exception as exc:
            return QueryState(
                summary=fallback.as_dict(),
                steps=[
                    _step(
                        "summarize",
                        StepStatus.OK,
                        started,
                        f"deterministic fallback ({type(exc).__name__})",
                    )
                ],
            )

    return node


def make_finalize_node(deps: GraphDeps):
    from app.graph.state import evidence_checklist

    def node(state: QueryState) -> QueryState:
        started = time.perf_counter()
        return QueryState(
            status=RunStatus.COMPLETED.value,
            evidence=evidence_checklist(state),
            steps=[_step("finalize", StepStatus.OK, started, "run complete")],
        )

    return node


__all__ = [
    "REPAIRABLE_CATEGORIES",
    "EmptySummaryError",
    "make_approve_node",
    "make_clarify_node",
    "make_execute_node",
    "make_finalize_node",
    "make_generate_node",
    "make_retrieve_node",
    "make_summarize_node",
    "make_understand_node",
    "make_validate_node",
    "make_verify_node",
]
