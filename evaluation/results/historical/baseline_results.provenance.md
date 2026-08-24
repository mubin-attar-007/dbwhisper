# Provenance — `baseline_results.json`

**Classification: NOT PUBLISHABLE ALONE.** The file records a naive baseline scoring **22 of 22
(exec_accuracy 1.0)** — higher than the pipeline's advertised 82% on the same golden questions. That
comparison is not usable in either direction without the confounders below, and the file also
contradicts itself about which model produced it.

## The block

> **Metric produced:** `n: 22`, `correct: 22`, `exec_accuracy: 1.0`.
> **Method (verbatim from the file):** "naive single-prompt (full schema, no retrieval, no
> validator), temp 0.1, exec-accuracy vs gold on eval_store.sqlite — same model/temp as the
> DBWhisper pipeline".
> **Dataset:** the same untracked 22-question golden set, but run against
> **`eval/eval_store.sqlite`** (`eval/baseline.py:32,59-65`) — a SQLite file, **not** the PostgreSQL
> store the pipeline run used.
> **n:** 22.
> **Model:** the file's `model` field says `"qwen/qwen3-32b (Groq)"` (`:67`) and the code uses
> `ChatGroq(model="qwen/qwen3-32b")` (`:81`) — while the module docstring (`:3`) claims "Same model
> (gemini-2.5-flash)". **The file contradicts itself.** Take the code and the recorded field over
> the docstring, and treat the discrepancy as a reason to distrust the rest of the metadata.
> **Prompt version:** none — the baseline uses its own inline prompt, "Return ONLY the SQL"
> (`eval/baseline.py:33-39`). It is not `sql_agent_prompt@1.0` and it is not versioned.
> **Date:** 2026-07-10 (file mtime 11:11).
> **Environment:** the author's workstation, with a `GROQ_API_KEY` present.
> **Exclusions:** none recorded.
> **Limitations:** the confounders below.

## Reproducible today? No.

Untracked (`git ls-files eval` → `0`), depends on a provider key, and depends on
`eval/eval_store.sqlite`, which is a local artefact rather than a generated fixture.

## Confounders that make the head-to-head comparison invalid

1. **Different database.** The baseline ran against SQLite; the pipeline run (`results.json`) ran
   against PostgreSQL. Dialect differences alone can move an execution-match score.
2. **Different prompt, and one that suits the metric.** "Return ONLY the SQL" naturally produces
   minimal projections. Three of the pipeline's four misses were *extra-column* mismatches
   (see `results.provenance.md`) — precisely the failure mode this prompt avoids by accident. A
   noticeable part of the gap is prompt shape, not capability.
3. **Different scoring surface.** The pipeline number was produced by the `frozenset` comparison in
   `eval/harness.py`; this baseline has its own comparison. They were never scored by the same code.
4. **No safety, no validator, no retrieval.** The baseline is not a competing product; it is one
   model call with the whole schema pasted in. On a schema of any real size, "paste the whole schema"
   stops being an option — which is the thing the golden set is too small to show.

## How it may be used

Only as an internal caution, and only with all four confounders attached:

> "A naive no-retrieval, no-validator baseline scored 22 of 22 on the same 22-question golden set on
> 2026-07-10 — but against SQLite rather than PostgreSQL, with a 'return only the SQL' prompt that
> avoids the extra-column mismatches that cost the pipeline three of its four points, and scored by
> different code. The two runs are not comparable."

Do not publish the pipeline number without this one; do not publish this one without its
confounders. The standing recommendation is to publish neither and to re-measure both under
`app/evaluation/`, where a baseline and the pipeline can be scored by the same metric code against
the same generated fixture.
