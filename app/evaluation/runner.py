"""Running the corpus through the real pipeline and recording what happened.

There is no evaluation-only code path here. Each case goes through
:func:`app.graph.query_graph.compile_query_graph` with a durable checkpointer, the same retrieval
index construction the API uses, the same policy engine reading a real ``schema_index.yaml``, and the
same execution service. The only substituted component is the model provider, and which one was used
is stamped on the provenance block.

Three properties this design buys, each of which the v1 harness lacked
(``docs/v2/CLAIM_AUDIT.md`` §4.4):

* **It runs from a clean checkout.** The fixtures are generated, the schema index is generated, the
  provider is offline. No laptop-local service, no DSN with a password in it, no port that has to be
  listening.
* **It re-scores without re-running.** Every case produces a :class:`~app.evaluation.outcomes.CaseOutcome`
  that is JSON-serialisable and complete; metrics are pure functions of a list of them. When a
  scoring predicate turns out to be wrong, the fix does not require another model run.
* **The safety invariant is checked twice.** A case whose contract is "decline" is unsafe if rows
  came back - and, independently of that, every statement that *did* execute is re-evaluated by the
  policy engine after the fact. If those two ever disagree, something has bypassed the gate.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver

from app.embeddings.fake import FakeEmbeddingProvider
from app.evaluation.datasets import get_spec
from app.evaluation.datasets.build import (
    build_index,
    connection_string,
    fixture_path,
    prepare,
    row_counts,
    run_readonly,
    snapshot_id,
)
from app.evaluation.datasets.spec import DatasetSpec
from app.evaluation.metrics import (
    Scorecard,
    clarification_matches,
    compare_result_sets,
    refusal_matches,
    score,
)
from app.evaluation.oracle import ORACLE_PROFILE_LABEL, OracleProvider
from app.evaluation.outcomes import CaseOutcome, ObservedBehavior, RetrievalScore, RunCost
from app.evaluation.provenance import Provenance
from app.evaluation.schema import EvalCase, EvalDataset, ExpectedBehavior, load_dataset
from app.evaluation.sqlfacts import sql_facts
from app.evaluation.taxonomy import classify
from app.graph.deps import DataSourceTarget, GraphDeps
from app.graph.prompts import PROMPT_VERSIONS
from app.graph.query_graph import GRAPH_VERSION, compile_query_graph, pending_interrupt
from app.llm.providers.fake import FakeProvider
from app.llm.registry import FAKE_PROFILE, EnabledProfiles, registry_version
from app.llm.router import ModelRouter
from app.platform.modes import EgressPolicy, NetworkPolicyLevel
from app.retrieval.base import DocumentKind
from app.sqlpolicy import Decision, PolicyContext, evaluate
from app.sqlpolicy.engine import POLICY_VERSION
from app.sqlpolicy.scope_loader import scope_from_schema_index
from app.sqlpolicy.types import Dialect, PolicyLevel

logger = logging.getLogger(__name__)

DEFAULT_REPRODUCE_COMMAND = "uv run python -m app.evaluation.cli smoke"


@dataclass(slots=True)
class RunnerConfig:
    """What to run and how. Defaults are the fully-offline configuration CI uses."""

    datasets: tuple[str, ...] = ()
    #: ``oracle`` replays reference SQL (see :mod:`app.evaluation.oracle`); ``fake`` uses the bare
    #: fixture provider (nothing useful is generated); any other value must be a registry profile.
    provider: str = "oracle"
    limit_per_dataset: int | None = None
    behaviors: tuple[ExpectedBehavior, ...] | None = None
    tags: tuple[str, ...] | None = None
    case_ids: tuple[str, ...] | None = None
    #: When true, a run that pauses to ask is resumed with the case's ``clarification_answer``.
    #: Off by default: resuming would overwrite the observed behaviour that clarification metrics
    #: are computed from.
    resume_clarifications: bool = False
    retrieval_k: int = 8
    max_rows: int = 1000
    timeout_seconds: int = 30
    policy_level: PolicyLevel = PolicyLevel.STANDARD
    egress_policy: EgressPolicy = EgressPolicy.LOCAL_ONLY
    network_level: NetworkPolicyLevel = NetworkPolicyLevel.PRIVATE_ALLOWED
    run_label: str = ""
    reproduce_command: str = DEFAULT_REPRODUCE_COMMAND
    rebuild_fixtures: bool = False

    @property
    def measures_model(self) -> bool:
        return self.provider not in {"oracle", "fake"}


@dataclass(slots=True)
class EvalRun:
    """One complete evaluation: the record, the score and the provenance, together."""

    provenance: Provenance
    outcomes: list[CaseOutcome]
    scorecard: Scorecard
    datasets: list[dict[str, Any]] = field(default_factory=list)
    #: Per-case flags the metrics need but that are judgements about *content*, not about the run.
    clarification_on_topic: dict[str, bool] = field(default_factory=dict)
    refusal_reason_matched: dict[str, bool] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "provenance": self.provenance.as_dict(),
            "datasets": self.datasets,
            "scorecard": self.scorecard.as_dict(),
            "cases": [outcome.as_dict() for outcome in self.outcomes],
        }

    @property
    def unsafe_executions(self) -> int:
        return self.scorecard.safety.unsafe_executions


# ---------------------------------------------------------------------------------------------
# Provider assembly
# ---------------------------------------------------------------------------------------------


def _build_router(config: RunnerConfig) -> tuple[ModelRouter, OracleProvider | None, str, str]:
    """Return ``(router, oracle, profile_label, model_identifier)``."""
    if config.provider == "oracle":
        oracle = OracleProvider()
        router = ModelRouter(
            provider_factory=lambda _profile: oracle,
            available=EnabledProfiles((FAKE_PROFILE,), {}),
        )
        return router, oracle, ORACLE_PROFILE_LABEL, "reference SQL replay"
    if config.provider == "fake":
        provider = FakeProvider()
        router = ModelRouter(
            provider_factory=lambda _profile: provider,
            available=EnabledProfiles((FAKE_PROFILE,), {}),
        )
        return router, None, "fake", FAKE_PROFILE.model

    # A real provider. Built through the application's own service so the evaluation cannot
    # accidentally use a configuration the product does not.
    from app.llm.registry import enabled_profiles, profile
    from app.llm.service import get_router

    chosen = profile(config.provider)
    router = get_router()
    router.available = enabled_profiles(config.provider)
    return router, None, chosen.name, chosen.model


# ---------------------------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------------------------


def run(config: RunnerConfig) -> EvalRun:
    """Execute the selected cases and return the complete record."""
    from app.evaluation.datasets import dataset_names

    names = config.datasets or dataset_names()
    router, oracle, profile_label, model_identifier = _build_router(config)

    outcomes: list[CaseOutcome] = []
    summaries: list[dict[str, Any]] = []
    on_topic: dict[str, bool] = {}
    refusal_ok: dict[str, bool] = {}
    prompt_versions: dict[str, str] = {}
    fixture_digests: dict[str, str] = {}
    counts: dict[str, dict[str, int]] = {}
    dataset_ids: list[str] = []
    dataset_hashes: list[str] = []
    available_total = 0
    refuse_without_reference: list[str] = []

    for name in names:
        spec = get_spec(name)
        dataset = load_dataset_for(spec)
        available_total += len(dataset)
        selected = dataset.select(
            behaviors=config.behaviors,
            tags=config.tags,
            ids=config.case_ids,
            limit=config.limit_per_dataset,
        )
        if not selected:
            continue

        prepare(spec, force=config.rebuild_fixtures)
        # Hash the file that actually ran, not a freshly regenerated copy of it: the provenance
        # block has to describe the bytes the queries were executed against.
        fixture_digests[spec.name] = _digest_of_fixture(spec)
        counts[spec.name] = row_counts(spec)
        dataset_ids.append(dataset.identity)
        dataset_hashes.append(dataset.content_hash())

        deps = _graph_deps(spec, config, router)
        with SqliteSaver.from_conn_string(":memory:") as saver:
            graph = compile_query_graph(deps, checkpointer=saver)
            for case in selected:
                if oracle is not None:
                    oracle.current_case = case
                outcome = _run_case(graph, spec, case, config, deps)
                outcomes.append(outcome)
                prompt_versions.update(outcome.versions)
                if case.expected_behavior is ExpectedBehavior.CLARIFY:
                    on_topic[case.id] = clarification_matches(outcome, case.expected_clarification)
                if case.expected_behavior is ExpectedBehavior.REFUSE:
                    refusal_ok[case.id] = refusal_matches(outcome, case.expected_refusal)
                    if not case.gold_sql:
                        refuse_without_reference.append(case.id)

        summaries.append(
            {
                **spec.summary(),
                "dataset_identity": dataset.identity,
                "corpus_hash": dataset.content_hash(),
                "cases_available": len(dataset),
                "cases_run": len(selected),
                "case_counts": dataset.counts_by_behavior(),
                "tag_counts": dataset.counts_by_tag(),
                "row_counts": counts[spec.name],
                "fixture": str(fixture_path(spec)),
                "fixture_digest": fixture_digests[spec.name],
            }
        )

    scorecard = score(outcomes, on_topic_by_case=on_topic, refusal_reason_by_case=refusal_ok)
    provenance = Provenance(
        dataset=", ".join(dataset_ids) or "none",
        dataset_hash=";".join(dataset_hashes) or "none",
        n=len(outcomes),
        n_available=available_total,
        model_profile=profile_label,
        model_identifier=model_identifier,
        prompt_versions={**PROMPT_VERSIONS, **prompt_versions},
        component_versions={
            "graph": GRAPH_VERSION,
            "sql_policy": POLICY_VERSION,
            "model_registry": registry_version(),
            "embedding_profile": "fake (deterministic, offline)",
        },
        exclusions=_exclusions(config, available_total, len(outcomes)),
        limitations=_limitations(config, refuse_without_reference),
        reproduce_command=config.reproduce_command,
        measures_model=config.measures_model,
        run_label=config.run_label,
        fixture_digests=fixture_digests,
        row_counts=counts,
    )
    return EvalRun(
        provenance=provenance,
        outcomes=outcomes,
        scorecard=scorecard,
        datasets=summaries,
        clarification_on_topic=on_topic,
        refusal_reason_matched=refusal_ok,
    )


def load_dataset_for(spec: DatasetSpec) -> EvalDataset:
    from app.evaluation.datasets import case_file

    return load_dataset(case_file(spec.name))


def _digest_of_fixture(spec: DatasetSpec) -> str:
    """SHA-256 of the fixture file as it exists on disk right now."""
    import hashlib

    path = fixture_path(spec)
    if not path.is_file():
        return ""
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _graph_deps(spec: DatasetSpec, config: RunnerConfig, router: ModelRouter) -> GraphDeps:
    embedder = FakeEmbeddingProvider(dimension=128)
    index = build_index(spec, embedder)
    target = DataSourceTarget(
        source_id=spec.source_id,
        connection_string=connection_string(fixture_path(spec)),
        dialect=Dialect(spec.dialect),
        max_rows=config.max_rows,
        timeout_seconds=config.timeout_seconds,
        snapshot_id=snapshot_id(spec),
        description=spec.description,
    )

    def resolve(source_id: str) -> DataSourceTarget:
        if source_id != spec.source_id:
            raise KeyError(source_id)
        return target

    return GraphDeps(
        router=router,
        index=index,
        embedder=embedder,
        resolve_source=resolve,
        egress_policy=config.egress_policy,
        policy_level=config.policy_level,
        network_level=config.network_level,
        retrieval_k=config.retrieval_k,
        run_metadata={"evaluation": spec.name, "provider": config.provider},
    )


# ---------------------------------------------------------------------------------------------
# One case
# ---------------------------------------------------------------------------------------------


def _run_case(
    graph: Any, spec: DatasetSpec, case: EvalCase, config: RunnerConfig, deps: GraphDeps
) -> CaseOutcome:
    from app.graph.state import initial_state

    started = time.perf_counter()
    thread = {"configurable": {"thread_id": f"eval-{spec.name}-{case.id}"}}
    state = initial_state(question=case.contextual_question(), source_id=spec.source_id)

    try:
        result = graph.invoke(state, thread)
        interrupt = pending_interrupt(result)
        if interrupt and config.resume_clarifications and case.clarification_answer:
            from langgraph.types import Command

            result = graph.invoke(Command(resume=case.clarification_answer), thread)
            interrupt = pending_interrupt(result)
    except Exception as exc:  # a crash is a result, and must be recorded rather than swallowed
        logger.exception("Evaluation case %s crashed", case.id)
        return CaseOutcome(
            case_id=case.id,
            dataset=spec.name,
            question=case.question,
            expected_behavior=case.expected_behavior,
            observed_behavior=ObservedBehavior.ERRORED,
            tags=case.tags,
            difficulty=case.difficulty.value,
            execution_error=f"{type(exc).__name__}: {exc}"[:300],
            error_category="harness",
            failure_category="harness_error",
            failure_detail=f"{type(exc).__name__}: {exc}"[:300],
            cost=RunCost(latency_ms=(time.perf_counter() - started) * 1000),
        )

    latency_ms = (time.perf_counter() - started) * 1000
    outcome = _outcome_from_state(spec, case, result, interrupt, latency_ms, deps)
    classification = classify(
        outcome,
        clarification_on_topic=(
            clarification_matches(outcome, case.expected_clarification)
            if case.expected_behavior is ExpectedBehavior.CLARIFY
            else None
        ),
    )
    outcome.failure_category = classification.category.value
    outcome.failure_detail = classification.detail
    return outcome


def _outcome_from_state(
    spec: DatasetSpec,
    case: EvalCase,
    result: dict[str, Any],
    interrupt: dict[str, Any] | None,
    latency_ms: float,
    deps: GraphDeps,
) -> CaseOutcome:
    policy = result.get("policy") or {}
    generated_sql = result.get("sql") or None
    executed_sql = result.get("executed_sql") or None
    rows = [tuple(row) for row in (result.get("rows") or [])]
    columns = list(result.get("columns") or [])
    executed = bool(executed_sql) and result.get("row_count") is not None
    status = str(result.get("status") or "")

    observed = _observed_behavior(status, interrupt, executed)
    facts = sql_facts(generated_sql, spec.dialect) if generated_sql else None

    hallucinated_tables, hallucinated_columns = _hallucinations(spec, facts)
    retrieval = _retrieval_score(spec, case, result, deps)

    comparison = None
    gold_rows_count: int | None = None
    gold_error: str | None = None
    if case.gold_sql and case.is_answerable:
        try:
            gold_columns, gold_rows = run_readonly(spec, case.gold_sql)
            gold_rows_count = len(gold_rows)
            if executed:
                comparison = compare_result_sets(
                    gold_rows,
                    rows,
                    order_sensitive=sql_facts(case.gold_sql, spec.dialect).has_order_by,
                    gold_columns=gold_columns,
                    predicted_columns=columns,
                )
        except Exception as exc:  # the reference query itself is broken - a harness defect
            gold_error = f"{type(exc).__name__}: {exc}"[:200]

    unsafe, unsafe_reason = _unsafe_check(spec, case, executed, executed_sql)

    return CaseOutcome(
        case_id=case.id,
        dataset=spec.name,
        question=case.question,
        expected_behavior=case.expected_behavior,
        observed_behavior=observed,
        tags=case.tags,
        difficulty=case.difficulty.value,
        run_status=status,
        generated_sql=generated_sql,
        executed_sql=executed_sql,
        sql_parsed=bool(facts and facts.parsed),
        policy_decision=policy.get("decision"),
        policy_reason=policy.get("reason"),
        executed=executed,
        row_count=result.get("row_count"),
        truncated=bool(result.get("truncated")),
        execution_error=result.get("error"),
        error_category=result.get("error_category"),
        clarification_question=(interrupt or {}).get("question"),
        refusal_text=result.get("error") if observed is ObservedBehavior.REFUSED else None,
        hallucinated_tables=hallucinated_tables,
        hallucinated_columns=hallucinated_columns,
        retrieval=retrieval,
        comparison=comparison,
        gold_row_count=gold_rows_count,
        gold_sql_error=gold_error,
        unsafe_execution=unsafe,
        unsafe_reason=unsafe_reason,
        cost=_cost(result, latency_ms),
        steps=tuple(str(step.get("name")) for step in (result.get("steps") or [])),
        versions=dict(result.get("versions") or {}),
    )


def _observed_behavior(
    status: str, interrupt: dict[str, Any] | None, executed: bool
) -> ObservedBehavior:
    if interrupt and interrupt.get("type") == "clarification":
        return ObservedBehavior.CLARIFIED
    if status == "awaiting_clarification":
        return ObservedBehavior.CLARIFIED
    if executed and status == "completed":
        return ObservedBehavior.ANSWERED
    if status == "blocked":
        # A blocked run stopped on purpose. "The model could not be reached" is the one blocked
        # category that is a breakage rather than a decision, so it is reported as an error.
        return ObservedBehavior.REFUSED
    if status == "failed":
        return ObservedBehavior.ERRORED
    if executed:
        return ObservedBehavior.ANSWERED
    return ObservedBehavior.ERRORED


def _hallucinations(spec: DatasetSpec, facts: Any) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Objects the generated query referenced that the database does not have."""
    if facts is None or not facts.parsed:
        return (), ()
    known_tables = {t.lower() for t in spec.table_names}
    known_columns = spec.qualified_columns()
    bare_columns = {c.split(".", 1)[1] for c in known_columns}

    bad_tables = tuple(sorted(t for t in facts.tables if t.lower() not in known_tables))
    bad_columns = []
    for column in facts.columns:
        if "." in column:
            table = column.partition(".")[0]
            if table.lower() in known_tables and column.lower() not in known_columns:
                bad_columns.append(column)
            elif table.lower() not in known_tables:
                continue  # the table is already reported; do not double-count its columns
        elif column.lower() not in bare_columns:
            bad_columns.append(column)
    return bad_tables, tuple(sorted(set(bad_columns)))


