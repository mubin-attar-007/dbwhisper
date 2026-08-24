# ADR 0006 — Checkpointed state carries identifiers; credentials resolve through `GraphDeps`

**Status:** Accepted — 2026-08-21, Phase 6.
**Related:** ADR 0001 (the graph), ADR 0007 (secrets at rest).

## Context

A pausable run needs a durable checkpointer: LangGraph writes the entire state to Postgres or SQLite
after every node, and `POST /v2/runs/{id}/resume` reads it back — possibly in a different process,
after a restart. Whatever is in that state is therefore *at rest*, in a table that operators back up,
that the trace viewer renders, and that support engineers read while debugging.

The natural way to write the graph puts the connection string in the state, because the execute node
needs it. That would make every checkpoint row a credential store and the trace viewer a credential
viewer.

## Decision

**State holds `source_id`. Deps hold the resolver.**

* `app/graph/state.py::QueryState` carries `source_id`, `snapshot_id`, `dialect`, `tenant_id` — no
  DSN, no password, no engine. The module docstring states this as the first of two constraints on
  the type.
* `app/graph/deps.py::GraphDeps` holds `resolve_source: SourceResolver`, a callable bound when the
  graph is compiled, returning a `DataSourceTarget` with the connection string. Deps are passed to
  the node factories, never serialised into state.
* The credential is decrypted at the moment it is needed (`app/api/v2/deps.py` resolves the row and
  decrypts through `app/platform/connection_secrets.py`), used by the execution service, and
  discarded.
* A resumed run therefore re-resolves its target rather than replaying a stored one.

**The property is tested both ways.**

* Runtime: `tests/graph/test_query_graph.py::TestResilience::test_no_credentials_are_written_into_the_checkpointed_state`
  runs a full question against a real `SqliteSaver`, serialises both the result and
  `graph.get_state(config).values`, and asserts the target's connection string and the substring
  `sqlite:///` appear in neither.
* Structural: the import contract `No credential reaches the graph` (`pyproject.toml`, run in CI as
  `uv run lint-imports`) forbids `app.graph`, `app.analysis` and `app.retrieval` from importing
  `db.database_manager` or `app.platform.connection_secrets` at all.

## Consequences

**What it buys.** Checkpoint tables can be dumped, inspected and shipped to a debugging session
without handling secrets. A rotated credential takes effect on the next resume, because the run
never captured the old one. It is also why a trace can be rendered without a credential having to be
redacted out of it first.

**What it costs.**

* **A checkpoint is not self-contained.** Resuming requires a process that can build the same deps.
  You cannot hand a checkpoint to a bare worker and expect it to finish.
* **Resolution happens per execution**, including one secret decryption, rather than once per run.
* **The runtime test is a string search.** It catches the DSN it was given and the `sqlite:///`
  scheme; it would not catch a novel encoding of a credential, or a credential embedded in an error
  message from a driver we have not seen. The import contract is the stronger half of the guarantee.
* **"No secrets" is not "no sensitive data".** Result `rows` *are* persisted in the checkpoint —
  `docs/v2/THREAT_MODEL.md` gap **G-4** records this as open. The state docstring's claim is about
  credentials specifically, and this ADR does not extend it.
* **Deviation from the target architecture, recorded honestly.** `TARGET_ARCHITECTURE.md` §4 planned
  an `app/runs/` package with `QueryRun`/`RunStep` tables as the product trace. That package does not
  exist; `GET /v2/runs/{id}` reads the LangGraph checkpoint directly
  (`app/api/v2/router.py::_load_run`). Consequences: run history is bounded by checkpoint retention,
  and there is no relational way to ask "all runs for this tenant last week". Reconsider when run
  analytics are needed.
