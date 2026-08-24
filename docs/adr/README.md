# Architecture decision records

One file per decision that was expensive to make and would be expensive to reverse. Each follows the
Nygard shape — Title, Status, Context, Decision, Consequences — and each Consequences section states
what the decision **costs**, not only what it buys. An ADR with no downside listed is marketing.

These records describe the code on `feat/dbwhisper-v2` as it stands, with file and symbol citations
(never line numbers — lines move). Where the code does not yet do what the design document says, the
ADR says so rather than describing the plan: see 0011 (no PostgreSQL retrieval index), 0006 (no
`app/runs` tables), 0015 (two generation paths), 0008 (an unwired PII classifier), 0010
(`create_all` still reachable as a fallback), 0003 (the network check does not run at enrollment).

The design of record is [`docs/v2/TARGET_ARCHITECTURE.md`](../v2/TARGET_ARCHITECTURE.md); §2 is the
list of principles these decisions implement. What each phase actually landed, with evidence, is the
implementation log at the bottom of
[`docs/v2/IMPLEMENTATION_ROADMAP.md`](../v2/IMPLEMENTATION_ROADMAP.md). What may be said in public
about any of it is governed by [`docs/v2/CLAIM_AUDIT.md`](../v2/CLAIM_AUDIT.md).

| # | Title | Status | In one line |
|---|---|---|---|
| [0001](0001-one-langgraph-stategraph.md) | One LangGraph `StateGraph`, not an autonomous multi-agent system | Accepted | Ten named nodes and explicit edges, so the reachable states are finite, an interrupt has somewhere to happen and repairs have a budget. |
| [0002](0002-ast-policy-engine-with-legacy-second-layer.md) | A SQLGlot AST policy engine, with the v1 regex validator kept as a second layer | Accepted (amended) | Parse instead of regex; keep the old validator as a second opinion that can only add denials, and delete the duplicate read-only checker that a caller could choose. |
| [0003](0003-single-execution-service.md) | One execution service is the only path that runs generated SQL | Accepted | Policy re-check, fingerprint-bound approval, network check, read-only session, row cap — one entrance, with the introspection carve-out named. |
| [0004](0004-local-first-model-layer.md) | Local-first model layer: Ollama over plain `httpx`, plus a deterministic fake provider | Accepted | No vendor SDK on the default path and a fixture-backed provider that is a real provider, so the suite runs offline. |
| [0005](0005-typed-application-modes.md) | Three application modes resolving to one immutable policy object | Accepted | `demo`/`self_hosted`/`production` become one frozen `AppModePolicy` the code consults, instead of `APP_ENV` checks scattered across modules. |
| [0006](0006-no-credentials-in-checkpointed-state.md) | Checkpointed state carries identifiers; credentials resolve through `GraphDeps` | Accepted | State holds `source_id`; a test and an import contract keep connection strings out of the checkpoint. |
| [0007](0007-encrypted-connection-secrets.md) | Target credentials encrypted at rest, with the key outside the database | Accepted (breaking) | MultiFernet with rotation, `enc:v1:` ciphertext, and a refusal to store a secret when the mode requires encryption and no key is set. |
| [0008](0008-ranked-egress-policy.md) | Egress is a rank-ordered enum, enforced where data would leave | Accepted | Five ranked levels from `LOCAL_ONLY` upward, enforced as a routing filter and a summary row budget — at exactly two points, which is the weakness. |
| [0009](0009-database-backed-job-queue.md) | A job queue in the application database, not Redis or a broker | Accepted | `SKIP LOCKED` on PostgreSQL, a guarded update on SQLite, a lease for crash recovery, resumable stages — at the cost of polling and at-least-once. |
| [0010](0010-alembic-as-schema-source-of-truth.md) | Alembic migrations are the schema of record; `create_all` survives only as a fallback | Accepted | An idempotent baseline adopts existing databases; the surviving `create_all` fallback is named as debt. |
| [0011](0011-hybrid-retrieval-with-rrf.md) | Hybrid retrieval fused by Reciprocal Rank Fusion, over an in-process index | Accepted (partial) | Fuse ranks not scores; BM25 + cosine in-process so CI needs no PostgreSQL — and the PostgreSQL index still does not exist. |
| [0012](0012-deterministic-analysis-over-model-calls.md) | Deterministic code wherever a model is not strictly needed | Accepted | Statistics, chart choice, shape verification and the summary fallback are ordinary code, which is what makes an evaluation repeatable. |
| [0013](0013-evidence-checklist-not-confidence-score.md) | An evidence checklist, not a confidence score | Accepted | Seven three-valued facts about what happened, each traceable to a step — and a fully green checklist still does not mean the answer is right. |
| [0014](0014-verified-query-lifecycle.md) | Verified query pairs carry a lifecycle and are retired on schema drift | Accepted | Only `approved` pairs reach the model; drift moves the affected ones to `stale` and deletes their embedding, visibly and reversibly. |
| [0015](0015-keep-v1-endpoints-alongside-v2.md) | Keep the v1 endpoints working, and re-implement them on the v2 services | Accepted (transitional) | v1 contracts stay; the dangerous half was rewired onto the v2 execution path — leaving two generation paths, two model layers and two retrievers to carry. |
| [0016](0016-evaluation-through-the-real-pipeline.md) | Evaluation runs the real pipeline, and every number carries provenance | Accepted | No evaluation-only code path, a scripted oracle that declares `measures_model=False`, and a provenance block that refuses to be incomplete. |
| [0017](0017-observability-off-by-default.md) | Observability is off by default, and a closed allowlist decides what a signal may carry | Accepted | Tracing cannot take the app down, an attribute is dropped unless allowlisted, and a metric label may never come from user input. |
| [0018](0018-guarded-source-identifier-paths.md) | One guarded builder turns a `db_flag` into a path, and rejects rather than sanitises | Accepted | An identifier allowlist plus a containment check in one function, with a test that fails if a seventh module builds the path by hand. |

## Enforcement

Four of these decisions are structural rather than conventional, and CI checks them as import
contracts (`[tool.importlinter]` in `pyproject.toml`, run as `uv run lint-imports`): the model layer
cannot reach a database (0004), the policy engine cannot consult a model or a connection (0002), no
credential-bearing module is importable from the graph (0006), and observability is a leaf (0017).
A docstring saying "the model layer never touches a database" is worth very little; an import graph
that makes it impossible is worth a lot.

## Adding one

Next number, kebab-case filename, same five headings. Status is `Proposed`, `Accepted`,
`Superseded by NNNN` or `Deprecated`; do not edit an accepted ADR to make it look like it was always
right — amend the Status line and record what the evidence changed, as 0002 does. If you cannot
write a Consequences section that costs something, the decision was probably not consequential
enough to record.
