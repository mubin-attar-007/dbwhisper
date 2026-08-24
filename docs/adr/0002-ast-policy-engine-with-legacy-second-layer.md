# ADR 0002 — A SQLGlot AST policy engine, with the v1 regex validator kept as a second layer

**Status:** Accepted — 2026-08-21, Phase 2. Amended 2026-08-24 (the duplicate read-only checker was
deleted rather than subordinated; see below).
**Principle:** "Security over convenience" — `docs/v2/TARGET_ARCHITECTURE.md` §2, §11.

## Context

v1 decided whether generated SQL was safe with 182 lines of `sqlparse` plus regular expressions
(`docs/v2/CURRENT_STATE_AUDIT.md` §8.1 reconstructs it line by line from `git show HEAD:...`). Regex
over SQL fails in both directions, and the audit found examples of each:

* **False positives** that refused legitimate queries: substring matches on forbidden words inside
  identifiers and literals (`grant_total`, `'update'`), `REPLACE(...)` the string function read as
  MySQL `REPLACE INTO`, a leading `/* hint */` comment read as a non-SELECT statement,
  `EXTRACT(YEAR FROM col)` read as a table reference, and quoted or bracketed identifiers.
* **Structural blindness.** Table extraction was `\b(?:from|join)\s+([\w\[\]".]+)`, which cannot
  distinguish a CTE name from a real table, cannot follow an alias, and cannot see into a derived
  table. Pagination was worse: `f"SELECT * FROM ({sanitized_sql}) AS _sub LIMIT {page_size}"`.

The replacement had to parse. The open question was what to do with the old validator.

## Decision

**1. The engine parses.** `app/sqlpolicy/engine.py::PolicyEngine.evaluate(sql, ctx)` returns a typed
`PolicyDecision` after eight stages, each recording a `RuleResult`: length and invisible-character
checks, single-statement parse in the declared dialect (`app/sqlpolicy/parse.py`), root statement
class, denied node types anywhere in the tree, blocked functions (file, network, sleep, lock,
sequence, config introspection), scope-aware object resolution against the enrolled schema
(`app/sqlpolicy/allowlist.py`), complexity limits for the policy level, AST row-limit injection
(`app/sqlpolicy/limits.py`), optional parameterisation, and finally a fingerprint over the
normalised tree. Pagination is an AST rewrite (`limits.apply_pagination`), not string concatenation.

**2. The old validator is retained as an independent second opinion.**
`app/sqlpolicy/legacy_heuristics.py::legacy_validate` runs as stage 7, gated by
`PolicyContext.run_legacy_layer` (default `True`, `app/sqlpolicy/types.py`). Agreement is recorded
on `PolicyDecision.legacy_agrees`; disagreement denies, so **the stricter answer wins**. Several of
the false positives listed above were fixed first, so the heuristic layer cannot poison the engine
with the failures it was retired for.

**3. The duplicate read-only checker was deleted, not subordinated.** On 2026-08-24
`app/security/db_readonly_checker.py` (162 lines) was removed in favour of
`app/execution/readonly.py`.

Those two calls look contradictory. They are not, and the distinction is the reason this ADR exists:

| | `legacy_heuristics` | `db_readonly_checker` |
|---|---|---|
| Reachable how? | Only from inside `PolicyEngine.evaluate`, after the AST engine has already decided | Directly, by any caller — enrollment imported it |
| Can it change an outcome? | Only towards **deny** | It *was* the outcome; it returned a boolean |
| Coverage when audited | Exercised by the whole 255-case corpus | 8% (`CURRENT_STATE_AUDIT.md` §14.3) |

A second opinion that can only add denials is defence in depth. A second *implementation* that a
caller can choose between is a bug waiting for the wrong import — and it had already happened: the
weaker boolean checker was wired to `/schemas/enroll` while the stronger tri-state one
(`ReadOnlyStatus`, five states, never attempts a write) sat unused. Keeping a weaker duplicate of a
security control is how the wrong one gets called.

## Consequences

**What it buys.** Decisions are structural and per-dialect. Every decision carries a version
(`sql_policy@2.0.0`), a fingerprint and the rules that fired, which is what lets an approval be
bound to a statement (ADR 0006) and lets the execution service re-check a decision it was handed
(ADR 0003). `tests/sqlpolicy` holds 616 tests, including a 255-case adversarial corpus
(`app/evaluation/datasets/adversarial/sql_policy_cases.yaml`) expanded across dialects and
Hypothesis property tests.

**What it costs.**

* **Two rule sets to maintain.** A change to what is forbidden has to be made in the engine and
  considered for the heuristic layer, or the layers will disagree for uninteresting reasons.
* **A legacy false positive is now a refusal.** Because the stricter answer wins, any remaining
  weakness in the old regexes costs a user a legitimate query. That is the direction we chose to
  fail in, and it is a real cost.
* **A second parse per statement.** An extra `sqlparse` pass on every statement the engine admits.
  Not measured, and small next to a database round-trip — but not free, which is why the layer is
  switchable per context (`PolicyContext.run_legacy_layer`).
* **The engine is bounded by SQLGlot's fidelity** for the dialect it was given. The module docstring
  says so explicitly: this is a structural filter, not a proof. It is one layer alongside a
  least-privilege role, a read-only transaction, a timeout and a row cap.
* **Deleting the duplicate checker changed enrollment behaviour.** Enrollment now fails closed when
  the privilege probe raises, where before it enrolled the source as safe. Some connections that
  were accepted are now refused; `SECURITY.md` had to be corrected because it still described the
  deleted probe as *warning*.

**Enforcement.** The import contract `The policy engine decides on its own` forbids `app.sqlpolicy`
from importing `app.llm`, `app.graph`, `app.execution`, `app.api`, `app.agent` or `db`. The verdict
is therefore a pure function of the statement and the schema scope — which is also what makes the
adversarial corpus mean anything.
