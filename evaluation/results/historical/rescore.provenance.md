# Provenance — `rescore.json`

**Classification: UNPROVABLE (harness defect). NOT PUBLISHABLE.**

The whole file is nine numbers:

```json
{"strict": [3, 22], "answer_correct": [3, 22], "fail_closed": [4, 4]}
```

It has been read as "only 3 of 22 were actually correct". It is not evidence that accuracy is 14%.
It is evidence that the re-scoring script was broken.

## The block

> **Metric produced:** `strict: [3, 22]`, `answer_correct: [3, 22]`, `fail_closed: [4, 4]`.
> **Dataset:** the same untracked 22-question golden set as `results.json`.
> **n:** 22.
> **Model:** none for the re-score path by design — it replays the SQL already stored in
> `results.json` — but `eval/rescore.py:51-53` falls back to a **live API call** when the stored SQL
> fails, so some rows did involve a model. Which rows, and which model, is not recorded.
> **Prompt version:** `sql_agent_prompt@1.0` for the stored SQL (retired 2026-08-21); unknown for
> any live retry.
> **Date:** 2026-07-08, file mtime 17:55.
> **Environment:** the author's workstation. `eval/server.log` (mtime 17:55, the same minute) shows
> the API serving `/query` at 17:54–17:55 and stopping mid-request at `17:55:10`.
> **Exclusions:** none recorded.
> **Limitations:** the mechanism below. The output is not a measurement of query accuracy.

## Reproducible today? No.

The API endpoint refuses connections and the eval-store credentials are rejected
(`FATAL: password authentication failed for user "eval"`).

## The defect

Derived by reading `eval/rescore.py`, not by reproducing it — the audit says so explicitly
(`docs/v2/CLAIM_AUDIT.md` §6.1) and so does this file.

1. `run()` at `:28-30` calls `cur.connection.rollback()` **only on success**.
2. The call sites at `:51-53` and `:60-61` swallow the exception without rolling back.
3. Under PostgreSQL semantics the connection then sits in a failed transaction, and every later
   `execute` raises `InFailedSqlTransaction`.
4. Row id 1 is the first answerable row, and its saved SQL references the non-existent
   `CompanyMaster` (see `results.provenance.md`). It raises, and poisons the connection for the rest
   of the loop.
5. Downstream at `:60-69`: the gold SQL also raises, so `g = []` and `gset = frozenset()`. If the
   live-API retry succeeded, a non-empty generated set scores **wrong**; if the retry failed,
   `gen = None` and empty-vs-empty scores **right**.

So "3" most consistently reads as *three rows whose live retry also failed*, not as three correct
answers. Two corroborations:

* `strict` and `answer_correct` are both exactly 3/22 — a tell in itself. The lenient path (`:68`)
  requires `len(gen) == len(g)`, and with `g` always empty it can only agree with the empty-match
  cases, so the two metrics collapse into one.
* `eval/server.log` shows the API dying mid-run at 17:55:10, the same minute this file was written.

## Status

**UNPROVABLE.** What is certain regardless of whether the `InFailedSqlTransaction` hypothesis is
exactly right: `3/22` is not a measurement of query accuracy, and the script never rolls back on the
error path. Do not quote this number in any form.

## What the v2 harness does instead

`app/evaluation/runner.py` computes the reference result by opening the generated fixture read-only
in a fresh connection per statement (`app/evaluation/datasets/build.py::run_readonly`), so one
failing statement cannot affect any other. A reference query that errors is recorded as
`gold_sql_error` and classified as `GOLD_SQL_ERROR` — a *harness* failure, loudly, rather than being
silently scored as a model failure. `tests/evaluation/test_gold_sql.py` fails the build if any
reference query errors at all.
