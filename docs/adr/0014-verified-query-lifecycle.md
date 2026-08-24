# ADR 0014 — Verified query pairs carry a lifecycle and are retired on schema drift

**Status:** Accepted — 2026-08-24, Phase 8. Migration `0004_verified_query_governance`.

## Context

A verified pair is a reviewer saying "this SQL correctly answers this question". The qualifier that
was missing is **"for this schema"**. A pair approved in June against a table that changed in August
was still being handed to the model in September as a verified example, and there was no way for
anyone to notice: `mark_stale_for_tables` existed with no caller, and pairs had no status at all.
An approval that cannot expire is worse than no approval, because it carries a reviewer's authority
into a situation the reviewer never saw.

## Decision

**A pair has a status.** `db/verified_queries.py::VerifiedStatus`:
`draft → approved → (stale | superseded | needs_review | rejected)`. `usable_examples()` returns
`approved` only — that is the set the model may see. The row also records the snapshot it was
approved against, a literal-independent SQL fingerprint, the tables it touches, the reviewer, usage,
and prompt/model provenance.

**Drift retires the affected pairs.** `app/schema_pipeline/drift.py` fingerprints each table over
the attributes that can change what a query *means* — `SIGNIFICANT_COLUMN_KEYS = ("name", "type",
"is_nullable")` — and diffs the extracted artefacts before and after an extraction. Enrollment gained
`snapshot_schema` and `detect_drift` stages either side of extraction (ADR 0009), so re-enrolling a
source retires the pairs the change invalidates.

**Retirement is real, not cosmetic.** `mark_stale_for_tables` sets the status *and deletes the
pair's embedding*, so a stale pair stops being retrievable rather than merely being relabelled. The
reverse operation is symmetric: `set_status` re-embeds on reinstatement, because relabelling in one
direction only left re-approved pairs approved-but-unembedded and therefore permanently invisible.

**A human can put a pair back.** `POST /training/pairs/{id}/review` moves a drift-retired pair back
into circulation, with the staleness reason visible so the reviewer knows what changed.

**Review effort becomes test coverage.** `export_as_eval_cases()` turns approved pairs into
evaluation cases (ADR 0016).

## Consequences

**What it buys.** An approval means something specific and can expire. Retirement is visible
(`staleness_reason`) rather than silent. Human review time compounds into regression coverage
instead of evaporating. `tests/test_verified_queries.py` and `tests/test_schema_drift.py` cover
drift-to-stale, approved-only export and reinstatement.

**What it costs.**

* **Drift is detected by diffing artefacts, not by watching the database.** The comparison is over
  the YAML files enrollment writes, so a schema that changes between enrolments is invisible until
  the next enrolment runs. There is no trigger, no logical-replication listener, and no schedule
  beyond whatever the operator runs. A stale pair can therefore be served for as long as nobody
  re-enrolls.
* **The fingerprint deliberately ignores things that can still matter.** A description edit or a
  column reordering is not drift, by design — but neither is a change of meaning that leaves name,
  type and nullability alone (a status code whose values were redefined, a currency change). Those
  pass silently.
* **Retirement is coarse and errs towards false positives.** It is per *table*: a pair that touches
  a table which gained an unrelated column is retired too, and a pair whose tables were never
  recorded is retired conservatively. Each false positive costs a human review cycle. We chose that
  over serving a wrong example.
* **A first enrolment has no baseline**, so it retires nothing — correct, and worth stating so that
  "drift detection ran and found nothing" is not misread as a clean bill of health.
* **The snapshot a pair records is only as good as `snapshot_id` currently is** — a schema file's
  mtime until the catalog tables land (ADR 0011).
