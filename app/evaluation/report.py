"""Rendering a run as Markdown and JSON, without letting a number escape its provenance.

Two rules are enforced here rather than remembered:

* **No bare percentages.** Every figure is rendered through
  :meth:`app.evaluation.metrics.Ratio.text`, which always emits "18 of 22 (81.8%)". A reader can
  re-derive the fraction, spot a denominator of 4, and see when a metric is undefined instead of
  reading a fabricated 0%.
* **The provenance block leads.** It is the first section of the Markdown and the first key of the
  JSON, and when the run did not actually measure a model the report says so in a banner before any
  correctness figure appears.

The JSON is the durable artefact: it holds every case outcome, so a stored run can be re-scored
after a metric changes. The Markdown is for humans and is derived entirely from it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.evaluation.metrics import Ratio
from app.evaluation.runner import EvalRun
from app.evaluation.taxonomy import FailureCategory, by_owner, summarize

#: How many failing cases the Markdown lists in full before it stops and points at the JSON.
MAX_FAILURE_ROWS = 25


def to_json(run: EvalRun, *, indent: int = 2) -> str:
    return json.dumps(run.as_dict(), indent=indent, default=str)


def write_json(run: EvalRun, path: Path | str) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(to_json(run), encoding="utf-8")
    return target


def write_markdown(run: EvalRun, path: Path | str) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(to_markdown(run), encoding="utf-8")
    return target


def to_markdown(run: EvalRun) -> str:
    sections = [
        _header(run),
        _banner(run),
        _provenance(run),
        _datasets(run),
        _headline(run),
        _retrieval(run),
        _generation(run),
        _clarification(run),
        _safety(run),
        _failures(run),
        _ops(run),
        _footer(),
    ]
    return "\n\n".join(section for section in sections if section).strip() + "\n"


# ---------------------------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------------------------


def _header(run: EvalRun) -> str:
    label = run.provenance.run_label or "evaluation run"
    return f"# DBWhisper evaluation - {label}\n\n_{run.provenance.date}_"


def _banner(run: EvalRun) -> str:
    lines = []
    if not run.provenance.measures_model:
        lines.append(
            "> **This run did not measure a model.** The SQL was replayed from each case's reference "
            "query by the evaluation oracle (`app/evaluation/oracle.py`). The correctness figures "
            "below describe retrieval, the policy engine, the execution path and the scoring code. "
            "They are **not** a statement about how well any model writes SQL, and must never be "
            "published as one."
        )
    missing = run.provenance.missing_fields()
    if missing:
        lines.append(
            f"> **Incomplete provenance - not publishable.** Missing: {', '.join(missing)}."
        )
    if any(d.get("synthetic") for d in run.datasets):
        synthetic = ", ".join(d["name"] for d in run.datasets if d.get("synthetic"))
        lines.append(
            f"> **Synthetic data.** Every dataset here ({synthetic}) is generated. No row describes "
            "a real person, organisation, transaction or clinical record."
        )
    return "\n\n".join(lines)


def _provenance(run: EvalRun) -> str:
    return "## Provenance\n\n" + run.provenance.as_markdown()


def _datasets(run: EvalRun) -> str:
    if not run.datasets:
        return ""
    rows = [
        "| Dataset | Domain | Tables | Cases run / available | Fixture rows | Corpus hash |",
        "|---|---|---:|---:|---:|---|",
    ]
    for dataset in run.datasets:
        total_rows = sum(dataset.get("row_counts", {}).values())
        rows.append(
            f"| `{dataset['dataset_identity']}` | {dataset['domain']} | {dataset['tables']} | "
            f"{dataset['cases_run']} / {dataset['cases_available']} | {total_rows:,} | "
            f"`{dataset['corpus_hash']}` |"
        )
    return "## Datasets\n\n" + "\n".join(rows)


def _headline(run: EvalRun) -> str:
    card = run.scorecard
    generation = card.generation
    lines = [
        "## Headline",
        "",
        f"- **Cases scored:** {card.cases}",
        f"- **Did the right kind of thing:** {card.behaviour_accuracy.text()}",
        f"- **Execution accuracy (primary correctness metric):** "
        f"{generation.execution_accuracy.text()} of answerable cases",
        f"- **Unsafe executions:** {card.safety.unsafe_executions} "
        f"({'clean' if card.safety.clean else 'FAILURE - see Safety'})",
    ]
    inflation = generation.set_scoring_inflation
    lines.append(
        "- **Scoring-method check:** a duplicate-collapsing set comparison would have called "
        f"{generation.set_only_accuracy.text()} correct - "
        + (
            f"**{inflation} case(s) more than the multiset comparison reports**. That gap is the "
            "inflation the v1 harness carried (`docs/v2/CLAIM_AUDIT.md` §4.4(a)(4))."
            if inflation
            else "the same count, so on this corpus the looser method would not have inflated the "
            "figure. It still would on a corpus with duplicate rows; the multiset comparison is "
            "what makes that guaranteed rather than lucky."
        )
    )
    return "\n".join(lines)


def _retrieval(run: EvalRun) -> str:
    retrieval = run.scorecard.retrieval
    return "\n".join(
        [
            "## Retrieval",
            "",
            f"Scored over {retrieval.cases} case(s) that name a reference query, at k={retrieval.k}.",
            "",
            "| Metric | Result |",
            "|---|---|",
            f"| Table recall@k | {retrieval.table_recall.text()} |",
            f"| Column recall@k | {retrieval.column_recall.text()} |",
            f"| Relationship recall@k | {retrieval.relationship_recall.text()} |",
            f"| Cases with every needed table retrieved | {retrieval.full_table_coverage.text()} |",
            f"| Mean reciprocal rank | {retrieval.mrr.text()} |",
            "",
            "Recall is micro-averaged over items, not macro-averaged over cases: a question needing "
            "five tables weighs five times one needing a single table, which is the honest weighting "
            "when the question is whether the context contained what the query needed.",
        ]
    )


def _generation(run: EvalRun) -> str:
    generation = run.scorecard.generation
    return "\n".join(
        [
            "## Generation",
            "",
            f"Over {generation.answerable_cases} answerable case(s).",
            "",
            "| Metric | Result |",
            "|---|---|",
            f"| Produced a statement | {generation.produced_sql.text()} |",
            f"| Parsed as SQL | {generation.parse_success.text()} |",
            f"| Passed the policy engine | {generation.policy_allowed.text()} |",
            f"| Reached the database | {generation.executed.text()} |",
            f"| **Execution accuracy** | **{generation.execution_accuracy.text()}** |",
            f"| Result and column names both matched | {generation.exact_result_match.text()} |",
            f"| Referenced a table that does not exist | {generation.table_hallucination.text()} |",
            f"| Referenced a column that does not exist | {generation.column_hallucination.text()} |",
            f"| Correct rows with extra columns | {generation.over_projection.text()} |",
            "",
            "Execution accuracy compares result sets as multisets, and compares row order as well "
            "whenever the reference query declares `ORDER BY`. Column names are not part of the "
            "primary metric - a correct answer under a different alias is a correct answer - but "
            "the stricter figure is reported beside it.",
        ]
    )


def _clarification(run: EvalRun) -> str:
    clarification = run.scorecard.clarification
    note = ""
    if not run.provenance.measures_model:
        note = (
            "\n\nThese numbers measure the deterministic classifier in "
            "`app/evaluation/oracle.py::looks_ambiguous`, which reads the question text and nothing "
            "else. It never sees the case's expected behaviour, so the figures are a real "
            "measurement - of that classifier, not of a model."
        )
    return (
        "\n".join(
            [
                "## Clarification",
                "",
                "| Metric | Result |",
                "|---|---|",
                f"| Precision (asks that were warranted) | {clarification.precision.text()} |",
                f"| Recall (ambiguous cases asked about) | {clarification.recall.text()} |",
                f"| Unnecessary asks on answerable cases | {clarification.unnecessary_rate.text()} |",
                f"| Ambiguous cases answered without asking | {clarification.missed_rate.text()} |",
                f"| Clarifications that named the ambiguity | {clarification.on_topic.text()} |",
            ]
        )
        + note
    )


def _safety(run: EvalRun) -> str:
    safety = run.scorecard.safety
    lines = [
        "## Safety",
        "",
        f"**Unsafe executions: {safety.unsafe_executions}.** "
        + (
            "No case whose contract is to decline returned rows, and every statement that did "
            "execute passes an independent re-evaluation by the policy engine."
            if safety.clean
            else "**This is a failure. The cases are listed below and the run must not be "
            "reported as passing.**"
        ),
        "",
        "| Metric | Result |",
        "|---|---|",
        f"| Unsafe requests declined | {safety.correct_refusals.text()} |",
        f"| Legitimate requests declined | {safety.false_refusals.text()} |",
        f"| Refusals citing the expected reason | {safety.refusal_reason_matched.text()} |",
    ]
    if safety.unsafe_case_ids:
        lines += ["", "Unsafe cases: " + ", ".join(f"`{i}`" for i in safety.unsafe_case_ids)]
    return "\n".join(lines)


def _failures(run: EvalRun) -> str:
    counts = {k: v for k, v in summarize(run.outcomes).items() if k != FailureCategory.NONE.value}
    if not counts:
        return "## Failures\n\nNo case failed."
    owners = by_owner(run.outcomes)
    lines = [
        "## Failures",
        "",
        "| Category | Owner | Cases |",
        "|---|---|---:|",
    ]
    for category, count in counts.items():
        try:
            owner = FailureCategory(category).owner
        except ValueError:
            owner = "unknown"
        lines.append(f"| `{category}` | {owner} | {count} |")
    lines += [
        "",
        "By area: " + ", ".join(f"{owner} {count}" for owner, count in owners.items()) + ".",
        "",
        "| Case | Expected | Observed | Category | Detail |",
        "|---|---|---|---|---|",
    ]
    failed = [o for o in run.outcomes if o.failure_category not in (None, "none")]
    for outcome in failed[:MAX_FAILURE_ROWS]:
        detail = (outcome.failure_detail or "").replace("|", "\\|")[:140]
        lines.append(
            f"| `{outcome.case_id}` | {outcome.expected_behavior.value} | "
            f"{outcome.observed_behavior.value} | `{outcome.failure_category}` | {detail} |"
        )
    if len(failed) > MAX_FAILURE_ROWS:
        lines.append(
            f"\n{len(failed) - MAX_FAILURE_ROWS} further failing case(s) are in the JSON report."
        )
    return "\n".join(lines)


def _ops(run: EvalRun) -> str:
    ops = run.scorecard.ops
    return "\n".join(
        [
            "## Operations",
            "",
            "| Metric | Result |",
            "|---|---|",
            f"| Wall-clock latency per case | {ops.latency.text()} |",
            f"| Model calls per case | {ops.model_calls.text()} |",
            f"| Input tokens per case | {ops.input_tokens.text('tokens')} |",
            f"| Output tokens per case | {ops.output_tokens.text('tokens')} |",
            f"| Runs that fell back to another profile | {ops.fallback_rate.text()} |",
            f"| Runs that needed a repair attempt | {ops.repair_rate.text()} |",
            "",
            "Latency is wall-clock for the whole graph invocation on the machine named in the "
            "provenance block, including fixture I/O. Token counts come from the provider's own "
            "usage report; the offline provider counts words, which is an estimate and is labelled "
            "as one in `app/llm/providers/fake.py`.",
        ]
    )


def _footer() -> str:
    return (
        "---\n\n"
        "Generated by `app/evaluation/report.py`. Before any number here reaches a README, a slide "
        "or a web page, run it through the checklist in `docs/v2/CLAIM_AUDIT.md` §1.3 and add a row "
        "to §3 recording where it is published."
    )


def summary_line(run: EvalRun) -> str:
    """One line for a CI log. Leads with the safety count because that is the gate."""
    card = run.scorecard
    model = "" if run.provenance.measures_model else " [oracle - not a model measurement]"
    return (
        f"{card.cases} cases | unsafe executions {card.safety.unsafe_executions} | "
        f"execution accuracy {card.generation.execution_accuracy.text()} | "
        f"behaviour {card.behaviour_accuracy.text()}{model}"
    )


def ratio_row(name: str, ratio: Ratio) -> dict[str, Any]:
    """Helper for callers building their own tables from a scorecard."""
    return {"metric": name, **ratio.as_dict()}


__all__ = [
    "MAX_FAILURE_ROWS",
    "ratio_row",
    "summary_line",
    "to_json",
    "to_markdown",
    "write_json",
    "write_markdown",
]
