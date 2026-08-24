# Evaluation

How to run the evaluation suite, and — more importantly — how to read what it gives you back.

This document exists because the previous evaluation harness produced numbers that had to be
withdrawn. Not because they were bad numbers: because nobody, including their author, could
reproduce them. The harness was never committed, the API it posted to no longer existed, the prompt
it measured had been deleted, and its scoring predicate could not distinguish "the model refused"
from "the service was down". The post-mortem is `docs/v2/CLAIM_AUDIT.md` §4.4 and it is worth ten
minutes of your time before you publish any figure of your own.

Everything that replaced it is in the repository, runs offline, and prints its own provenance.

---

## The two kinds of measurement

They answer different questions and are never mixed.

| | **Policy corpus** | **Pipeline evaluation** |
|---|---|---|
| Asks | does the policy engine make the right decision? | does the whole pipeline do the right kind of thing? |
| Lives in | `app/evaluation/datasets/adversarial/sql_policy_cases.yaml` | `app/evaluation/datasets/cases/*.yaml` |
| Run with | `uv run pytest tests/sqlpolicy` | `scripts/eval-smoke.sh` |
| Model involved | **none** — a deterministic decision function | depends on `--provider` |
| Touches a database | **no** — nothing is ever executed | yes, generated SQLite fixtures |
| Reproducible to the case | yes | yes with the oracle; approximately with a model |

If you only run one thing, run the policy corpus. It is the only headline-quality number in this
project that a stranger with a clone can reproduce exactly.

---

## The policy corpus

```bash
uv run pytest tests/sqlpolicy
```

255 cases, each declaring which dialects it applies to, expanded into 577 case×dialect combinations:
343 expected-deny, 210 expected-allow, 24 expected-needs-approval. Categories include write, DDL,
multi-statement, catalog access, admin, exec, file and network functions, obfuscation, denial of
service, cross-database references, unknown tables, sensitive columns — and, deliberately, 61
false-positive cases whose job is to catch a policy engine that has become too strict to be useful.

Nothing in this corpus is ever sent to a database. It measures the *decision*.

**What a passing run does and does not tell you.** It says there is no bypass among the attacks
somebody thought to write down. It does not say there is no bypass. The engine decides on a parsed
AST, so its guarantee is bounded by SQLGlot's parse fidelity per dialect — which is why the
architecture calls it a structural filter and not a proof.

---

## The pipeline evaluation

One entry point, several sub-commands:

```bash
python -m app.evaluation.cli <command> [options]
```

With wrappers that pin the offline providers and a run label for you (`.sh` for bash, `.ps1` for
PowerShell — both take the same extra arguments):

| Command | Wrapper | What it is for |
|---|---|---|
| `smoke` | `scripts/eval-smoke.sh` | **The CI gate.** Whole corpus, offline oracle, exits non-zero on any unsafe execution. |
| `custom` | `scripts/eval-report.sh` | Run the corpus with a chosen provider. This is the one that produces a publishable number. |
| `safety` | `scripts/eval-safety.sh` | Only the cases whose contract is to decline. The narrowest gate; run it on every policy change. |
| `retrieval` | `scripts/eval-retrieval.sh` | Retrieval metrics only, no model and no execution. Seconds, not minutes. |
| `report` | — | Re-render a stored JSON run as Markdown. |
| `build` | — | Regenerate fixtures and enrollment artefacts, print their digests. |
| `spider` / `bird` | — | Print what a person must do by hand to run an external benchmark. These download nothing. |

Common options: `--dataset` (repeatable), `--limit`, `--tag`, `--case`, `--out`, `--label`,
`--rebuild`, `--quiet`.

Exit codes: `0` success, `1` a gate failed (an unsafe execution, or a threshold you set), `2` the
command could not run at all.

### The datasets

Three synthetic domains, versioned, generated from code in `app/evaluation/datasets/`:

| Dataset | Domain | Tables | Cases | Fixture rows |
|---|---|---:|---:|---:|
| `retail@1.0.0` | retail | 9 | 63 | 4,475 |
| `saas_ops@1.0.0` | SaaS operations | 9 | 53 | 4,721 |
| `synthetic_snf@1.0.0` | synthetic healthcare | 10 | 53 | 7,899 |

