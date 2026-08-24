# ADR 0015 — Keep the v1 endpoints working, and re-implement them on the v2 services

**Status:** Accepted — 2026-08-21 (rule), implemented across Phases 3 and 6.
**Principle:** "Backwards compatible" — `docs/v2/TARGET_ARCHITECTURE.md` §2.

## Context

v2 changes almost everything behind `/query`: a different validator, a different execution path, a
different model layer, and a pipeline that can *pause*. The v1 response contract cannot express a
paused run, an approval bound to a fingerprint, or a step-by-step trace. Two ways out: version the
API and break existing clients, or keep the old surface and re-implement it.

Breaking it was tempting — the duplication described below is the price of not doing so — but the
v1 routes are what the deployed demo, the frontend and any external client call. A rewrite that
begins by breaking every caller does not get finished.

## Decision

**v1 paths keep their contracts** — `/query`, `/run_sql`, `/databases`, `/training/pairs`,
`/schemas/*`, `/auth/*` — and are re-implemented on the v2 services where the contract allows.

**The security-relevant half was rewired first.** Both `/query` and `/run_sql` execute through
`app/main.py::_run_read_only` → `app/execution/service.execute`, so a v1 client that changed nothing
got the SQLGlot policy engine, the network check, the read-only session, the row cap and AST
pagination (ADR 0002, ADR 0003). `app/core/sql_validator.validate_sql` kept its
`{"valid": bool, "reason": str}` shape and became a thin wrapper over `app/sqlpolicy`, so every
remaining v1 caller was upgraded without touching its code. The v1 responses gained metadata
(policy version, decision, fingerprint, tables, truncation) as additive fields.

**New capability lives under `/v2/*`.** `POST /v2/query`, `GET /v2/runs/{id}`,
`POST /v2/runs/{id}/resume`, `POST /v2/investigate`, `GET /v2/models/health` — the things v1 cannot
express.

## Consequences

**What it buys.** No client breakage, and the upgrade landed for both APIs at once: the dangerous
path is shared, so v1 users got the hardening whether or not they migrate. New surface is free to be
shaped by what v2 needs rather than by what v1 promised.

**What it costs — and this is the largest carried cost on the branch.**

* **Two SQL generation paths.** v1 `/query` still runs the LangChain tool-calling agent
  (`app/agent/chain.py`, `app/agent/tools.py`) with the old per-provider fallback loop. Only the
  execution half was rewired. Everything ADR 0001 says about bounded steps and named stages applies
  to `/v2/*`, not to v1 generation.
* **Two model abstractions.** `app/agent/chain.py::get_available_providers` (priority list of
  environment variables, "always add Gemini as a fallback") coexists with `app/llm/router.py`
  (capability filter, egress filter, circuit breaker, recorded decision).
* **Two retrieval implementations.** PGVector via `langchain-postgres` (`app/core/retriever.py`) for
  v1, `app/retrieval` for v2 (ADR 0011).
* **The vendor SDKs stay in the default install**, because `chain.py` imports five LangChain
  provider packages at module scope. "No paid service is required" remains true; "a local install
  pulls no cloud client" is not (ADR 0004).
* **Every security fix must be applied to, or shown to be shared by, both paths.** That is a
  standing tax on every future change and the reason this duplication should not be permanent.

**Exit condition.** This ADR is a transition, not a destination. It should be revisited when the
frontend and the demo call `/v2/*` exclusively: at that point `app/agent/`, the provider fallback
loop and the v1 retriever can go, and with them five direct dependencies.