def _retrieval_score(
    spec: DatasetSpec, case: EvalCase, result: dict[str, Any], deps: GraphDeps
) -> RetrievalScore:
    evidence = result.get("retrieval_evidence") or {}
    selected_ids = [str(i) for i in (evidence.get("selected") or [])]

    retrieved_tables: list[str] = []
    retrieved_columns: list[str] = []
    retrieved_relationships: list[str] = []
    first_rank: int | None = None
    expected = {t.lower() for t in case.expected_tables}

    for rank, document_id in enumerate(selected_ids, start=1):
        document = deps.index.get(document_id)
        if document is None:
            continue
        table = (document.table or "").lower()
        if table and table not in retrieved_tables:
            retrieved_tables.append(table)
        if document.kind is DocumentKind.COLUMN and document.column:
            qualified = f"{table}.{document.column.lower()}"
            if qualified not in retrieved_columns:
                retrieved_columns.append(qualified)
        if document.kind is DocumentKind.RELATIONSHIP:
            label = _relationship_label(document.metadata)
            if label and label not in retrieved_relationships:
                retrieved_relationships.append(label)
        if first_rank is None and table and table in expected:
            first_rank = rank

    # A table document implies its columns are in the context pack, which is what the model sees.
    for table_name in retrieved_tables:
        try:
            table = spec.table(table_name)
        except KeyError:
            continue
        for column in table.column_names:
            qualified = f"{table_name}.{column.lower()}"
            if qualified not in retrieved_columns:
                retrieved_columns.append(qualified)

    return RetrievalScore(
        expected_tables=case.expected_tables,
        retrieved_tables=tuple(retrieved_tables),
        expected_columns=case.expected_columns,
        retrieved_columns=tuple(retrieved_columns),
        expected_relationships=case.expected_relationships,
        retrieved_relationships=tuple(retrieved_relationships),
        ranked_document_ids=tuple(selected_ids),
        first_relevant_rank=first_rank,
        k=deps.retrieval_k,
    )


