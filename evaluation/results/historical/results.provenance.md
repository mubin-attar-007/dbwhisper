# Provenance — `results.json`

**Classification: NOT PUBLISHABLE.** Four independent disqualifiers, any one of which is sufficient.
Recorded in `docs/v2/CLAIM_AUDIT.md` §4.4(a) and §4.4(b); this file restates the block so the number
never travels without it.

## The block

> **Metric produced:** `exec_accuracy: 0.8181818181818182` — published as "82% execution accuracy"
> and "18 of 22". Also `fail_closed: 1.0`, `unsafe: [4, 4]` — published as "100% fail-closed on
> unsafe input".
> **Dataset:** `eval/golden.json`, a 22-question golden set (18 answerable + 4 unsafe). Untracked;
> not versioned; no content hash was recorded at the time.
> **n:** 22 questions, of which 18 answerable and 4 unsafe.
> **Database:** a four-table PostgreSQL demo store (`customers`, `orders`, `order_items`,
> `products`) reached over `127.0.0.1:55432`. Not a fixture that ships with the repository.
> **Model:** `qwen/qwen3-32b` via Groq, temperature 0.1 (inferred from the sibling scripts; the file
> itself records no model field).
> **Prompt version:** `sql_agent_prompt@1.0`. **RETIRED on 2026-08-21** — `app/agent/prompt.py:5-7`
> records that it was "hard-coded to a single SQL Server 'DME' schema … used for *every* enrolled
> database".
> **Date:** 2026-07-08 (file mtime 17:37).
> **Environment:** the author's workstation, with the API listening on `127.0.0.1:8010` and a
> Postgres eval store on `127.0.0.1:55432`. Neither is part of the repository.
> **Exclusions:** none recorded. The harness scored every row it received, including rows where the
> API returned an error.
> **Limitations:** see below — they are severe enough to be disqualifiers rather than caveats.

## Reproducible today? No.

* `git ls-files eval` → `0`. The harness (`eval/harness.py`), the dataset (`eval/golden.json`) and
  this result file are all untracked. A fresh clone does not contain the measurement.
* The API at `127.0.0.1:8010` that `eval/harness.py:18` posts to refuses connections.
* The eval-store DSN at `eval/rescore.py:14` returns
  `FATAL: password authentication failed for user "eval"`.
* Even the no-new-LLM-calls re-score cannot run. See `rescore.provenance.md`.

## Why the accuracy figure cannot be published

1. **Not reproducible** — the harness is not in the repository (above).
2. **Not re-runnable here either** — both services it depends on are unreachable.
3. **Measured with a deleted prompt.** The contamination is visible *inside the recorded run*: row 1
   generated `SELECT COUNT(*) AS total_customers FROM CompanyMaster;` against a store whose tables
   are `customers, orders, order_items, products`. `CompanyMaster` is a table from the hard-coded
   schema baked into `sql_agent_prompt@1.0`. The number describes software that no longer exists.
4. **The scoring is looser than advertised.** `eval/harness.py:35-41` defines `as_set()` returning a
   **`frozenset`**, which collapses duplicate rows and discards ordering. The project's own web copy
   called this "Spider-style"; standard Spider execution match compares multisets and respects order
   under `ORDER BY`, which the repository's own Spider script (`eval/spider_eval.py:5-6`) does.

## Why the "100% fail-closed" figure cannot be published

This is not a stale number — it is **the wrong measurement**.

The scoring predicate (`eval/harness.py:94`) requires `valid and status == "success"` before it can
register a safety failure, and `:64-67` fabricates an error response when the API call throws. So an
outage scores as a pass. All four recorded `gen_sql` values are English prose with
`validation_passed=False`: the model declined at generation time and **nothing ever reached the
validator**. The run therefore demonstrates that the model did not emit unsafe SQL on four prompts.
It does not demonstrate that the validator blocks unsafe SQL, which is what "fail-closed" claims.

## What the four misses actually were

Worth recording because it is more flattering than the headline, and still unpublishable:

| id | Detail from the file | Reading |
|---|---|---|
| 1 | `no result (status=error, valid=False)`, `gen_sql` referenced `CompanyMaster` | hallucinated table leaked from prompt@1.0 |
| 8 | `gen=[('Mumbai', 3)]` vs `gold=[('Mumbai',)]` | right answer, one extra column |
| 18 | `gen=[('Mechanical Keyboard', 3499), …]` vs `gold=[('Mechanical Keyboard',), …]` | right answer, one extra column |
| 22 | `gen=[('Aisha Khan', 3)]` vs `gold=[('Aisha Khan',)]` | right answer, one extra column |

Three of the four "failures" are projection-shape mismatches, not wrong answers. The v2 metrics
detect this case explicitly and report it separately
(`app/evaluation/metrics.py`, `gold_is_projection_of_predicted`) — as a diagnostic, never as a match.

## If it must be quoted at all

`docs/v2/CLAIM_AUDIT.md` §4.5 permits exactly one form, and recommends against using it:

> "On a 22-question golden set run on 2026-07-08 against a four-table PostgreSQL store, 18 of 22
> generated queries matched the reference result set exactly; three of the four misses returned the
> correct rows with one extra column. That run used prompt `sql_agent_prompt@1.0`, which was retired
> on 2026-08-21, and it has not been reproduced since."

The standing recommendation is to publish neither this nor the fail-closed figure, and to lead with
the policy corpus result in §4.1, which anyone can reproduce with `uv run pytest tests/sqlpolicy`.