Every row is generated. No row describes a real person, organisation, transaction or clinical
record, and the report says so at the top of every run.

### The smoke run

```bash
scripts/eval-smoke.sh                       # or ./scripts/eval-smoke.ps1
scripts/eval-smoke.sh --dataset retail --limit 10
```

RAN on this branch, 2026-08-24:

```
169 cases | unsafe executions 0 | execution accuracy 135 of 135 (100.0%)
         | behaviour 167 of 169 (98.8%) [oracle - not a model measurement]
```

That last clause is the whole point of this section. **The smoke run replays each case's reference
SQL through the pipeline.** It genuinely exercises retrieval, the policy engine, the execution path,
result comparison and the safety path — and it says nothing whatsoever about how well a model writes
SQL. The harness enforces that distinction rather than trusting you to remember it: `Provenance`
carries a `measures_model` flag, and when it is false the report prints the disclaimer in a banner,
in the summary, and next to the accuracy figure.

### Running it against a real model

```bash
MODEL_PROFILE=local-small scripts/eval-report.sh --provider local-small
# or a hosted provider, if its key is set:
scripts/eval-report.sh --provider gemini-flash --min-execution-accuracy 0.7
```

`--min-execution-accuracy` makes the run a gate: it exits non-zero below the threshold you name.
`--resume-clarifications` replays a paused run with the case's stored answer, which changes what is
being measured — the report records that it was used.

A model run is not bit-for-bit reproducible; a temperature above zero and a provider's own drift see
to that. Publish it with its date, its model identifier and its prompt versions — all of which the
provenance block already carries — and re-run before you quote it again.

---

## How to read a provenance block

Every run leads with one. Here is a real one, from the smoke run above, with the fields that matter
called out.

```
> Dataset: `retail@1.0.0, saas_ops@1.0.0, synthetic_snf@1.0.0` (corpus hash `sha256:54dff68d…`)
> n: 169 cases scored of 169 in the corpus
> Model: `fake/oracle` / `reference SQL replay`
> Prompt versions: generate_sql@2.0, repair_sql@2.0, sql_policy@2.0.0, grounded_summary@2.0, understand@2.0
> Component versions: embedding_profile=fake, graph=query_graph@2.0, model_registry=models@5c42882e2532
> Date / environment: 2026-08-24 12:42 UTC, Windows 10, Python 3.13.13
> Fixture digests: retail=sha256:079482c4…, saas_ops=sha256:07c86c4b…, snf=sha256:275eaf3b…
> Exclusions: none - every case ran
> Limitations: (a) THE SQL WAS NOT GENERATED BY A MODEL. …
> Reproduce: `uv run python -m app.evaluation.cli smoke`
> NOT A MODEL MEASUREMENT. …
```

Read it in this order:

1. **`measures_model` / the banner.** If it says the run did not measure a model, no correctness
   figure in the report is a statement about model quality. Stop treating it as one.
2. **`n` and `n_available`.** "169 of 169" means nothing was skipped. A gap is a filter or a
   failure, and `exclusions` says which.
3. **Dataset version and corpus hash.** The hash covers the case file. If it changed, the number is
   not comparable to the previous run even if the version string did not move.
4. **Fixture digests.** These cover the generated SQLite databases. Two runs with the same digests
   ran against byte-identical data — which is what makes a regression a regression rather than a
   reseed.
5. **Prompt and component versions.** A number without a prompt version is not re-checkable after
   the next prompt change. This is the field that made the old 82% figure impossible to defend.
6. **Exclusions.** Anything dropped rather than scored. Dropping failures moves a figure up; if
   `n` and `n_available` differ, the exclusions list is the first thing to read.
7. **Limitations.** Not boilerplate. The smoke run's list currently names seven specific ones,
   including that every case runs against SQLite, that clarification metrics measure a deterministic
   classifier rather than a model, and that two named cases carry no reference query so their refusal
   originates in the provider rather than in the policy engine.
8. **Reproduce.** The exact command. If it does not work from a clean checkout, the number is not
   publishable — that is the rule that retired the previous harness.

---

## How to read the report body

**Headline.** Cases scored, "did the right kind of thing", execution accuracy, unsafe executions.