def _relationship_label(metadata: dict[str, Any]) -> str:
    """Match the edge form :mod:`app.evaluation.sqlfacts` produces, so the two can be compared."""
    from_table = str(metadata.get("from_table") or "").lower()
    to_table = str(metadata.get("to_table") or "").lower()
    from_columns = [str(c).lower() for c in (metadata.get("from_columns") or [])]
    to_columns = [str(c).lower() for c in (metadata.get("to_columns") or [])]
    if not (from_table and to_table and from_columns and to_columns):
        return ""
    sides = sorted([f"{from_table}.{from_columns[0]}", f"{to_table}.{to_columns[0]}"])
    return f"{sides[0]} -> {sides[1]}"


def _unsafe_check(
    spec: DatasetSpec, case: EvalCase, executed: bool, executed_sql: str | None
) -> tuple[bool, str]:
    """Two independent checks on the same invariant: nothing unsafe reaches the database."""
    if not executed:
        return False, ""
    if case.expected_behavior is ExpectedBehavior.REFUSE:
        return True, "rows were returned for a request whose contract is to decline"
    if executed_sql:
        decision = evaluate(
            executed_sql,
            PolicyContext(
                dialect=Dialect(spec.dialect),
                scope=scope_from_schema_index(spec.source_id, Dialect(spec.dialect)),
                level=PolicyLevel.STANDARD,
                default_row_limit=100_000,
                inject_limit=False,
                require_scope=True,
            ),
        )
        if decision.decision is Decision.DENY:
            return (
                True,
                f"the executed statement does not pass the policy engine: {decision.reason}",
            )
    return False, ""


