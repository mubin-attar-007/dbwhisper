"""``python -m app.evaluation.cli`` - the entry point for every evaluation the project runs.

Subcommands, and what each one is for:

* ``build`` - regenerate the fixtures and the enrollment artefacts, print their digests. Nothing
  else needs running first; every other subcommand does this implicitly.
* ``smoke`` - the CI gate. Runs the whole corpus offline with the gold-SQL oracle and **exits
  non-zero if a single unsafe execution occurred**. Fast, deterministic, no credentials.
* ``custom`` - run the project's own corpus with whatever provider is configured. This is the one
  that produces a publishable number, and only when ``--provider`` names a real model.
* ``safety`` - only the cases whose contract is to decline. The narrowest gate, and the one worth
  running on every change to the policy engine.
* ``retrieval`` - retrieval metrics only, with no model and no execution, so a retrieval change can
  be measured in seconds.
* ``report`` - re-render a stored JSON run as Markdown. Re-scoring a stored run is the capability
  the v1 harness lacked when its scoring predicate turned out to be wrong.
* ``spider`` / ``bird`` - print what a person must do by hand to run an external benchmark. These
  download nothing (see :mod:`app.evaluation.adapters`).

Exit codes: ``0`` success, ``1`` a gate failed (unsafe execution, or a threshold the caller set),
``2`` the command could not run at all.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from app.evaluation import report as report_module
from app.evaluation.datasets import dataset_names, get_spec
from app.evaluation.runner import EvalRun, RunnerConfig, run
from app.evaluation.schema import ExpectedBehavior

DEFAULT_OUTPUT_DIR = Path("evaluation/results")


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--dataset",
        action="append",
        choices=sorted(dataset_names()),
        help="restrict to one dataset; repeatable (default: all)",
    )
    parser.add_argument("--limit", type=int, default=None, help="cases per dataset")
    parser.add_argument("--tag", action="append", help="only cases carrying this tag; repeatable")
    parser.add_argument("--case", action="append", help="explicit case id; repeatable")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="write <name>.json and <name>.md under this directory",
    )
    parser.add_argument("--label", default="", help="run label recorded in the provenance block")
    parser.add_argument(
        "--rebuild", action="store_true", help="rebuild the SQLite fixtures from scratch first"
    )
    parser.add_argument(
        "--quiet", action="store_true", help="print only the one-line summary, not the report"
    )


def _config(args: argparse.Namespace, **overrides) -> RunnerConfig:
    return RunnerConfig(
        datasets=tuple(args.dataset or ()),
        limit_per_dataset=args.limit,
        tags=tuple(args.tag) if args.tag else None,
        case_ids=tuple(args.case) if args.case else None,
        run_label=args.label,
        rebuild_fixtures=bool(args.rebuild),
        **overrides,
    )


def _emit(result: EvalRun, args: argparse.Namespace, name: str) -> None:
    if args.out:
        json_path = report_module.write_json(result, Path(args.out) / f"{name}.json")
        markdown_path = report_module.write_markdown(result, Path(args.out) / f"{name}.md")
        print(f"wrote {json_path}")
        print(f"wrote {markdown_path}")
    elif not args.quiet:
        print(report_module.to_markdown(result))
    print(report_module.summary_line(result))


def _gate(result: EvalRun) -> int:
    """The one non-negotiable gate: no unsafe execution, ever."""
    unsafe = result.scorecard.safety.unsafe_executions
    if unsafe:
        print(
            f"FAIL: {unsafe} unsafe execution(s): "
            + ", ".join(result.scorecard.safety.unsafe_case_ids),
            file=sys.stderr,
        )
        return 1
    return 0


# ---------------------------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------------------------


def cmd_build(args: argparse.Namespace) -> int:
    from app.evaluation.datasets.build import fixture_digest, prepare, row_counts

    for name in args.dataset or dataset_names():
        spec = get_spec(name)
        fixture, index_path = prepare(spec, force=True)
        digest = fixture_digest(spec)
        counts = row_counts(spec)
        print(f"{spec.name}@{spec.version}")
        print(f"  fixture      {fixture}")
        print(f"  digest       {digest}")
        print(f"  schema index {index_path}")
        print(f"  rows         {sum(counts.values()):,} across {len(counts)} tables")
    return 0


def cmd_smoke(args: argparse.Namespace) -> int:
    result = run(
        _config(
            args,
            provider="oracle",
            reproduce_command="uv run python -m app.evaluation.cli smoke",
        )
    )
    _emit(result, args, "eval-smoke")
    return _gate(result)


def cmd_custom(args: argparse.Namespace) -> int:
    result = run(
        _config(
            args,
            provider=args.provider,
            resume_clarifications=bool(args.resume_clarifications),
            reproduce_command=(
                f"uv run python -m app.evaluation.cli custom --provider {args.provider}"
            ),
        )
    )
    _emit(result, args, "eval-custom")
    status = _gate(result)
    if args.min_execution_accuracy is not None:
        accuracy = result.scorecard.generation.execution_accuracy
        value = accuracy.value
        if value is None or value < args.min_execution_accuracy:
            print(
                f"FAIL: execution accuracy {accuracy.text()} is below the "
                f"--min-execution-accuracy threshold of {args.min_execution_accuracy}",
                file=sys.stderr,
            )
            status = 1
    return status


def cmd_safety(args: argparse.Namespace) -> int:
    result = run(
        _config(
            args,
            provider=args.provider,
            behaviors=(ExpectedBehavior.REFUSE,),
            reproduce_command="uv run python -m app.evaluation.cli safety",
        )
    )
    _emit(result, args, "eval-safety")
    status = _gate(result)
    refusals = result.scorecard.safety.correct_refusals
    if refusals.denominator and refusals.numerator < refusals.denominator:
        missed = [o.case_id for o in result.outcomes if not o.behaviour_matched]
        print(
            f"FAIL: {refusals.text()} unsafe requests declined; not declined: " + ", ".join(missed),
            file=sys.stderr,
        )
        status = 1
    return status


def cmd_retrieval(args: argparse.Namespace) -> int:
    """Retrieval only: no model call is needed to know which tables came back."""
    from app.evaluation.datasets.build import build_index, prepare, snapshot_id
    from app.evaluation.metrics import Ratio, recall_at_k, reciprocal_rank
    from app.evaluation.runner import load_dataset_for
    from app.retrieval.context_pack import build_context_pack
    from app.retrieval.hybrid import RetrievalRequest, retrieve

    embeddings_kind = getattr(args, "embeddings", "fake")
    embedding_version = "unknown"
    per_dataset: list[dict[str, object]] = []
    table_hits = table_total = 0
    reciprocal_total = 0.0
    scored = 0
    for name in args.dataset or dataset_names():
        spec = get_spec(name)
        prepare(spec, force=bool(args.rebuild))
        embedder = _retrieval_embedder(embeddings_kind)
        embedding_version = embedder.version.key
        index = build_index(spec, embedder)
        dataset = load_dataset_for(spec)
        dataset_hits = dataset_total = dataset_scored = 0
        dataset_reciprocal = 0.0
        cases = dataset.select(
            behaviors=(ExpectedBehavior.ANSWER,),
            limit=args.limit,
            ids=tuple(args.case or ()) or None,
        )
        for case in cases:
            result = retrieve(
                index,
                RetrievalRequest(
                    question=case.contextual_question(),
                    source_id=spec.source_id,
                    snapshot_id=snapshot_id(spec),
                    k=args.k,
                    embedding=embedder.embed_query(case.contextual_question()),
                ),
            )
            pack = build_context_pack(result.hits)
            retrieved = [t.split(".")[-1].lower() for t in pack.table_names]
            recall = recall_at_k(case.expected_tables, retrieved)
            table_hits += recall.numerator
            table_total += recall.denominator
            reciprocal_total += reciprocal_rank(case.expected_tables, retrieved)
            dataset_reciprocal += reciprocal_rank(case.expected_tables, retrieved)
            dataset_hits += recall.numerator
            dataset_total += recall.denominator
            dataset_scored += 1
            scored += 1
        per_dataset.append(
            {
                "dataset": spec.name,
                "cases": dataset_scored,
                "table_recall": Ratio(dataset_hits, dataset_total, "table recall@k").as_dict(),
                "mrr": (dataset_reciprocal / dataset_scored) if dataset_scored else None,
            }
        )
        print(f"{spec.name}: {len(cases)} case(s) scored")

    overall = Ratio(table_hits, table_total, "table recall@k")
    print(f"\nTable recall@{args.k}: {overall.text()} over {scored} case(s)")
    print(
        "Mean reciprocal rank: "
        + (f"{reciprocal_total / scored:.4f} (n={scored})" if scored else "n/a (0 cases)")
    )
    print(
        "\nRetrieval only - no model, no policy engine, no execution. This measures whether the "
        "context pack contained the tables the reference query needed."
    )

    if args.out:
        for path in _write_retrieval_report(
            out_dir=Path(args.out),
            label=getattr(args, "label", None),
            k=args.k,
            embeddings_kind=embeddings_kind,
            embedding_version=embedding_version,
            overall=overall,
            mrr=(reciprocal_total / scored) if scored else None,
            scored=scored,
            per_dataset=per_dataset,
        ):
            print(f"wrote {path}")
    return 0


def _retrieval_embedder(kind: str):
    """The embedder a retrieval run scores against.

    Fake is the default and the one CI uses: it is deterministic, needs no download and no network,
    so a recall figure from CI is reproducible byte-for-byte and a regression is a real regression.
    It does not measure semantic retrieval - with fake vectors the fusion is carried by BM25 and the
    exact-match boost - so a report built on it must say so, and ``_write_retrieval_report`` does.

    Local uses the same CPU model a self-hosted deployment uses, downloaded on first call. That is
    the number that describes the product; it is not run in CI because a model download does not
    belong in a pull-request gate.
    """
    if kind == "local":
        from app.embeddings.local import LocalEmbeddingProvider

        return LocalEmbeddingProvider()

    from app.embeddings.fake import FakeEmbeddingProvider

    return FakeEmbeddingProvider(dimension=128)


def _write_retrieval_report(
    *,
    out_dir: Path,
    label: str | None,
    k: int,
    embeddings_kind: str,
    embedding_version: str,
    overall,
    mrr: float | None,
    scored: int,
    per_dataset: list[dict],
) -> list[Path]:
    """Write the run as JSON and Markdown, with the provenance that makes the number citable.

    A retrieval number without its embedder, its k and its dataset revision is not a measurement,
    it is a rumour - so both files carry all three, plus the command that reproduces them.
    """
    import json
    from datetime import UTC, datetime

    from app.evaluation.provenance import environment_description

    out_dir.mkdir(parents=True, exist_ok=True)
    name = label or f"retrieval-{embeddings_kind}-k{k}"
    measured_at = datetime.now(UTC).isoformat(timespec="seconds")

    reproduce = (
        "uv run python -m app.evaluation.cli retrieval"
        + "".join(f" --dataset {d['dataset']}" for d in per_dataset)
        + f" --k {k} --embeddings {embeddings_kind}"
    )
    caveat = (
        "Fake embeddings are deterministic vectors, so this measures the lexical and fusion path, "
        "not semantic similarity. It is a regression gate, not a statement about retrieval quality."
        if embeddings_kind == "fake"
        else "Local CPU embeddings - the same model a self-hosted deployment uses."
    )

    payload = {
        "run": name,
        "measured_at": measured_at,
        "kind": "retrieval-only",
        "k": k,
        "embeddings": embeddings_kind,
        "embedding_version": embedding_version,
        "cases_scored": scored,
        "table_recall": overall.as_dict(),
        "mrr": mrr,
        "per_dataset": per_dataset,
        "environment": environment_description(),
        "reproduce": reproduce,
        "measures_model": False,
        "caveat": caveat,
    }

    json_path = out_dir / f"{name}.json"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    rows = "\n".join(
        "| {} | {} | {} | {} |".format(
            d["dataset"],
            d["cases"],
            d["table_recall"].get("text", ""),
            "n/a" if d["mrr"] is None else f"{d['mrr']:.4f}",
        )
        for d in per_dataset
    )
    markdown = f"""# Retrieval evaluation - {name}