**The scoring-method check** is the section to read if you are sceptical. Execution accuracy compares
result sets as **multisets**, and compares row order too whenever the reference query declares
`ORDER BY`. The old harness compared `frozenset`s, so `[Mumbai, Mumbai, Delhi]` and `[Mumbai, Delhi]`
scored equal and a "top 5" sorted backwards scored correct. The report computes the looser verdict as
well — not to publish, but to state how much the old method would have inflated the figure. On the
current corpus the two agree, which the report says explicitly rather than letting you assume it
generalises.

**Retrieval.** Table, column and relationship recall at `k`, micro-averaged over items rather than
macro-averaged over cases: a question needing five tables weighs five times one needing a single
table, which is the honest weighting when the question is whether the context contained what the
query needed. Relationship recall is the weakest number in the current run (38 of 82) and is not
smoothed over.

**Safety.** Unsafe executions is the gate: it is true only when rows were actually fetched for a case
whose contract is to decline. It is a fact about the run, not a judgement — a run that failed for an
unrelated reason is not an unsafe execution, and a refusal that happened for the wrong reason is
still not one. The wrong-reason case shows up separately as "refusals citing the expected reason".

**Failures.** Categorised and attributed to an owning area (policy, retrieval, generation, harness)
so the list is actionable rather than a wall of diffs. Up to 25 are listed in full; the rest are in
the JSON.

**Operations.** Latency, model calls, tokens, fallbacks and repairs — each with its `n`, because
"p95 340 ms" over four runs is not a latency measurement. Token counts come from the provider's own
usage report; the offline provider counts words, and the report labels that as an estimate.

---

## Artefacts, and re-scoring a stored run

`--out <dir>` writes `<label>.json` and `<label>.md`. **The JSON is the durable artefact**: it holds
every case outcome, so a stored run can be re-scored after a metric is fixed.

```bash
python -m app.evaluation.cli report evaluation/results/eval-smoke.json --out evaluation/results
```

That capability is a direct response to the previous harness's failure: when its scoring predicate
turned out to be wrong, there was no way to re-score the runs it had already produced, so the runs
were simply lost. Keep the JSON.

CI uploads the smoke report as a build artifact on every run, passing or failing.

---

## In CI

The `eval-smoke` job runs `scripts/eval-smoke.sh` with `MODEL_PROFILE=fake` and
`EMBEDDING_PROFILE=fake`. It downloads no model, needs no credentials, and touches no network. Its
exit code is the gate: non-zero means a request whose contract was to be declined returned rows, or
a statement that executed failed an independent re-check by the policy engine.

The `backend` job separately runs the policy corpus as part of `pytest`.

**What is deliberately not in CI:** any run against a real model. It would need a key, cost money,
and produce a number that varies between runs — a bad gate and a worse metric. Model runs are
manual, dated, and published with their provenance block or not at all.

---

## Before you publish a number

The rule, from `docs/v2/CLAIM_AUDIT.md` §5, in order — the order matters because it is what makes the
claim true:

1. **Measure it with a harness inside the repository.** If `git ls-files <harness-dir>` returns
   nothing, the number does not exist for publication purposes.
2. **Make it re-runnable by a stranger** — no dependence on a service running on your laptop, no
   credentials baked into a DSN, and a loud failure if a key is required.
3. **Read the scoring predicate line by line** and ask what *else* would produce this number. If an
   outage, a rate-limit or an empty result would score as a pass, you are measuring availability or
   emptiness, not the property you are naming.
4. **Record the provenance block.** The harness does this for you; do not strip it.
5. **Add a row to `docs/v2/CLAIM_AUDIT.md` §3** — verbatim claim, `path:line`, classification,
   evidence, and the wording you intend to publish.
6. **Run the §1.3 wording checklist.** Every box. No absolutes in a safety claim, no percentage
   without a numerator and a denominator, no metric without provenance.
7. **Only then write it in the UI**, and cite the reproducing command so a sceptic has somewhere to go.

And when a claim is retired, say so, with the date and the reason. The version history at the top of
`app/agent/prompt.py` is the pattern: it is the only reason the staleness of the old accuracy figure
could be established at all.
