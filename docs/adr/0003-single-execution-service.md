# ADR 0003 — One execution service is the only path that runs generated SQL

**Status:** Accepted — 2026-08-21, Phase 3. v1 `/query` and `/run_sql` rewired onto it the same
phase; `app/core/query_executor.py` deleted.
**Principle:** "Security over convenience" — `docs/v2/TARGET_ARCHITECTURE.md` §2, §12.

## Context

Every safety property this product claims is a property of the code between "the model emitted text"
and "a connection was opened". In v1 that code existed in more than one place: an executor module,
direct SQLAlchemy use in the request handlers, and f-string pagination that wrapped already-validated
SQL in a new statement *after* validation (`docs/v2/CURRENT_STATE_AUDIT.md` §9.1). Two paths mean two
sets of guarantees, and the weaker one is the one an attacker gets.

## Decision

`app/execution/service.py::execute` is the single entrance. Generated SQL, user-edited SQL, an
investigation sub-query and an evaluation case all arrive as an `ExecutionRequest`. Before a row is
fetched:

1. **Policy is re-evaluated**, even when the caller already holds a `PolicyDecision`. A caller's
   decision is accepted only as an *approval token*: `request.approved_fingerprint` must equal the
   fingerprint of the statement actually being run, otherwise the call is refused. An approved query
   cannot be swapped for a different one after approval.
2. **The target host is checked** against the active network policy
   (`app/platform/network_policy.py::check_target`) — cloud metadata addresses and hostnames refused
   in every mode, non-database ports blocked, every address in the DNS answer inspected.
3. **A read-only session is opened** per dialect (`app/execution/connections.py::read_only_connection`)
   with a statement timeout, and rolled back at the end whatever happened.
4. **At most `max_rows + 1` rows are read**, so truncation is detected rather than guessed.
5. **Errors are translated** before they leave: a driver exception can carry the DSN, the host and
   the schema, none of which belongs in an API response.

Pagination re-executes through the same path with an AST-injected `OFFSET`/`FETCH`
(`app/sqlpolicy/limits.apply_pagination`), never by wrapping a string.

## Consequences

**What it buys.** One place to read, one place to test, one place to change. Rewiring v1 onto it
upgraded both APIs at once: a v1 client that changed nothing got the AST policy engine, the network
check and the read-only transaction (ADR 0015). The re-evaluation in step 1 means a caller's stale, forged or
misremembered decision cannot widen what runs: a caller can only make the outcome stricter by
declining to call.

**What it costs.**

* **Every statement is parsed twice** — once where the decision was made, once here. Deliberate, and
  it is CPU we spend on every query.
* **The read-only session is not equally strong on every engine.** PostgreSQL gets
  `SET TRANSACTION READ ONLY`, MySQL/MariaDB `START TRANSACTION READ ONLY`, SQLite
  `PRAGMA query_only = ON` — but SQL Server has no session-level read-only mode, so there the
  guarantee rests on the policy engine plus a least-privilege login, and
  `ExecutionResult.read_only_enforced` comes back `False` rather than being fudged
  (`app/execution/connections.py`). Public copy has to state the scope per engine instead of
  averaging.
* **Callers lose flexibility.** No caller can pass its own connection, hold a cursor open, or stream
  a large result: everything must fit the `ExecutionRequest`/`ResultFrame` shape. Server-side
  streaming for very large exports is not possible without changing this decision.
* **The property is not fully machine-checked.** `uv run lint-imports` enforces four architectural
  contracts (`pyproject.toml`), and none of them says "only `app.execution` may open a target
  connection". Today the property holds by review plus the fact that
  `grep -rn "from app.execution.service"` finds two callers (`app/graph/nodes.py`, `app/main.py`).
  A contract would be better.
* **Introspection is a deliberate exception.** `app/schema_pipeline/introspector.py` builds its own
  engine and uses SQLAlchemy's `Inspector` plus a small set of *literal* metadata statements
  (`SELECT DB_NAME()`, `SELECT current_database()`, …). No model output and no user text reaches it,
  so it is not in scope for the policy engine — but it means "the only code that connects to a
  target" would be a false statement. The accurate claim is "the only code that runs generated or
  user-supplied SQL", and that is the wording used in the module docstring and in public copy.
* **The network check runs on the execution path only.** Enrollment dials the target first — for the
  read-only privilege probe and for introspection — and does not call `check_target`. An SSRF probe
  therefore succeeds at enrollment time even though it would be refused at query time. This is a
  real gap, not a design choice; it is recorded here so the next person does not read "SSRF is
  handled" and stop looking. (`docs/v2/THREAT_MODEL.md` T-14 documents the related DNS-rebinding
  residual, G-4: the checker returns resolved addresses and the executor does not pin them.)