**Retrieval only.** No model call, no policy engine, no execution. This measures one thing: whether
the context pack handed to the model contained the tables the reference query actually needed.

{caveat}

| Field | Value |
|---|---|
| Measured | {measured_at} |
| k | {k} |
| Embeddings | `{embeddings_kind}` |
| Embedding model | `{embedding_version}` |
| Cases scored | {scored} |
| Table recall@{k} | **{overall.text()}** |
| Mean reciprocal rank | **{"n/a" if mrr is None else f"{mrr:.4f}"}** |
| Measures model quality | no |

## Per dataset

| Dataset | Cases | Table recall@{k} | MRR |
|---|---|---|---|
{rows}

## Reproduce

```
{reproduce}
```

## Environment

```
{environment_description()}
```
"""
    markdown_path = out_dir / f"{name}.md"
    markdown_path.write_text(markdown, encoding="utf-8")
    return [json_path, markdown_path]


def cmd_report(args: argparse.Namespace) -> int:
    """Re-render a stored run. Deliberately reads the JSON rather than re-running anything."""
    import json

    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    markdown = _markdown_from_payload(payload)
    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(markdown, encoding="utf-8")
        print(f"wrote {target}")
    else:
        print(markdown)
    return 0


def _markdown_from_payload(payload: dict) -> str:
    """Rebuild a report from a stored JSON run without re-running the pipeline."""
    from app.evaluation.metrics import score
    from app.evaluation.provenance import Provenance
    from app.evaluation.runner import EvalRun

    outcomes = [_outcome_from_payload(case) for case in payload.get("cases", [])]
    stored = payload.get("provenance", {})
    provenance = Provenance(
        dataset=stored.get("dataset", "unknown"),
        dataset_hash=stored.get("dataset_hash", ""),
        n=int(stored.get("n") or len(outcomes)),
        n_available=int(stored.get("n_available") or len(outcomes)),
        model_profile=stored.get("model_profile", "unknown"),
        model_identifier=stored.get("model_identifier", "unknown"),
        prompt_versions=stored.get("prompt_versions", {}),
        component_versions=stored.get("component_versions", {}),
        date=stored.get("date", ""),
        environment=stored.get("environment", ""),
        exclusions=list(stored.get("exclusions", [])),
        limitations=list(stored.get("limitations", [])),
        reproduce_command=stored.get("reproduce_command", ""),
        measures_model=bool(stored.get("measures_model", True)),
        run_label=stored.get("run_label", ""),
        fixture_digests=stored.get("fixture_digests", {}),
        row_counts=stored.get("row_counts", {}),
    )
    return report_module.to_markdown(
        EvalRun(
            provenance=provenance,
            outcomes=outcomes,
            scorecard=score(outcomes),
            datasets=list(payload.get("datasets", [])),
        )
    )


def _outcome_from_payload(case: dict):
    """Rehydrate one stored outcome. Only the fields the metrics and the report read."""
    from app.evaluation.metrics import ResultComparison
    from app.evaluation.outcomes import CaseOutcome, ObservedBehavior, RetrievalScore, RunCost

    comparison_payload = case.get("comparison")
    comparison = None
    if comparison_payload:
        comparison = ResultComparison(
            match=bool(comparison_payload.get("match")),
            order_sensitive=bool(comparison_payload.get("order_sensitive")),
            ordered_match=bool(comparison_payload.get("ordered_match")),
            multiset_match=bool(comparison_payload.get("multiset_match")),
            set_match=bool(comparison_payload.get("set_match")),
            column_count_match=bool(comparison_payload.get("column_count_match")),
            column_names_match=bool(comparison_payload.get("column_names_match")),
            gold_rows=int(comparison_payload.get("gold_rows") or 0),
            predicted_rows=int(comparison_payload.get("predicted_rows") or 0),
            gold_columns=tuple(comparison_payload.get("gold_columns") or ()),
            predicted_columns=tuple(comparison_payload.get("predicted_columns") or ()),
            gold_is_projection_of_predicted=bool(
                comparison_payload.get("gold_is_projection_of_predicted")
            ),
            reason=str(comparison_payload.get("reason") or ""),
        )
    retrieval_payload = case.get("retrieval") or {}
    return CaseOutcome(
        case_id=case.get("case_id", ""),
        dataset=case.get("dataset", ""),
        question=case.get("question", ""),
        expected_behavior=ExpectedBehavior(case.get("expected_behavior", "answer")),
        observed_behavior=ObservedBehavior(case.get("observed_behavior", "errored")),
        tags=tuple(case.get("tags") or ()),
        difficulty=case.get("difficulty", "medium"),
        run_status=case.get("run_status", ""),
        generated_sql=case.get("generated_sql"),
        executed_sql=case.get("executed_sql"),
        sql_parsed=bool(case.get("sql_parsed")),
        policy_decision=case.get("policy_decision"),
        policy_reason=case.get("policy_reason"),
        executed=bool(case.get("executed")),
        row_count=case.get("row_count"),
        truncated=bool(case.get("truncated")),
        execution_error=case.get("execution_error"),
        error_category=case.get("error_category"),
        clarification_question=case.get("clarification_question"),
        refusal_text=case.get("refusal_text"),
        hallucinated_tables=tuple(case.get("hallucinated_tables") or ()),
        hallucinated_columns=tuple(case.get("hallucinated_columns") or ()),
        retrieval=RetrievalScore(
            expected_tables=tuple(retrieval_payload.get("expected_tables") or ()),
            retrieved_tables=tuple(retrieval_payload.get("retrieved_tables") or ()),
            first_relevant_rank=retrieval_payload.get("first_relevant_rank"),
            k=int(retrieval_payload.get("k") or 0),
        ),
        comparison=comparison,
        gold_row_count=case.get("gold_row_count"),
        gold_sql_error=case.get("gold_sql_error"),
        unsafe_execution=bool(case.get("unsafe_execution")),
        unsafe_reason=case.get("unsafe_reason", ""),
        failure_category=case.get("failure_category"),
        failure_detail=case.get("failure_detail", ""),
        cost=RunCost(**{k: v for k, v in (case.get("cost") or {}).items() if k != "profiles_used"}),
        versions=dict(case.get("versions") or {}),
    )


def cmd_external(args: argparse.Namespace) -> int:
    from app.evaluation import adapters

    name = args.command
    if adapters.available(name):
        dataset = adapters.load_external(name, limit=args.limit)
        print(
            f"{name}: {len(dataset)} case(s) available at {adapters.BENCHMARKS[name].directory()}"
        )
        print(
            "Conversion works. Scoring these through the v2 pipeline needs a source registered for "
            "each benchmark database, which this repository does not do automatically - see "
            "app/evaluation/adapters.py."
        )
        return 0
    print(adapters.instructions(name))
    return 2


# ---------------------------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.evaluation.cli",
        description="DBWhisper evaluation harness. Everything runs offline by default.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="regenerate fixtures and enrollment artefacts")
    build.add_argument("--dataset", action="append", choices=sorted(dataset_names()))
    build.set_defaults(handler=cmd_build)

    smoke = subparsers.add_parser(
        "smoke", help="offline oracle run over the whole corpus; fails on any unsafe execution"
    )
    _add_common(smoke)
    smoke.set_defaults(handler=cmd_smoke)

    custom = subparsers.add_parser("custom", help="run the corpus with a chosen provider")
    _add_common(custom)
    custom.add_argument(
        "--provider",
        default="oracle",
        help="oracle (replays reference SQL), fake, or a model profile name such as local-balanced",
    )
    custom.add_argument(
        "--resume-clarifications",
        action="store_true",
        help="resume a paused run with the case's stored answer (changes what is measured)",
    )
    custom.add_argument(
        "--min-execution-accuracy",
        type=float,
        default=None,
        help="exit non-zero when execution accuracy falls below this fraction",
    )
    custom.set_defaults(handler=cmd_custom)

    safety = subparsers.add_parser("safety", help="only the cases whose contract is to decline")
    _add_common(safety)
    safety.add_argument("--provider", default="oracle")
    safety.set_defaults(handler=cmd_safety)

    retrieval = subparsers.add_parser("retrieval", help="retrieval metrics only, no model")
    _add_common(retrieval)
    retrieval.add_argument("--k", type=int, default=8)
    retrieval.add_argument(
        "--embeddings",
        choices=("fake", "local"),
        default="fake",
        help=(
            "fake (default): deterministic and offline, what CI measures. "
            "local: the CPU model a self-hosted deployment uses, downloaded on first run."
        ),
    )
    retrieval.set_defaults(handler=cmd_retrieval)

    report_parser = subparsers.add_parser("report", help="re-render a stored JSON run as Markdown")
    report_parser.add_argument("input", type=Path, help="path to a JSON run produced by --out")
    report_parser.add_argument("--out", type=Path, default=None)
    report_parser.set_defaults(handler=cmd_report)

    for name in ("spider", "bird"):
        external = subparsers.add_parser(
            name, help=f"how to run {name.upper()} by hand (downloads nothing)"
        )
        external.add_argument("--limit", type=int, default=None)
        external.set_defaults(handler=cmd_external)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
