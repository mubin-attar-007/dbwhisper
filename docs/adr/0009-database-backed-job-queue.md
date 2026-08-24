# ADR 0009 — A job queue in the application database, not Redis or a broker

**Status:** Accepted — 2026-08-24, Phase 10.
**Principle:** "No required purchase" — `docs/v2/TARGET_ARCHITECTURE.md` §2.

## Context

Enrollment is not a request. Extracting a schema, documenting it, embedding it and detecting drift
takes minutes, must survive a restart, and must not repeat work it already finished. In v1 it ran
inline in the HTTP handler, so a dropped connection lost the run and a re-enroll repeated every
stage — including per-table model calls, because `_mark_schema_extracted` sat inside the wrong
branch and the flag was never set on the normal path.

The obvious answer is Celery or RQ with Redis. The obstacle is the first guiding rule of the
programme: nothing may be *required* beyond the app and its database. A broker in the default
`compose --profile core` makes "runs with nothing paid and nothing extra" false.

## Decision

A table, a lease, and a polling worker.

* **Claiming.** `app/jobs/queue.py::claim` uses `SELECT ... FOR UPDATE SKIP LOCKED` on
  PostgreSQL, so several workers compete without blocking each other or being handed the same job
  twice. On SQLite — single-writer by nature — it is a guarded conditional update, which is
  equivalent at the concurrency SQLite supports.
* **A lease, not a lock.** `DEFAULT_LEASE_SECONDS = 900`. A worker that dies leaves `locked_at` in
  the past and the job becomes claimable again when the lease expires; a long stage renews with
  `queue.heartbeat`. Crash recovery needs no operator.
* **Resumable named stages.** `app/jobs/models.py::JobStage` records status, timing, attempt and
  error per stage; a resumed job skips stages already marked complete. Enrollment is
  `app/jobs/handlers.py::ENROLL_HANDLER`: `verify_read_only`, `snapshot_schema`, `extract_schema`,
  `detect_drift`, `document_schema`, `build_index`, `mark_enrolled` — the last three conditional on
  the payload. `snapshot_schema` and `detect_drift` sit either side of extraction (ADR 0014).
* **A boring worker.** `python -m app.jobs.worker` claims, runs, records, and sleeps
  `worker_poll_interval_seconds` (default 2.0) when there is nothing to do, with signal handling
  that finishes the current job before exiting.

Redis and a real broker remain perfectly good deployments; they are simply not required.

## Consequences

**What it buys.** `docker compose --profile core up` needs no broker. Job state and application
state commit in the same transaction, so there is no dual-write to go wrong — a job cannot be
enqueued for a row that was rolled back. `tests/test_jobs.py` (24 tests) covers lease recovery and
stage resume, which are the two behaviours that would otherwise be discovered in production.

**What it costs.**

* **Polling latency.** A job waits up to the poll interval before it starts. At two seconds this is
  invisible for enrollment and unacceptable for anything interactive — this queue is for work
  measured in minutes, not milliseconds.
* **Throughput is bounded by the application database**, and every poll is a query. Fine at jobs per
  hour; wrong at thousands per second. If that day comes, the handler interface is the seam to keep
  and this module is the part to replace.
* **At-least-once, so every handler must be idempotent.** A lease can expire mid-stage and the stage
  will run again. That burden is real and falls on every future handler author; the stage table makes
  it tractable but does not remove it.
* **A long stage that forgets to renew loses its job** to another worker, and both may then be
  running it.
* **SQLite concurrency is nominal.** The guarded update is correct, but a single writer means the
  SQLite path is for tests and single-operator deployments, not for parallel workers.
