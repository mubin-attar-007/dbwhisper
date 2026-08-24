# ADR 0010 — Alembic migrations are the schema of record; `create_all` survives only as a fallback

**Status:** Accepted — 2026-08-21, Phase 1.
**Principle:** "Persisted data is migrated, never deleted" — `docs/v2/IMPLEMENTATION_ROADMAP.md`,
guiding rule 2.

## Context

v1 created its schema with `Base.metadata.create_all` at startup, plus a hand-written repair for the
one drift anybody had noticed (`Database_config.owner_id`, added when multi-tenancy landed).
`create_all` never `ALTER`s an existing table, so the moment a column changed the running database
and the models diverged silently. Every v2 phase adds tables or columns to live deployments —
encrypted secrets, jobs, verified-query governance — so the divergence would have compounded.

## Decision

Alembic, with a baseline written to adopt databases that already exist.

* **`0001_baseline`** is idempotent by construction: it creates only the tables that are missing and
  repairs the known drift, so `alembic upgrade head` is safe on a fresh database and on one created
  by the old `create_all` path. Adoption, not a reset.
* **Startup runs it.** `db/migrate.py::upgrade_to_head` is called on boot, keeping the old
  "repair on deploy" ergonomics with a reviewable history behind it. It touches the application
  database only, never a target.
* **One revision per phase's schema change**: `0002_connection_secret` (ADR 0007), `0003_jobs`
  (ADR 0009), `0004_verified_query_governance` (ADR 0014).
* **Verified on both engines.** `tests/test_migrations.py` covers a fresh upgrade, adoption of a
  pre-Alembic database, and the presence of the new column; a separate CI job
  (`postgres-integration`) runs a fresh upgrade and an idempotent re-run against a real PostgreSQL
  service container with pgvector.

## Consequences

**What it buys.** Schema changes are reviewable in a diff, replayable in order, and testable on
SQLite before they touch PostgreSQL. Existing deployments upgrade rather than being asked to
re-enroll their sources.

**What it costs.**

* **`create_all` is still reachable, and that is uncomfortable.**
  `db/database_manager.py::create_metadata_tables` catches a failed Alembic run and falls back to
  `Base.metadata.create_all` plus the `owner_id` repair, logging a warning. The intent is that a
  broken migration must not brick a boot. The cost is that a deployment can end up on an
  *un-versioned* schema that looks healthy, and the next migration will then run against a database
  Alembic has no revision record for. The warning is the only signal. This is a deliberate trade
  that should be revisited once migrations are boring.
* **Startup does schema work.** Two processes booting at once can race on `upgrade_to_head`; the
  migrations are written to tolerate it, but "migrate on boot" is a pattern with known edges and we
  have chosen it for operator convenience.
* **Downgrades are written but not exercised.** The test suite covers upgrade paths and idempotent
  re-runs; there is no automated round-trip test asserting a downgrade restores the previous shape.
  Treat `alembic downgrade` as untested on this branch.
* **The legacy plaintext credential column is still in the schema** because `0002` deliberately kept
  it (ADR 0007). Migrations that preserve data leave debt visible in the schema.
