# ADR 0016 — Evaluation runs the real pipeline, and every number carries provenance

**Status:** Accepted — 2026-08-24, Phase 8.
**Principle:** "Truthful evidence" — `docs/v2/TARGET_ARCHITECTURE.md` §2, §14; rules in
`docs/v2/CLAIM_AUDIT.md`.

## Context

The v1 evaluation harness was untracked (`git ls-files eval | wc -l` → `0`, recorded in
`CLAIM_AUDIT.md` §1.4), needed a live API and a hosted key, and one of its scripts embedded a DSN
whose credentials no longer authenticate. Two headline metrics derived from it could not be
reproduced, and one measured something other than what it said. That is not an evaluation
subsystem; it is an anecdote with a number attached.

Rebuilding it raised the question that shapes this ADR: an offline harness must substitute
*something* for the model, and whatever it substitutes determines what the resulting numbers may be
said to mean.

## Decision

**There is no evaluation-only code path.** `app/evaluation/runner.py` runs each case through
`app/graph/query_graph.compile_query_graph` with a durable checkpointer, the same retrieval index
construction the API uses, the same policy engine reading a real `schema_index.yaml`, and the same
execution service. The only substituted component is the model provider.

**The oracle is honest about what it is.** `app/evaluation/oracle.py` replays the dataset's
reference SQL. Its module docstring opens by saying that an execution-accuracy number produced this
way **is not a model result**, and `Provenance.measures_model=False` carries that fact into the
report so it cannot be quietly forgotten. What an oracle run *does* measure is stated as
specifically: retrieval surfaces the tables the reference query needs; the policy engine allows them
against the real scope; execution runs read-only and returns the expected rows; the comparison,
taxonomy and report are correct end to end; and **no unsafe request executes** — that last one is a
real result, because the safety cases replay genuinely unsafe statements through the whole graph.

**Provenance is a dataclass, not a convention.** `app/evaluation/provenance.py` requires dataset and
version, n, model, prompt version, date, environment and exclusions; `missing_fields()` refuses to
report a run with holes in it.

**The safety invariant is checked twice.** A case whose contract is "decline" fails if rows came
back, and — independently — every statement that *did* execute is re-evaluated by the policy engine
afterwards. If those two ever disagree, something bypassed the gate.

**CI gates on it.** The `eval-smoke` job runs `scripts/eval-smoke.sh` with `MODEL_PROFILE=fake`; a
non-zero exit means an unsafe execution or a failed independent re-check.

**Outcomes are re-scorable.** Each case produces a JSON-serialisable `CaseOutcome`, and metrics are
pure functions over a list of them, so a wrong scoring predicate is fixed without another run.

## Consequences

**What it buys.** A smoke run from a clean checkout with no key, no network and no model download.
A safety property that is checked on every pull request rather than argued about. Numbers that carry
enough context for a stranger to reproduce or reject them.

Reproduced while writing this ADR (2026-08-24, branch `feat/dbwhisper-v2`, Windows 10, Python
3.13, `MODEL_PROFILE=fake`, oracle provider): `scripts/eval-smoke.sh` completed 169 cases with
**0 unsafe executions**, exit code 0, and the summary line printed
`[oracle - not a model measurement]` next to the accuracy figure — which is the labelling this ADR
exists to require, and the reason that accuracy figure is not quoted here.

**What it costs.**

* **The most quotable number from an oracle run is the one that means least.** "Execution accuracy"
  under the oracle is a property of the dataset author's SQL. It is labelled in three places for
  that reason, and it will still be misquoted eventually — a permanent cost of publishing it at all.
* **The corpus is ours**, so it measures what we thought to ask. Spider and BIRD adapters download
  on demand and are deliberately not part of CI, which keeps CI offline and keeps our numbers
  self-referential.
* **No model-matrix result is published.** Nothing here yet says how a local `qwen2.5-coder` model
  actually performs (ADR 0004), so no accuracy claim about DBWhisper-with-a-model is currently
  permitted.
* **Running the real pipeline makes the harness fragile in a useful way**: a change to the graph, the
  policy engine or the execution service can fail the eval job, and that is the intended behaviour
  rather than a flake to be retried.
