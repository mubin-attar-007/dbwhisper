# ADR 0012 — Deterministic code wherever a model is not strictly needed

**Status:** Accepted — 2026-08-21, Phase 6.
**Principle:** "Deterministic where possible" — `docs/v2/TARGET_ARCHITECTURE.md` §2.

## Context

It is easy to reach for a model for everything once one is already in the request path: let it pick
the chart, let it describe the result, let it decide whether the answer looks right. Each of those
adds latency, adds a failure mode that cannot be unit-tested, and makes an evaluation run
irreproducible — the same question yields a different pipeline on a different day. It also creates
a dependency on a hosted service for behaviour that has nothing to do with language.

## Decision

A model is called for the three things that genuinely need one — classifying a question, writing
SQL, and writing prose. Everything else is ordinary code:

* **Result statistics.** `app/execution/results.py::compute_stats` — per-column types, cardinality,
  ranges, null counts, computed once from the `ResultFrame`.
* **Chart selection.** `app/analysis/charts.py` picks a chart from column types, cardinality and row
  count, and every `ChartSpec` carries the `reason` it was chosen so the UI can show it and a
  reviewer can disagree. A table is always a valid answer; when nothing charts well, that is what
  comes back.
* **Shape verification.** `app/analysis/shape.py` compares the result against what the question
  asked for — "top 5" returning 500 rows, a trend with no time column, a comparison missing a group
  — before any prose is written about it.
* **Summary fallback.** `app/analysis/summary.py::deterministic_summary` describes the result from
  the statistics alone. It is what a `LOCAL_ONLY` deployment with no model gets, what a provider
  outage falls back to, and what the evaluation harness compares against.
* **Limit injection and pagination** (`app/sqlpolicy/limits.py`), **retrieval fusion**
  (ADR 0011), and **PII classification** (`app/security/pii.py`, a pure function of `ColumnFacts`).

## Consequences

**What it buys.** These stages are unit-testable with no provider, they cost nothing, they cannot
hallucinate, and they make an evaluation run repeatable — which is the precondition for any number
this project is allowed to publish (ADR 0016). An answer still exists when no model may be used at
all.

**What it costs.**

* **Rules only cover the shapes we anticipated.** A chart heuristic is a set of cases someone wrote
  down; an unusual result gets a table. A model would be more imaginative and less predictable, and
  we chose predictable.
* **The deterministic summary reads like statistics**, because it is statistics. It is a floor, not
  a substitute for the grounded summary, and it should not be presented as one.
* **Shape checks are deliberately conservative** — an empty table is not a failure, one row is not
  "too few" — so they miss subtler mismatches. A green shape check is not evidence that the query
  answered the question (see ADR 0013).
* **More code to maintain than a prompt.** Three analysis modules exist where one prompt would have
  produced something plausible on day one.
* **Determinism does not mean correctness.** `app/security/pii.py` classifies deterministically and
  is measured mostly by its false-positive block in `tests/test_pii.py`; it has no callers yet
  (ADR 0008), so its determinism currently buys nothing in production.