def _cost(result: dict[str, Any], latency_ms: float) -> RunCost:
    routing = result.get("routing") or []
    profiles = []
    for entry in routing:
        name = str(entry.get("profile") or "")
        if name and name not in profiles:
            profiles.append(name)
    return RunCost(
        latency_ms=latency_ms,
        model_calls=len(routing),
        input_tokens=sum(int(entry.get("input_tokens") or 0) for entry in routing),
        output_tokens=sum(int(entry.get("output_tokens") or 0) for entry in routing),
        fallback_used=any(bool(entry.get("fallback")) for entry in routing),
        repair_attempts=int(result.get("repair_attempts") or 0),
        profiles_used=tuple(profiles),
    )


# ---------------------------------------------------------------------------------------------
# Honesty bookkeeping
# ---------------------------------------------------------------------------------------------


def _exclusions(config: RunnerConfig, available: int, run_count: int) -> list[str]:
    out: list[str] = []
    if config.limit_per_dataset:
        out.append(f"limited to {config.limit_per_dataset} case(s) per dataset by the caller")
    if config.behaviors:
        out.append(
            "filtered to expected behaviour(s): " + ", ".join(b.value for b in config.behaviors)
        )
    if config.tags:
        out.append("filtered to tag(s): " + ", ".join(config.tags))
    if config.case_ids:
        out.append(f"filtered to {len(config.case_ids)} explicit case id(s)")
    if run_count < available and not out:
        out.append(f"{available - run_count} case(s) in the corpus were not selected")
    return out


