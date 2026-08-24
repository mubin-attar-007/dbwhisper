# ADR 0001 — One LangGraph `StateGraph`, not an autonomous multi-agent system

**Status:** Accepted — 2026-08-21, implemented in Phase 6 (query graph) and Phase 7 (investigation).
**Principle:** "No multi-agent theater" — `docs/v2/TARGET_ARCHITECTURE.md` §2.

## Context

v1 answered questions with a LangChain tool-calling agent: `app/agent/chain.py::create_sql_agent`
builds a `create_agent(...)` loop over the tools in `app/agent/tools.py`, and
`app/main.py::execute_query` wraps it in a for-loop over whichever providers have keys. The loop
decides its own next step. That has three consequences we could not live with:

* **You cannot say in advance which steps ran.** The trace is a message list, not a sequence of
  named stages, so "was the schema consulted before the SQL was written?" is answered by reading a
  transcript rather than by reading a record.
* **There is nowhere to pause.** `docs/v2/CURRENT_STATE_AUDIT.md` §6.5 recorded the result of
  `grep -rn "interrupt|HumanInTheLoop|human_in_the_loop" app db tests`: the only hit in the whole
  repository was the word "interrupted" inside a timeout-error regex. The policy engine could
  already return `NEEDS_APPROVAL` and the execution service already refused it — but no code could
  ask a human and then continue.
* **The step count is unbounded by design.** A tool loop retries until the model stops calling
  tools. Against somebody else's database, "until the model is satisfied" is not a budget.

The obvious alternative — several cooperating agents (a planner, a SQL writer, a critic) — makes all
three problems worse and adds a fourth: each agent needs its own safety story, and the one nobody
audits becomes the way in.

## Decision

One `StateGraph` per pipeline, with named nodes and explicit conditional edges.

* `app/graph/query_graph.py::build_query_graph` registers ten nodes — `retrieve`, `understand`,
  `clarify`, `generate`, `validate`, `approve`, `execute`, `verify`, `summarize`, `finalize` — and
  routes between them with six pure predicate functions (`_after_retrieve`, `_after_understand`,
  `_after_generate`, `_after_validate`, `_after_approve`, `_after_execute`). The whole control flow
  fits on one screen and has a `Literal` return type naming every reachable successor.
* State is a typed `TypedDict` (`app/graph/state.py::QueryState`); every node returns a partial
  update and appends one `RunStep`, so the trace is a by-product of running rather than something
  instrumentation has to reconstruct.
* Human-in-the-loop is a real LangGraph `interrupt()` at two defined points (clarification and
  approval) over a durable checkpointer, so a paused run survives a process restart.
* Repair is a counter, not a loop: `GraphDeps.max_repairs` (default 2) and `_is_repairable(...)`
  in `app/graph/nodes.py` decide whether a failed `validate` or `execute` may re-enter `generate`.
  Policy denials are not in the repairable set.
* Investigation is a **subgraph**, not a second pipeline:
  `app/graph/investigation.py::compile_investigation_graph` plans 2–5 sub-questions and invokes the
  *same* query graph once per sub-question, so it inherits the same policy engine, the same
  execution service and the same approval mechanics rather than reimplementing them.

## Consequences

**What it buys.** The set of reachable states is finite and reviewable. An interrupt has a defined
place to happen, which is what made approvals bound to a SQL fingerprint possible at all (ADR 0006).
Investigation cannot drift from the safety properties of Quick Query, because it does not have its
own path to a database. `tests/graph` (68 tests) can enumerate the paths: every interrupt, every
resume, the repair budget being exhausted, and durability across two graph instances.

**What it costs.**

* **New behaviour means editing the graph.** The model cannot improvise a step we did not write.
  That is the point, and it is also the limitation: a question whose shape we did not anticipate
  gets a blocked run rather than a creative recovery.
* **More code than a prompt.** Ten node factories, six predicates and a typed state are considerably
  more surface than "give the agent a tool and let it work it out".
* **No backtracking.** A failure outside the repairable categories ends the run. We accept a lower
  ceiling on rescued queries in exchange for a bounded number of model calls per question.
* **Two pipelines exist today.** v1 `/query` still runs the old agent for SQL generation — see
  ADR 0015. The claim "there is no free-running loop" is true of `/v2/*` and of every path that
  reaches a target database, and it is not yet true of v1 SQL generation.

**Enforcement.** The import contract `No credential reaches the graph` in `pyproject.toml`
(`[tool.importlinter]`, run in CI as `uv run lint-imports`) keeps the graph from acquiring its own
route to a database, which is what would let a node quietly become an agent again.
