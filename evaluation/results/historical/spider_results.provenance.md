# Provenance — `spider_results.json`

**Classification: CONDITIONALLY PUBLISHABLE**, with the whole block attached and the scope stated
first. This is the only one of the four archived results that survives the audit
(`docs/v2/CLAIM_AUDIT.md` §4.5), and the reason it does is that `eval/spider_eval.py:1-11` was
admirably explicit about what it was and was not measuring.

## The block

> **Metric produced:** `correct: 101`, `scored: 139`, `exec_accuracy: 0.7266`.
> **Benchmark:** Spider dev split (`benchmark: "Spider dev"`).
> **n:** **148 sampled**, **139 scored**, **101 correct**. The 9-question gap is
> `llm_unavailable_skipped: 9` — transport/throttle failures dropped rather than counted wrong.
> `invalid_gold_skipped: 0`.
> **System under test:** **the generation model alone.** No retrieval, no context pack, no policy
> engine, no execution service, no repair loop. The database schema was pasted into the prompt.
> **Model:** `qwen/qwen3-32b` via Groq, temperature 0.1. Config-truncated or throttled items were
> re-run once at `max_tokens=8192`; genuinely wrong SQL was left as-is.
> **Prompt version:** the inline prompt in `eval/spider_eval.py`. Not `sql_agent_prompt@1.0`, and not
> versioned.
> **Scoring:** execution accuracy by result-set match, **ordered when the gold query has ORDER BY** —
> i.e. the correct Spider rule, and the one this repository's v2 metrics now apply to its own corpus
> (`app/evaluation/metrics.py`).
> **Date:** 2026-07-10 (file mtime 19:53). A first pass is preserved at
> `eval/spider_results_pass1.json` (mtime 17:33).
> **Environment:** the author's workstation, with a `GROQ_API_KEY` present and the Spider dataset
> downloaded locally.
> **Exclusions:** 9 of 148 sampled questions dropped as transport failures. **This moves the figure
> up.** The floor, if all nine were counted wrong, is 101/148 = 68.2%.
> **Limitations:** the four below.

## Reproducible today? Partly — and not from a clean checkout.

* The script and this result file are untracked (`git ls-files eval` → `0`).
* The Spider dataset is deliberately not vendored (`.gitignore` excludes `eval/spider/`), so
  re-running needs a manual download and acceptance of Spider's terms.
* It needs a `GROQ_API_KEY`, so it costs money and depends on a third-party service being up.

`app/evaluation/adapters.py` documents exactly what a person must do by hand to run Spider or BIRD
against this repository. It downloads nothing.

## Limitations that must travel with the number

1. **It is a model measurement, not a product measurement.** It says nothing about DBWhisper's
   retrieval, its policy engine, its execution path or its refusal behaviour, because none of them
   were in the loop.
2. **It is a sample, not the full dev split.** 148 sampled questions out of Spider dev's ~1,034.
3. **The exclusion is favourable.** Dropping 9 transport failures rather than counting them wrong
   raises the figure by up to 4.5 points. Publish the range if the reader deserves it.
4. **The model is a remote hosted one.** The v2 architecture's default is a *local* model
   (`app/llm/registry.py`), so this figure does not describe the shipping default configuration.

## The only approved wording

> "Spider dev sample, generation model only (qwen/qwen3-32b at temperature 0.1, schema in context,
> no retrieval or policy layer): 101 of 139 scored questions matched the gold result set (72.7%);
> 9 of 148 sampled questions were dropped as transport failures rather than scored, so the floor if
> all nine were counted wrong is 101 of 148 (68.2%). Recorded 2026-07-10; the Spider dataset is not
> vendored, so re-running needs the dataset and a provider key."

`docs/v2/CLAIM_AUDIT.md` §4.5 recommends leading instead with §4.1, the policy corpus result, which
a stranger can reproduce with `uv run pytest tests/sqlpolicy` and no credentials at all.