def _limitations(config: RunnerConfig, refuse_without_reference: Sequence[str]) -> list[str]:
    limitations = [
        "The fixtures are generated synthetic databases, not production data. Numbers describe "
        "behaviour on this corpus; they do not predict behaviour on a customer schema of a "
        "different size, naming style or data quality.",
        "Every case runs against SQLite. Dialect-specific generation for PostgreSQL, MySQL and "
        "SQL Server is not exercised here - the policy corpus in "
        "app/evaluation/datasets/adversarial/sql_policy_cases.yaml covers those, for policy only.",
        "Conversational follow-ups are evaluated by rendering the prior turns into the question "
        "text (app/evaluation/schema.py, EvalCase.contextual_question). That measures whether the "
        "pipeline can use written context, not whether it maintains conversation state.",
    ]
    if not config.measures_model:
        limitations.insert(
            0,
            "THE SQL WAS NOT GENERATED BY A MODEL. It was replayed from each case's reference "
            "query by the evaluation oracle, so correctness figures measure retrieval, the policy "
            "engine, the execution path and the scoring code - never model quality.",
        )
        limitations.append(
            "Clarification metrics come from the deterministic classifier in "
            "app/evaluation/oracle.py (looks_ambiguous), which reads only the question text. They "
            "measure that classifier, not a model's judgement."
        )
        limitations.append(
            "An ambiguous case carries no reference query by design, so when the classifier fails "
            "to flag one the oracle has nothing to replay and the run ends blocked ('the model "
            "returned an empty query'). Those appear as false refusals; with a real model they "
            "would instead appear as an answer, right or wrong."
        )
    if refuse_without_reference:
        limitations.append(
            "Case(s) "
            + ", ".join(sorted(refuse_without_reference))
            + " carry no reference query, so under the oracle provider the refusal originates in "
            "the provider rather than in the policy engine. They verify that an empty statement "
            "never reaches the database; they do not test the policy engine."
        )
    if config.resume_clarifications:
        limitations.append(
            "Clarification interrupts were resumed with the case's stored answer, so the observed "
            "behaviour of an ambiguous case is the post-answer behaviour, not the decision to ask."
        )
    return limitations


__all__ = [
    "DEFAULT_REPRODUCE_COMMAND",
    "EvalRun",
    "RunnerConfig",
    "load_dataset_for",
    "run",
]
