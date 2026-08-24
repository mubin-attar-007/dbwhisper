# DBWhisper — Current State Audit

**Anchor commit:** `e5c89f3` · **Branch:** `feat/dbwhisper-v2` · **Audit window:** 2026-08-24, 14:36–15:18 IST
**Environment:** Windows 10 Pro 19045 · Python 3.13.13 · Node v24.14.1 · uv 0.11.6

---

## 1. Purpose and how to read this document

This document records what DBWhisper *is* on the `feat/dbwhisper-v2` branch, not what it is intended to become. It exists because three separate places already link to it as a Phase-0 deliverable — `docs/v2/TARGET_ARCHITECTURE.md:4`, `docs/v2/IMPLEMENTATION_ROADMAP.md:23` and `:169`, and a source comment at `app/agent/prompt.py:7` — and none of those references could resolve. It is the baseline against which the v2 program's claims can be checked.

### How to read it

Every non-obvious claim carries a `path:line` citation. Claims are labelled by how they were established:

| Label | Meaning |
|---|---|
| **RUN** | Established by executing a command during the audit window. The command is quoted. |
| **READ** | Established by reading source. Verifiable by opening the cited line. |
| **not verified** | Stated because it matters, together with the reason it could not be confirmed. Treat as a hypothesis. |

Two conventions apply throughout:

- **"Before v2" vs "today."** Pre-v2 source is quoted via `git show HEAD:<path>` and cited as `old:<line>`, because `HEAD` (`e5c89f3`) predates the v2 work, which sits uncommitted in the working tree. Current source is cited as a bare `path:line`.
- **No invented numbers.** Where a metric cannot be reproduced from the repository as it stands, that is said explicitly rather than quoted from memory. Section 13 is largely an application of this rule.

### A material caveat about this snapshot

**The working tree was being edited by another process throughout the audit** (RUN, repeated `ls -la --time-style=full-iso` and `git status`). Directories appeared mid-audit: `app/llm/` at 14:42–14:45, `app/embeddings/` at 14:48–14:50, `app/retrieval/` at 15:01, `app/analysis/` at 15:08, `app/graph/` at 15:12, `tests/graph/` at 15:14. Python line count excluding the vendored Spider corpus grew from 15,621 (14:04) to 23,173 (RUN, 15:15).

Consequences a reviewer must hold in mind:

1. **Line numbers are timestamped snapshots.** All `app/main.py` citations were re-verified at 15:14 against the file as it stands (1,206 lines, mtime 14:37:41) and are current. `app/core/embeddings.py` (114 lines, mtime 14:50:42) and `app/core/result_formatter.py` (143 lines, mtime 14:37:41) were likewise re-read immediately before publication.
2. **One observation is explicitly withdrawn.** At 14:36 `app/main.py:800` called a `result_formatter.format_results` symbol that no longer exists. By 14:37:41 the file was fully rewired and the call was gone. That was a transient mid-edit state, **not a defect**, and it is not reported as one.
3. **Test and type-check failures at the end of the window belong to in-flight work**, not to the audited surface. Section 2.3 separates the two.

---

## 2. Snapshot of the baseline

### 2.1 Pre-v2 baseline — 2026-08-21

Quoted from `docs/v2/IMPLEMENTATION_ROADMAP.md:18-22`. **Not verified by me:** reproducing it would require checking out the pre-v2 tree, which the audit's terms of reference forbid.

| Gate | Result (2026-08-21) |
|---|---|
| `ruff check` | PASS |
| `ruff format --check` | **FAIL** — 1 file (`app/main.py`) needed reformatting |
| `mypy app db` | **82 errors** (informational in CI) |
| `pytest` | **93 passed / 93**, coverage **41 %** |
| web lint / typecheck / build | PASS / PASS / PASS |
| `docker build` | not run — daemon reported unavailable |
| eval smoke | **none exists** (`IMPLEMENTATION_ROADMAP.md:21`) |
| e2e | none |
| security scans | gitleaks only |

### 2.2 Today — 2026-08-24, all RUN by me

| Gate | Command | Result | Time |
|---|---|---|---|
| ruff lint | `uv run ruff check app db tests run.py` | **All checks passed!** | 15:15:59 |
| ruff format | `uv run ruff format --check app db tests run.py` | **141 files already formatted** (0 would reformat) | 15:15:59 |
| mypy | `uv run mypy app db` | **107 errors in 33 files** (101 source files checked) | 15:16:18 |
| pytest | `uv run pytest --cov=app --cov=db` | **5 failed, 1017 passed**, 37 warnings, 74.98 s | 15:17:45 |
| coverage | same run | **TOTAL 7747 stmts, 2458 missed, 68 %** | 15:17:45 |
| web typecheck | `npm run typecheck` (`tsc --noEmit`) | PASS, exit 0 | 14:58:57 |
| web lint | `npm run lint` | **No ESLint warnings or errors**, exit 0 | 14:59:10 |
| web build | not re-run | established PASS 2026-08-21 | — |
| docker daemon | `docker info --format ServerVersion` | **29.3.1 — daemon IS running** | 14:49 |
| docker build | `docker build --check .` | could not resolve `ghcr.io/astral-sh/uv:0.11` (registry auth denied) | 14:51 |

**Correction to the 2026-08-21 record:** the Docker daemon *is* running on this machine (RUN, exit 0, server version 29.3.1). The earlier note that it was unavailable is wrong as of today. That said, **no image build has been verified.** `docker build --check .` — used as a cheap lint rather than a multi-minute apt + `msodbcsql18` + `uv sync` build — failed at metadata resolution with `ERROR: ghcr.io/astral-sh/uv:0.11: failed to fetch oauth token: denied: denied` (`Dockerfile:7`). **Not verified** whether that is a registry-auth/egress condition in this environment or a Dockerfile defect; the former is far likelier, but it cannot be proven from here.

### 2.3 Reading the deltas honestly

The coverage rise (41 % → 68 %) and the test-count rise (93 → 1022 collected) are real, and are attributable to the v2 packages, which are the best-covered code in the repository (§14.3). The mypy rise (82 → 107) and the 5 failures are **not** regressions in the audited surface:

- **All 5 failures are in `tests/graph/test_query_graph.py`** (RUN, 15:17:45) — a directory created at **15:14:23**, seventy seconds before the run. Named: `TestRefusals::test_a_model_that_writes_a_delete_is_blocked`, `TestApproval::test_approving_runs_the_query`, `TestApproval::test_edited_sql_is_revalidated_not_trusted`, `TestResilience::test_understanding_degrades_without_a_model`, `TestResilience::test_no_credentials_are_written_into_the_checkpointed_state`.
- **19 of the 107 mypy errors are in files created during the audit** (RUN, per-file distribution): `app/graph/nodes.py` 10, `app/graph/query_graph.py` 5, `app/llm/providers/remote.py` 4. The pre-existing distribution is unchanged at the top: `app/schema_pipeline/introspector.py` 17, `app/main.py` 16, `db/model.py` 12, `app/agent/chain.py` 8, `app/sqlpolicy/types.py` 6, `app/sqlpolicy/allowlist.py` 6, `app/execution/readonly.py` 6.
- An intermediate run at **14:44:34**, before `tests/graph/` and `tests/llm/` existed, was **785 passed / 0 failed** at 60 % coverage. That is the cleanest reading of the audited surface.

**Dominant mypy error classes** (READ, sampled): SQLAlchemy `Column[T]` vs `T` assignment (`db/model.py`; `app/main.py:1151-1152`), and `"object" has no attribute …` on the orchestrator's untyped `outcome` fields (`app/main.py:1047-1049`, `:1075-1076`). These are annotation debt, not logic defects.

---

## 3. Repository structure

Top-level directories (RUN `find . -maxdepth 1 -type d`): `.github/`, `app/`, `database_schemas/`, `db/`, `docs/`, `eval/`, `scripts/`, `tests/`, `web/`, plus gitignored `Log/`, `Temp/`, `.venv/` and caches.

Python line counts (RUN `find <dir> -name "*.py" | xargs cat | wc -l`, 15:15:30, excluding `eval/spider/`):

| Path | Files | Lines | Purpose | In live request path? |
|---|---|---|---|---|
| `app/main.py` | 1 | 1206 | FastAPI app, all v1 routes | yes |
| `app/models.py` | 1 | 564 | Pydantic request/response + pipeline dataclasses | yes |
| `app/agent/` | 4 | 1128 | `chain.py` 673, `tools.py` 341, `prompt.py` 114 | yes |
| `app/api/` | 2 | 106 | `auth.py` — `/auth` router | yes |
| `app/core/` | 8 | 773 | config, retriever, embeddings adapter, result formatter, two shims, observability | yes |
| `app/schema_pipeline/` | 11 | 2723 | `schema_documenting.py` 890, `builder.py` 518, `introspector.py` 362, … | yes |
| `app/security/` | 7 | 564 | auth 91, `db_readonly_checker` 162, ratelimit 81, sessions 89, user_auth 70, tenancy 42, passwords 29 | yes |
| `app/utils/` | 3 | 372 | logger 253, tool_cache 119 | yes |
| `app/sqlpolicy/` | 11 | 1893 | v2 SQLGlot AST policy engine | **yes** (via shim) |
| `app/execution/` | 5 | 1201 | v2 single execution path | **yes** |
| `app/platform/` | 5 | 653 | modes 212, network_policy 307, secrets 126, settings 7 | partly |
| `app/evaluation/` | 1 py | 0 py | only `datasets/adversarial/sql_policy_cases.yaml` (294 lines) | no |
| `app/embeddings/` | 5 | 425 | created 14:48–14:50 during the audit | no |
| `app/llm/` | 11 | 1763 | created 14:42–14:45 during the audit | **no** |
| `app/retrieval/` | 6 | 1288 | created 15:01 during the audit | **no** |
| `app/analysis/` | 4 | 630 | created 15:08 during the audit | **no** |
| `app/graph/` | 6 | 1442 | created 15:12 during the audit | **no** |
| `app/static/` | 3 | — | dev chat UI (html/css/js) | yes, unauthenticated |
| `db/` | 8 | 918 | model 89, conversation_memory 239, langchain_memory 80, database_manager 136, verified_queries 125, migrate 49, migrations/ | yes |
| `tests/` | 39 | 4421 | | — |
| `eval/` | 8 | 824 | **untracked** (§13) | — |
| `scripts/` | 1 | 172 | `seed_demo_db.py` | — |
| `run.py` | 1 | 12 | uvicorn entrypoint | — |

**Total Python excluding `eval/spider/`: 23,173 lines** (RUN, 15:15:30).

**Wiring status of the four newest packages** (RUN `grep -rn "app\.graph\|app\.retrieval\|app\.analysis\|app\.llm" app db --include=*.py`, excluding self-references): **zero importers.** None of `app/llm/`, `app/retrieval/`, `app/analysis/`, `app/graph/` is reachable from `app/main.py` or `app/agent/chain.py` as of 15:15. They are Phase-4/5 work landing beside the running system, not inside it. This distinction matters in §6 and §7, where the shipped behaviour differs from what those directory names imply.

`database_schemas/` holds three enrolled flags: `crm_db/`, `demo/`, `eval_store/` — the last untracked (RUN `git status --short` → `?? database_schemas/eval_store/`).

---

## 4. API surface

Fourteen routes. Decorator lines re-verified at 15:14 (RUN `grep -n "^@app\.\(get\|post\|delete\|put\)" app/main.py`).

| Method | Path | Decorator | Response model | Auth dependency | `_enforce_db_access`? | Behaviour |
|---|---|---|---|---|---|---|
| GET | `/chat` | `main.py:137` | FileResponse | **none** | no | Serves `app/static/chat.html`, else redirects to `/docs` |
| GET | `/health` | `main.py:239` | `HealthResponse` | none | no | Static liveness 200 |
| GET | `/ready` | `main.py:246` | JSONResponse | none | no | `SELECT 1` on project Postgres, checks `pg_extension` for `vector`; 200/503 |
| GET | `/databases` | `main.py:417` | `DatabasesResponse` | `require_api_key_if_enabled` | **no** — owner-filters the result set instead | Lists enrolled DBs; never returns connection strings |
| POST | `/training/pairs` | `main.py:461` | `VerifiedPair` | `require_api_key_if_enabled` | **yes** (`main.py:468`) | Saves a question→SQL pair after read-only validation |
| GET | `/training/pairs` | `main.py:478` | `VerifiedPairsResponse` | `require_api_key_if_enabled` | **no** — scoped by `owner_id` at `db/verified_queries.py:98` | Lists pairs |
| DELETE | `/training/pairs/{pair_id}` | `main.py:491` | `dict[str,bool]` | `require_api_key_if_enabled` | **no** — scoped at `db/verified_queries.py:110` | Deletes pair + its pgvector doc |
| GET | `/schemas/{db_flag}` | `main.py:503` | `SchemaResponse` | `require_api_key_if_enabled` | **yes** (`main.py:510`) | Reads `database_schemas/<flag>/schema/schema_index.yaml` |
| POST | `/run_sql` | `main.py:541` | `QueryResponse` | `require_api_key_if_enabled` | **yes** (`main.py:552`) | Runs user-edited SQL through the same policy + executor |
| POST | `/query` | `main.py:578` | `QueryResponse` | `require_api_key_if_enabled` | **yes** (`main.py:595`) | NL→SQL agent, execute, summarize |
| POST | `/schemas/embeddings` | `main.py:870` | `SchemaEmbeddingResponse` | **`require_api_key`** (always) | **no** | Re-embeds an arbitrary `db_flag` into `collection_name` |
| POST | `/schemas/enroll` | `main.py:923` | `SchemaPipelineResponse` | **none in decorator**; `Depends(resolve_enroll_owner)` at `main.py:929` | **no** | Enroll + extract + document + embed |
| GET | `/` | `main.py:1180` | dict | none | no | Static landing JSON |
| POST | `/auth/register` | `api/auth.py:49` | `UserOut` (201) | none | n/a | Argon2id hash, create session, set cookie |
| POST | `/auth/login` | `api/auth.py:69` | `UserOut` | none | n/a | Timing-equalised verify (`api/auth.py:22,75`), rehash-on-login, session cookie |
| POST | `/auth/logout` | `api/auth.py:96` | 204 | none | n/a | Revoke session row, clear cookie |
| GET | `/auth/me` | `api/auth.py:104` | `UserOut` | `Depends(require_user)` | n/a | Current user |

**Middleware order** (READ): `RateLimitMiddleware` added first (`main.py:153`), then CORS (`main.py:159`). Because Starlette applies middleware in reverse registration order, CORS ends up outermost, so even 429 responses carry CORS headers — a deliberate and correct choice for a browser client. `allow_credentials` is enabled only when origins are not `["*"]` (`main.py:162`), with a wildcard-in-production warning at `main.py:166`. The auth router is included at `main.py:173`; `/static` is mounted at `main.py:134`.

**Observation.** The route set is coherent for a single-tenant demo but the auth story is uneven across it: `/schemas/embeddings` is the only route that *always* demands an API key (`main.py:870-873`), while `/schemas/enroll` — the most expensive and most privileged operation in the system — carries no `dependencies=[...]` at all (§10.4).

---

## 5. Data model

`db/model.py` is 89 lines and declares exactly **four** application tables.

| Table | Lines | Key columns |
|---|---|---|
| `users` | `db/model.py:7-20` | `id` PK; `email` String(320) unique NOT NULL indexed; `password_hash` String(255) NOT NULL; `is_active` Bool default True; `is_admin` Bool default False; `created_at` DateTime(tz) server_default now() |
| `user_sessions` | `db/model.py:23-34` | `id` PK; `token_hash` String(64) unique NOT NULL indexed; `user_id` FK→`users.id` ON DELETE CASCADE NOT NULL indexed; `created_at`; `expires_at` NOT NULL |
| `Database_config` | `db/model.py:37-64` | `id` PK; `db_flag` String(100) unique NOT NULL indexed; `db_type` String(50) NOT NULL; `connection_string` Text NOT NULL (**plaintext — §10.6**); `description` Text; `max_rows` Int default 1000; `query_timeout` Int default 30; `intro_template` Text; `exclude_column_matches` Bool default False; `schema_extracted` Bool default False; `schema_extraction_date` DateTime server_default now(); `owner_id` FK→`users.id` ON DELETE SET NULL nullable indexed (NULL = public) |
| `verified_queries` | `db/model.py:67-89` | `id` PK; `db_flag` String(100) NOT NULL indexed; `question` Text; `sql` Text; `embedding_id` String(64) nullable (pgvector doc id); `owner_id` FK nullable indexed; `created_at` |

Note the capital `D` in `Database_config` (`db/model.py:37`) — an inconsistency with the other three snake_case table names that leaks into every quoted identifier, including the repair statement at `db/database_manager.py:73-86`.

**Indexes** created by the Alembic baseline: `ix_users_email` (unique), `ix_user_sessions_token_hash` (unique), `ix_user_sessions_user_id`, `ix_Database_config_db_flag` (unique), `ix_Database_config_owner_id`, `ix_verified_queries_db_flag`, `ix_verified_queries_owner_id` (`db/migrations/versions/0001_baseline.py:50,71,72,97,98,124,125`).

**A deliberate omission, documented in the source.** `db/model.py:50-52` states that read-only enforcement flags are kept *out* of the schema to avoid migrations. That was a reasonable trade when there was no migration tooling; now that Alembic is in place (§15.3) the rationale has expired, and the consequence is that a read-only verification result has nowhere to live (§17.3).

**What is not modelled** (RUN `grep -rniE "\bjob\b|snapshot|fingerprint|pii|glossary" db/` → zero hits): no schema snapshots, no snapshot versions or fingerprints, no run records, no job or queue table, no PII column tags, no business glossary or metric definitions, no approval records, no provider/routing records, no audit log. Several v2 features are blocked on this absence — most directly the approval flow (§6.4) and resumable enrollment (§11.4).

**Library-created tables in the same Postgres**, not modelled here: `langchain_pg_collection` / `langchain_pg_embedding` via `PGVector` (`app/core/retriever.py:58-63`); LangGraph store tables via `PostgresStore.setup()` (`db/langchain_memory.py:50`); LangGraph checkpoint tables via `PostgresSaver.setup()` (`db/langchain_memory.py:59`).

---

## 6. The agent as built

### 6.1 It is a tool-calling loop, not a state graph

The shipped agent is `langchain.agents.create_agent`, not a hand-written LangGraph `StateGraph`:

- `from langchain.agents import create_agent` (`app/agent/chain.py:13`)
- `from langchain.agents.structured_output import ToolStrategy` (`chain.py:15`)
- `create_agent(model=llm, tools=tools, system_prompt=system_prompt, response_format=ToolStrategy(LLMResponse), middleware=[debug_model_call, _postgres_checkpoint_middleware])` (`chain.py:308-314`)

LangGraph appears only as a persistence backend: `langgraph.checkpoint.base` (`chain.py:29-30`) and `langgraph.checkpoint.postgres` / `langgraph.store.postgres` (`db/langchain_memory.py:9-10`).

**A `StateGraph` does now exist in the tree** — `app/graph/query_graph.py:25` imports `END, START, StateGraph` and `build_query_graph` constructs one at `app/graph/query_graph.py:82-84`. It was created at 15:12 during the audit and **nothing imports it** (RUN, §3). The shipped request path is unchanged.

### 6.2 The five tools

Registered at `chain.py:296-302`, defined in `app/agent/tools.py`:

| Tool | Lines | Retrieval |
|---|---|---|
| `search_tables(query, k=4)` | `tools.py:169-203` | pgvector, `section="summary"` |
| `search_verified_queries(query, k=3)` | `tools.py:298-326` | `section="verified_qsql"` |
| `fetch_table_summary(table_name, db_schema)` | `tools.py:206-240` | k=1 |
| `fetch_table_section(table_name, section, db_schema)` | `tools.py:243-279` | k=1; `VALID_SECTIONS = {summary, header, columns, relationships, stats}` (`tools.py:29`) |
| `validate_sql(sql)` | `tools.py:282-295` | calls `sql_validator.validate_sql`, never cached |

There is **no join-path tool** and no join graph (§18.2).

### 6.3 LLM calls per `/query` request

- One agent loop at `main.py:671` (`agent.invoke({...})`). Each turn of a `create_agent` loop is one chat completion. Minimum 1; **not verified** as a measured figure — I did not execute a live `/query`. The practical range of 4–8 is *inferred* from the prompt mandating `search_verified_queries` + `search_tables` + `columns` + `relationships` per table + `validate_sql` (`app/agent/prompt.py:50-57`).
- **Verified hard bounds** (RUN `grep -n "recursion_limit|max_iterations|config=" app/agent/chain.py app/main.py` → no hits): no recursion limit is configured anywhere, so LangGraph's default governs and nothing in this repository caps it.
- **Exactly one additional call** for the natural-language summary: `summarize_query_results(...)` at `main.py:788` → `chain.py:536-634`. On failure it retries across every other available provider (`chain.py:546`, `:630-632`), so this step alone can be N calls.
- **Provider fallback wraps the whole loop:** `for provider_idx, provider in enumerate(providers)` at `main.py:640` rebuilds the agent and retries from scratch per provider (`main.py:644-680`).
- The tool-call guard is per-run, not per-cost: `_MAX_TOOL_CALLS_PER_TOOL = 8` (`tools.py:130`) returns an abort *hint string* rather than stopping the loop (`tools.py:158-165`).

Net: request cost is unbounded by construction. Nothing counts tokens (`app/utils/token_tracker.py` was deleted in Phase 1, §20) and nothing counts calls.

### 6.4 The checkpoints are write-only

`_postgres_checkpoint_middleware` hand-constructs a checkpoint dict and persists it:

- `_build_checkpoint_payload` builds `{v, id, ts, channel_values: {query_text, response_text, db_flag}, channel_versions: {agent: time.time_ns()}, versions_seen: {}, updated_channels: ["agent"]}` (`chain.py:155-174`).
- `_persist_checkpoint` builds `config={"configurable": {"thread_id": session_id, "checkpoint_ns": db_flag}}`, `metadata={"source": "input", "step": 0}`, lazily initialises the global `CHECKPOINTER` (`chain.py:199`), then calls `CHECKPOINTER.put(...)` (`chain.py:208`). It fires only when `user_id`, `session_id` and `db_flag` are all present (`chain.py:181-182`).

**Nothing ever reads them.** RUN `grep -n "CHECKPOINTER\." app/agent/chain.py` → one hit only, line 208, `.put(...)`. RUN `grep -rn "get_tuple|\.list\(|aget_tuple" app db` → zero hits. `create_agent` is never passed a `checkpointer=` argument (`chain.py:308-314`). The payloads are also the wrong shape for LangGraph resumption — a channel named `agent` with `versions_seen={}` is not something a graph run produces.

Actual conversation continuity comes from a different mechanism entirely: `db/conversation_memory.py` over `PostgresStore` (namespaces at `db/conversation_memory.py:32-36`), read back by `_build_context_from_history` (`chain.py:319-376`) via `get_query_history(..., limit=3)`. The checkpoint writer is dead weight on every request that has a session.

### 6.5 No human-in-the-loop exists

RUN `grep -rn "interrupt|HumanInTheLoop|human_in_the_loop" app db tests` → the only hit is the word "interrupted" inside a timeout-error regex (`app/execution/service.py:116`).

The policy engine *can* return `NEEDS_APPROVAL` (`app/sqlpolicy/engine.py:222`) and the execution service refuses it with `error_category="needs_approval"` (`app/execution/service.py:183-186`) — but there is no resume path, no approval store (§5), and no API to grant one. Six corpus cases expect `needs_approval` (RUN, §8.4), so the decision is exercised in tests; the workflow behind it is not built.

### 6.6 A naming trap

`get_cached_agent` (`chain.py:485`) and `get_cached_agent_with_context` (`chain.py:457`) are **not cached** — RUN `grep -n "lru_cache|@cache" app/agent/chain.py` → zero hits. Every request constructs a fresh chat client and a fresh agent. The names actively mislead.

---

## 7. Model providers

`PROVIDER_PRIORITY` (`chain.py:55-62`), in order: `openai`/`OPENAI_API_KEY`, `openrouter`/`OPENROUTER_API_KEY`, `deepseek`/`DEEPSEEK_API_KEY`, `groq`/`GROQ_API_KEY`, `anthropic`/`ANTHROPIC_API_KEY`, `gemini`/`GOOGLE_API_KEY`.

`get_available_providers()` (`chain.py:65-79`) appends a provider when its env key is set, then **unconditionally appends `gemini` even with no API key** — `chain.py:71-74`, with the comment "Always add Gemini as fallback provider (free tier)". `get_llm` reinforces this at `chain.py:429`: `if provider_normalized == "gemini" or (key_env is None) or os.environ.get(key_env)`.

The consequence is worth stating plainly: on a keyless install the chain always terminates in a `ChatGoogleGenerativeAI` construction that succeeds at build time and fails at call time, and *that* failure is what surfaces to the user as `502 LLM providers unavailable`. The diagnostic points at the wrong place.

Per-provider construction (`chain.py:382-414`), all at `temperature=0.1`:

| Provider | Model | Notes |
|---|---|---|
| openai | `gpt-4o` | |
| openrouter | `kwaipilot/kat-coder-pro:free` | `base_url="https://openrouter.ai/api/v1"` |
| deepseek | `deepseek-chat` | `api_base="https://api.deepseek.com/v1"` |
| groq | `qwen/qwen3-32b` | |
| anthropic | `claude-3-opus-20240229` | |
| gemini | `gemini-2.5-flash` | **no `api_key` argument at all** |

Aliases: `google→gemini`, `llama→groq`, `llama4→groq` (`chain.py:416-420`).

**What does not exist:**

- **No health check** — no probe before selection.
- **No circuit breaker** — a dead provider is retried on every request, because the loop is rebuilt fresh at `main.py:640`.
- **No capability routing** — no notion of which model can do structured output, long context, or SQL well. Selection is purely env-key order.
- **No cost or token accounting** (§6.3).
- **No record of which provider answered** beyond `logger.info("Generated SQL using provider=%s", provider)` at `main.py:682`. Neither `QueryResponse` (`app/models.py:308-325`) nor `ExecutionMetadata` (`app/models.py:243-270`) carries a provider or model field, so an operator cannot attribute a bad answer to a model after the fact.
- Rate-limit detection is a substring test: `"429" in error_str or ("rate" in error_str and "limit" in error_str)` (`main.py:688`).

A router with a real breaker is landing at `app/llm/router.py` (created 14:45 during the audit). It is **not wired into `chain.py`** (RUN, §3), and four of its tests were failing at 14:52.

---

## 8. SQL validation — before and after

### 8.1 The old validator

READ `git show HEAD:app/core/sql_validator.py` — 182 lines, sqlparse + regex. Entrypoint `validate_sql(sql, db_flag=None)` at `old:111`, in order:

1. strip trailing `;`, reject empty (`old:118-122`)
2. `sqlparse.split` length > 1 → "Multiple statements are not permitted" (`old:33-35`, `:124`)
3. length > `MAX_SQL_LENGTH_CHARS = 5000` (`old:30`, `:127`)
4. `READ_ONLY_PATTERN = ^\s*(with|select)\b` textual match (`old:10`, `:131`)
5. `sqlparse.parse`, re-check the first non-whitespace token (`old:38-45`, `:142`)
6. `SELECT_INTO_PATTERN = \bselect\b[\s\S]*\binto\b` (`old:29`, `:147`)
7. `_contains_forbidden_keyword`: an 18-word `FORBIDDEN_WORDS` set (`old:11-28`) matched two ways — exact token match when `ttype in (Keyword, DML)`, **plus a raw substring test `if f" {bad} " in f" {val} "`** (`old:59-61`)
8. substring test for `information_schema`, `sys.`, `pg_catalog.` in lowercased text (`old:157`)
9. optional schema-index scope: `_extract_referenced_tables` regexes `\b(?:from|join)\s+([\w\[\]".]+)` and takes the last dotted segment (`old:65-79`); CTE names via `(?:\bwith\b|,)\s+(\w+)\s+as\s*\(` (`old:82-87`); fails closed when no `schema_index.yaml` (`old:163-172`)

**Where this design was sound.** It fails closed on a missing schema index (`old:163-172`), it rejects multiple statements before anything else, and it is cheap and dependency-light. For a single-tenant demo against a database the operator controls, enforcing statement *shape* is a defensible 80 % control.

**Where it was structurally limited.** Because it has no AST, it cannot see node types (a `DELETE` nested inside a CTE), cannot resolve columns at all (no column check exists), cannot normalise identifiers or dialects, has no notion of complexity or cost, and matches keywords by substring against token text. Those are not bugs to be patched; they are consequences of the representation.

### 8.2 RUN-verified gap evidence

I extracted the `HEAD` version into a scratchpad and executed it against the v2 adversarial corpus (255 cases). **No database was touched** — only the decision was compared.

**With `db_flag="eval_store"`** (a real schema index of customers/products/orders/order_items is present — the fair comparison):

- **59 of 255 cases that the corpus requires DENY were ALLOWED.** By category: `dos` 11, `file` 8, `side_effect` 6, `config` 6, `unknown_column` 5, `obfuscation` 4, `cartesian` 4, `lock` 2, `cross_db` 2, `exec` 2, `wrong_schema` 2, `network` 1, `nesting` 1, `joins` 1, `window` 1, `cte_count` 1, `unknown_table` 1, `ambiguous` 1.
- **10 cases that the corpus requires ALLOW were DENIED.** Observed reasons: `Detected forbidden keyword: replace` on `SELECT REPLACE(name,'a','b')`; `Detected forbidden keyword: drop` on `WHERE name = 'a; DROP TABLE customers'` (a *string literal*); `SQL must start with SELECT or WITH (CTE)` on `/* hint */ SELECT …` and on `(SELECT id FROM customers)`; `Unknown or unauthorized tables referenced` on quoted/bracketed identifiers (`"demo"."customers"`, `[dbo].[customers]`), on CTE-defined names, and on lateral references.

**With `db_flag=None`** (no scope layer): 94 allowed-but-must-deny, 4 denied-but-must-allow. Concrete bypasses printed from that run: `SELECT pg_sleep(10)`, `SELECT pg_read_file('/etc/passwd')`, `SELECT lo_import('/etc/passwd')`, `SELECT dblink_exec('host=evil','DROP TABLE x')`, `SELECT nextval(...)`, `SELECT setval(...)`, `SELECT pg_terminate_backend(123)`, `SELECT set_config('search_path','pg_catalog',false)`, `SELECT sql FROM sqlite_master`, `SELECT usename, passwd FROM pg_shadow`, `SELECT rolname, rolpassword FROM pg_authid`, `SELECT user, authentication_string FROM mysql.user`, `SELECT name FROM master..sysdatabases`, `SELECT * FROM OPENQUERY(linked,'SELECT 1')`, `SELECT LOAD_FILE('/etc/passwd')`, `SELECT BENCHMARK(100000000, MD5('x'))`, `SELECT load_extension('/tmp/evil.so')`, `SELECT @@version`, `SELECT id FROM customers FOR SHARE`, and an invisible-character family (`SELECT\xa0id`, zero-width-joined `SELECT id​FROM`, `SELECT\x0bid`, homoglyph `сustomers`).

**Caveat on this experiment.** My first attempt with `db_flag="eval_store"` returned "No schema index found" for every case; a standalone probe then showed the index loads correctly, and the corrected run produced the 59/10 figures. **I could not identify the cause of the first run's failure** — most likely an import-ordering fluke inside the module's broad `try/except` at `old:107-108`. The 59/10 numbers are the ones to use; the 94/4 figures are the `db_flag=None` variant, not a correction of them.

**The honest framing.** The old validator enforced *statement shape* — single statement, SELECT/WITH prefix, keyword denylist, three system-schema substrings — and nothing else. The corpus enumerates the classes it structurally could not see: dangerous built-in functions, file and network functions, session-config reads, sequence side effects, row locks, system catalogs beyond three substrings, cross-database references, invisible/homoglyph obfuscation, column-level scope, and any resource or complexity bound. It should be reported in both directions: it under-blocked (59) **and** over-blocked (10) on the same corpus. The over-blocking mattered to users daily; the under-blocking mattered only against an adversary.

### 8.3 What replaced it

`app/core/sql_validator.py` is now a 47-line shim (`sql_validator.py:1-47`). `evaluate_sql()` builds a `PolicyContext` (dialect via `Dialect.from_db_type`, scope via `scope_from_schema_index(db_flag, d)`, `require_scope=bool(db_flag)`); `validate_sql()` returns `decision.as_legacy_dict()`, preserving the v1 `{valid, reason}` shape while adding `decision`, `fingerprint`, `transformed_sql`, `tables` and `policy_version`. Keeping the old shape was the right call — it let the engine be swapped without touching callers.

Engine stages, documented at `app/sqlpolicy/engine.py:1-17` and implemented at `engine.py:44-225`:

1. length check, then parse exactly one statement in the declared dialect (`engine.py:63-82`)
2. root must be in `ALLOWED_ROOT_TYPES = (exp.Select, exp.SetOperation)` (`rules.py:20`), then walk the entire tree rejecting `DENIED_NODE_TYPES` (`rules.py:25`; `engine.py:94-104`) — this is what catches a `DELETE` nested inside a CTE
3. blocked functions per dialect, including prefix matching (`rules.py:113`, `:193-204`; `engine.py:110-121`, `:239-249`)
4. object allowlist: system catalogs, cross-database refs, unknown tables, unresolvable columns, sensitive columns → deny or `needs_approval` (`engine.py:124-158`)
5. complexity limits per `PolicyLevel` (`engine.py:161-168`)
6. AST row-limit injection plus optional parameterisation (`engine.py:170-197`)
7. **the old heuristic runs as an independent second layer; the stricter verdict wins** (`engine.py:200-217`)
8. fingerprint plus normalised SQL (`engine.py:219-225`)

`POLICY_VERSION = f"sql_policy@{rules.RULESET_VERSION}"` = `sql_policy@2.0.0` (`engine.py:40`, `rules.py:13`).

Retaining the heuristic as a second opinion is a good defensive choice: it means the AST engine cannot silently *lose* a control the old one had. `app/sqlpolicy/legacy_heuristics.py:1-11` names the five original false positives it deliberately fixed — substring matches inside identifiers (`grant_total`), forbidden words inside string literals, `REPLACE(...)` the function versus MySQL `REPLACE INTO`, a leading comment, and `EXTRACT(YEAR FROM col)` parsed as a table reference.

### 8.4 The corpus

`app/evaluation/datasets/adversarial/sql_policy_cases.yaml`, 294 lines, dataset version 2.0.0. RUN (yaml load, 15:18): **255 cases — 176 deny, 73 allow, 6 needs_approval.** Cases are expanded per-dialect by `tests/sqlpolicy/test_adversarial_corpus.py:34-43`.

Nine labelled sections (lines 23, 80, 102, 119, 149, 184, 198, 225, 234): A direct writes/DDL/admin, B writes hidden inside read syntax, C multiple statements and obfuscation, D system catalogs and cross-database, E dangerous functions, F resource exhaustion and complexity, G allowlist (tables/columns/schemas), H sensitive columns, I false-positive guards.

Categories by count: fp 61, catalog 26, admin 16, write 14, dos 12, exec 11, obfuscation 9, file 9, ddl 9, unknown_table 8, write_cte 7, sensitive 7, side_effect 6, multi 6, config 6, unknown_column 5, schema_ok 5, network 5, lock 5, session 4, cartesian 4, txn 3, wrong_schema 2, recursive 2, cross_db 2, and one each of window, using_ok, union, star_ok, nesting, limit, joins, cte_shadow, cte_name, cte_count, ambiguous.

That 61 of 255 cases are false-positive guards is the most telling design fact here: the corpus was built to hold the *usability* line, not just the security line — a direct response to the 10 over-blocks in §8.2.

The test asserts at least 100 distinct ids, at least 100 deny and at least 40 allow (`test_adversarial_corpus.py:46-50`); that denied decisions never carry `transformed_sql` (`:76-81`); and that every ALLOW has a fingerprint, transformed SQL and `legacy_agrees is True` (`:71-73`).

---

## 9. Query execution

### 9.1 Before v2

READ `git show HEAD:app/core/query_executor.py`, 265 lines.

- **Connection:** `database_manager.get_connection(connection_string)` (`old:164`) — a raw `engine.connect()` from an `lru_cache(maxsize=8)` engine created with only `pool_pre_ping=True, pool_recycle=1800` (`db/database_manager.py:65-70`, `:133-136`). **No pool ceiling, no connect timeout.**
- **Read-only transaction: none.** No `BEGIN`, no `SET TRANSACTION READ ONLY`, no `PRAGMA query_only`, no rollback. The connection is simply `conn.close()`d (`old:218`), and when `include_total` is set a *second* connection is opened for the count (`old:237`).
- **`execution_options(timeout=query_timeout)` (`old:211`, `:214`, `:240`) did not enforce a statement timeout.** `timeout` is not a recognised SQLAlchemy execution option; the documented keys are `compiled_cache`, `logging_token`, `isolation_level`, `no_parameters`, `stream_results`, `max_row_buffer`, `yield_per`, `insertmanyvalues_page_size`, `schema_translate_map`, `preserve_rowcount`, plus dialect-specific ones. An unrecognised key is stored in `_execution_options` and ignored. Concretely: psycopg/psycopg2 take a statement timeout from `SET statement_timeout` or `options=-c statement_timeout=…` in the DSN; pymysql from `read_timeout`/`write_timeout` at connect time or `MAX_EXECUTION_TIME`; pyodbc from `Connection.timeout` on the DBAPI connection. None of those was reached. **Not verified experimentally** — this rests on SQLAlchemy's documented key set and on driver behaviour, not on a live test against a database. It is worth one confirming link to the SQLAlchemy docs when this document is reviewed.
- **f-string pagination** (`_wrap_sql_with_pagination`, `old:72-113`): Postgres/MySQL/default → `f"SELECT * FROM ({sanitized_sql}) AS _sub LIMIT {page_size} OFFSET {offset}"`; MSSQL regex-splits an existing `ORDER BY` and emits `f"{base_sql} ORDER BY {order_expr} OFFSET {offset} ROWS FETCH NEXT {page_size} ROWS ONLY"`, raising `ValueError` when there is no `ORDER BY` unless `ALLOW_MSSQL_AUTO_ORDER_BY` is truthy (`old:86-98`), in which case it appends `ORDER BY (SELECT NULL)`. `_extract_first_select_column` (`old:55-69`) is a regex column guess.
- **f-string COUNT wrapper** (`old:238`): `f"SELECT COUNT(*) FROM ({sanitized_sql}) AS _count_sub"`.
- **Row cap via `fetchmany`** (`old:216-217`): `columns = result.keys(); rows = result.fetchmany(int(page_size or max_rows))`. Because it fetched exactly the cap, truncation was indistinguishable from "the answer happens to be N rows" — there was no `+1` probe and no `truncated` flag anywhere in the payload. A user reading 1,000 rows had no way to know whether that was the answer or the ceiling.
- **`_has_unbound_parameters` false positives** (`old:36-52`): returns True for **any** `@word`, **any** `:word`, any `%s`, and **any literal `?`**. That rejects legitimate SQL such as `WHERE email LIKE '%@gmail.com'`, T-SQL `@@version`, Postgres casts written `col::int` (the `:` plus word matches `\:[A-Za-z_]\w*`), JSON path operators, and any string containing a question mark — including corpus case `ok-008`, `SELECT id FROM customers WHERE name = 'what?'`. The failure mode was a hard user-visible error at `old:193-197`.
- Dead code: `old:261-265` are unreachable, a second `return` after `return result_payload` at `old:260`.

### 9.2 After v2

READ `app/execution/service.py` (347 lines) and `app/execution/connections.py` (247 lines).

`execute()` (`service.py:175-284`):

- **Re-evaluates policy even when a decision is supplied**, accepting a caller decision only as an approval token whose fingerprint must match (`service.py:187-196`). This is the right shape: the caller cannot hand in a verdict.
- Checks the target host against the network policy (`service.py:198-209`).
- Opens `read_only_connection(...)` (`service.py:229-231`).
- **Fetches `effective_cap + 1` rows so truncation is detected, not guessed** (`service.py:235-242`, plus `_filled_the_cap` at `:287-299`) — directly fixing the §9.1 ambiguity.
- Builds the COUNT from the AST via `count_wrapper` instead of string concatenation (`service.py:302-311`); paginates via `apply_pagination` on the parsed AST (`service.py:212-221`).
- Driver errors are stripped of DSNs and credential-shaped key/value pairs and classified into 8 categories before leaving (`service.py:100-152`).

`read_only_connection` (`connections.py:182-217`) opens a transaction and **always rolls back** (`connections.py:212-217`). Per dialect (`read_only_setup`, `connections.py:140-179`):

| Dialect | Mechanism | `enforced` |
|---|---|---|
| Postgres | `SET TRANSACTION READ ONLY` + `SET LOCAL statement_timeout` / `lock_timeout` / `idle_in_transaction_session_timeout` | **True** |
| MySQL | `START TRANSACTION READ ONLY` (`connections.py:200`) + `SET SESSION MAX_EXECUTION_TIME` | **True** (MariaDB's spelling skipped when unsupported, `connections.py:158-159`, `:206-209`) |
| MSSQL | only `SET LOCK_TIMEOUT` plus pyodbc `Connection.timeout` (`connections.py:220-227`) | **False** (`connections.py:161-167`) |
| SQLite | `PRAGMA query_only = ON` on connect (`connections.py:128-137`) | True |
| unknown | `ReadOnlySetup(False, (), …)` (`connections.py:174-179`) | False |

The MSSQL note is explicit and correct: SQL Server has no session-level read-only mode, so enforcement there rests on the policy engine plus a least-privilege login. Engines are now bounded: `pool_size=5`, `max_overflow=2`, `pool_recycle=1800`, `connect_timeout=10` (`connections.py:39-42`, `:101-125`).

### 9.3 The old executor is now dead

`app/core/query_executor.py` is a 74-line shim building an `ExecutionRequest` and calling `app.execution.service.execute`. **RUN `grep -rn "query_executor" app db tests eval scripts` → zero importers.** Coverage confirms it: `18 stmts, 18 missed, 0%` (RUN, 15:17:45). It is a compatibility shim nothing is compatible with any more.

---

## 10. Authentication, tenancy and secrets

### 10.1 API key gate

`app/security/auth.py`, 91 lines. `X-API-Key` header (`auth.py:23-24`).

`require_api_key` (`auth.py:33-57`): no tokens configured and not required → allow with a one-time warning (`auth.py:45-51`); no tokens and `effective_auth_required` → **503, fail-closed** (`auth.py:40-44`); tokens configured → constant-time `hmac.compare_digest` against each (`auth.py:28-30`), else 401. `effective_auth_required = auth_required or is_production` (`app/core/config.py:125-131`).

`require_api_key_if_enabled` (`auth.py:60-71`) is a no-op unless `gate_query_endpoint` is True. `resolve_enroll_owner` (`auth.py:74-91`): if `user_auth_enabled` and a session resolves → that user's id; otherwise it **requires an API key** and returns `None`, which makes the enrolled database public.

Fail-closed in production plus constant-time comparison is correct. The limits are that this is a single shared static token with no rotation, no per-key identity, no scopes and no audit trail — and `/query` is ungated by default (`gate_query_endpoint=False`, `config.py:81`).

### 10.2 Passwords and sessions

`app/security/passwords.py` (29 lines): argon2-cffi `PasswordHasher()` defaults (Argon2id); `verify_password` swallows `VerifyMismatchError`/`InvalidHashError` → False (`passwords.py:16-21`); `needs_rehash` drives rehash-on-login (`passwords.py:24-28`, used at `api/auth.py:85-87`).

`app/security/sessions.py` (89 lines): opaque `secrets.token_urlsafe(32)` (`sessions.py:21`, `:36`); **only `hashlib.sha256(token).hexdigest()` is stored** (`sessions.py:24-25`, `:37-43`); `resolve_session` rejects expired rows and normalises naive datetimes from sqlite (`sessions.py:58-68`); `revoke_session` deletes the row (`sessions.py:74-87`).

Cookie: `__Host-dbw_session` when `cookie_secure` else `dbw_session` (`user_auth.py:17-20`); `httponly=True`, `samesite="lax"`, `path="/"`, `max_age=session_ttl_seconds` (`user_auth.py:23-33`). Default TTL 1,209,600 s = 14 days (`config.py:85`). `cookie_secure` defaults to production-or-mode-policy (`config.py:137-142`).

This is a solid, conventional implementation: opaque tokens, hash-at-rest, `__Host-` prefix, timing-equalised login via a dummy hash (`api/auth.py:22`, `:75`). The gaps are a 14-day TTL with no rotation on privilege change, and no lockout or attempt throttling on `/auth/login` beyond the generic per-IP limiter.

### 10.3 Tenancy

`user_auth_enabled` (`config.py:84`) defaults to **False**. It gates `_enforce_db_access` (`main.py:391-393`) and `_resolve_owner` (`main.py:406-410`). With the flag off, `_enforce_db_access` returns immediately and `_resolve_owner` returns `None`, so every request is the anonymous public tenant. Multi-tenancy is therefore off by default and the code paths below are inert in the default configuration.

`app/security/tenancy.py` (42 lines): `owner_id IS NULL` → public, readable by anyone (`tenancy.py:40-41`); otherwise `user_id is not None and owner_id == user_id` (`tenancy.py:42`); unknown flag → False (`tenancy.py:38-39`).

**Endpoints that call `_enforce_db_access`:** `/training/pairs` POST (`main.py:468`), `/schemas/{db_flag}` GET (`main.py:510`), `/run_sql` (`main.py:552`), `/query` (`main.py:595`).
**Endpoints that do not:** `/databases` (owner-filters the result set instead), `/training/pairs` GET and DELETE (scoped inside `db/verified_queries.py:98` and `:110`), `/schemas/embeddings` (**API-key only, no owner check at all**), `/schemas/enroll`, `/chat`, `/`, `/health`, `/ready`, `/auth/*`.

### 10.4 The `/schemas/enroll` ownership gap

Re-verified at 15:14. The decorator at `main.py:923-926` carries **no** `dependencies=[...]`; the only gate is `owner_id: int | None = Depends(resolve_enroll_owner)` (`main.py:929`). There is no `_enforce_db_access`.

`_fetch_or_create_database_config` then does (`main.py:1113-1116`):

```python
db_row = session.query(DatabaseConfig).filter_by(db_flag=request.db_flag).first()
if db_row:
    return db_row
```

**It returns an existing row for any `db_flag` regardless of `owner_id`, and the caller's `owner_id` is discarded on that path.** With `user_auth_enabled=True`, authenticated user B can POST `/schemas/enroll` with user A's `db_flag`; the read-only probe then runs against **A's** stored `connection_string` (`main.py:941-945`), and the pipeline re-extracts, re-documents (incurring LLM cost) and re-embeds A's database into A's collection, overwriting `database_schemas/<A's flag>/schema/*.yaml`.

B cannot read A's connection string back through the API — it is never returned in any response. The impact is unauthorised cost, unauthorised overwrite of another tenant's catalog, and unauthorised connection use, not credential disclosure. Also note `owner_id` is only ever set at INSERT (`main.py:1125`); there is no re-stamp or transfer path.

**Not verified by execution** — established by reading `main.py:929`, `:941-945` and `:1113-1116` together. I did not run it against a live instance with `user_auth_enabled=True`.

### 10.5 A confirmed defect: `schema_extracted` is never set when embeddings run

RUN `awk` over `app/main.py:1060-1092`, 15:14. `_mark_schema_extracted(request.db_flag)` is at **`main.py:1089`**, indented 12 spaces, inside the `else:` branch at **`main.py:1080`** — which is the `else` of `if request.run_embeddings:` at **`main.py:1062`**.

Therefore: **when `run_embeddings=True` — the full enrollment path — `_mark_schema_extracted` is never called**, and `Database_config.schema_extracted` stays False. The flag is only set when embeddings are *skipped*.

Consequences (READ): the "already enrolled, skipping" fast path at `main.py:971-1005` keys off `db_row.schema_extracted`, so a fully enrolled database re-runs the entire pipeline — extraction, N LLM documentation calls, and re-embedding — on every subsequent enroll call. This inverts the intended caching and is the single most expensive bug in the audited surface. It was flagged as uncertain in my working notes and is now confirmed against the current file.

### 10.6 Secrets at rest — declared but not wired

`app/platform/secrets.py` (126 lines) implements MultiFernet with an `enc:v1:` prefix and a key-rotation story (`secrets.py:1-10`, `:19`, `:43-60`), and is well tested (`tests/test_secrets.py`, 9 tests, 92 % covered).

**RUN `grep -rn "platform.secrets|platform import secrets" app db tests`** → the only hits are `tests/test_secrets.py:7`, plus the module's own docstring and `__main__` CLI lines (`secrets.py:7`, `:108`). **No production code encrypts or decrypts anything.**

`DatabaseConfig.connection_string` is `Column(Text, nullable=False)` (`db/model.py:43`), written in plaintext at `main.py:1121` and read back in plaintext at `app/user_db_config_loader.py:70`. `AppModePolicy.secret_encryption_required=True` for both self-hosted and production (`app/platform/modes.py:128`, `:148`) is unenforced.

Stated plainly: **target-database credentials are stored unencrypted in the application database today.** The capability to fix it exists and is tested; the wiring does not.

### 10.7 CSRF

Cookies are `SameSite=Lax` (`user_auth.py:31`). There is no double-submit token and no Origin check — RUN `grep -rn "csrf" app` → no hits. `SameSite=Lax` blocks cross-site POST from a plain form navigation, which covers the common case, but it is the only layer.

---

## 11. Schema enrollment and the data catalog

### 11.1 Stage sequence

`SchemaPipelineOrchestrator.run()` (`app/schema_pipeline/orchestrator.py:56-89`):

1. **Extraction** — `_run_extraction` (`orchestrator.py:91-100`) → `SchemaExtractionPipeline` (`pipeline.py:35-58`) → `SQLServerMetadataExtractor`. Despite the name it is dialect-agnostic SQLAlchemy reflection: `MetaData.reflect` over non-excluded schemas (`introspector.py:61-100`), with the database name resolved per dialect (`introspector.py:106-120`). → `SchemaGraphBuilder` (518 lines) → `YamlSchemaWriter` with `backup_existing=True` (`orchestrator.py:97`).
2. **Documentation** (optional, `run_documentation`) — `_run_documentation` (`orchestrator.py:102-111`) → `document_database_schema(...)` in `app/schema_pipeline/schema_documenting.py` (890 lines). This is **an LLM stage**: `SchemaDocumentingAgent.__init__` calls `get_llm(provider)` (`schema_documenting.py:45`) and runs `self.prompt | self.llm.with_structured_output(...)` (`schema_documenting.py:49`) **once per table** (`self.chain.invoke(prompt_state)`, `schema_documenting.py:246`). `incremental=True` by default (`orchestrator.py:33`).
3. **Embeddings** (optional, `run_embeddings`) — `_run_embeddings` (`orchestrator.py:113-133`) → `SchemaEmbeddingPipeline` with `embedding_mode="structured"` (`orchestrator.py:31`), `chunk_size=2000`, `chunk_overlap=100` (`orchestrator.py:41-42`).

### 11.2 Artifacts written

| Artifact | Location | Notes |
|---|---|---|
| Per-table YAML | `database_schemas/<db_flag>/schema/<schema_name>/<table>.yaml` | `writer.py:39` |
| `metadata.yaml` | same dir | `writer.py:42`; verified contents for `demo`: `database_name`, `extracted_at`, `total_schemas`, `total_tables` |
| `schema_index.yaml` | same dir | `writer.py:43`; verified contents: `database_name`, `extraction_date`, `total_schemas/tables/views`, `schemas[]`, `tables[]` each with `table`, `schema`, `object_type`, `keywords`, `column_names[]`, `primary_key[]`, `has_foreign_keys`, `short_description` |
| pgvector collection `<db_flag>_docs` | Postgres | `default_collection_name` (`retriever.py:26-29`; `orchestrator.py:40`), rows in `langchain_pg_embedding` with `use_jsonb=True` (`retriever.py:58-63`) |
| `Database_config` row | Postgres | `_fetch_or_create_database_config` (`main.py:1118-1129`) with hardcoded `max_rows=10000`, `query_timeout=30` (`main.py:1127-1128`) |
| `temp_output/minimal/<db_flag>/` | filesystem | `SchemaEmbeddingPipeline.DEFAULT_OUTPUT_ROOT` (`embedding_pipeline.py:28`) |

`schema_index.yaml` carries three jobs at once: it is the SQL-policy scope source (`scope_from_schema_index`), the `/schemas/{db_flag}` response source, and the extraction record. That concentration is convenient but means a corrupt or stale index silently widens or narrows the policy allowlist.

**A reporting inconsistency:** `temp_output/minimal/<db_flag>/` is only populated when `embedding_mode == "minimal"` (`embedding_pipeline.py:75-81`), but the default `structured` mode still reports that directory in the API response (`main.py:1061`, `:1069`, `:1077`, `:1085`). The directory does not exist in the repo today (RUN `ls temp_output` → empty) and `temp_output/` is gitignored (`.gitignore:28`). Callers are told about a path that will not exist.

### 11.3 It runs synchronously inside the HTTP request

`async def enroll_database` (`main.py:927`) calls `orchestrator.run()` directly on the event loop — no `BackgroundTasks`, no worker, no `run_in_executor`. RUN `grep -rn "BackgroundTasks|celery|rq|arq" app` → no hits.

Because `orchestrator.run()` is synchronous and the handler is `async def`, this blocks the event loop, not merely a thread — a large database stalls the whole worker for the duration of extraction plus N LLM documentation calls plus embedding. `WORKER_POLL_INTERVAL_SECONDS` is declared in settings (`config.py:58`) but there is no worker (§16).

### 11.4 No job record, no snapshot, no resume

No jobs/runs/snapshots table exists (§5). The only state is the boolean `Database_config.schema_extracted` plus `schema_extraction_date`.

Re-enrollment is a boolean fork: `db_row.schema_extracted and not request.incremental_documentation` → everything skipped and a synthetic "already enrolled" report returned (`main.py:971-1005`); otherwise the whole pipeline reruns from scratch. If a request dies mid-way, nothing records where it stopped and the next call restarts at stage 1.

**And per §10.5 the fast path is currently unreachable on the default full-enrollment path**, because `schema_extracted` is never set when `run_embeddings=True`.

### 11.5 Read-only refusal at enrollment

`is_read_only_connection(db_row.connection_string, db_type=...)` at `main.py:941-945`. If the probe returns False → **HTTP 400** naming the reason (`main.py:951-963`), with the code comment at `main.py:939` reading "reject writable connections (fail closed)".

**But if the probe itself raises, the exception is swallowed and enrollment continues** (`main.py:946-950`) — `read_only_ok` was initialised True at `main.py:937` and is never reset, so a probe that errors is indistinguishable from a probe that passed. That is fail-*open* on probe error, inside a control whose own comment claims fail-closed.

---

## 12. Retrieval

- **pgvector `similarity_search` only** — `store.similarity_search(query, k=k, filter=filters or None)` (`app/core/retriever.py:109`). No `similarity_search_with_score`, no MMR, no threshold. Scores are never seen, so nothing can be filtered on relevance.
- **k defaults:** `search_tables` k=4 (`tools.py:170`); `search_verified_queries` k=3 (`tools.py:299`); `fetch_table_summary` / `fetch_table_section` k=1 (`tools.py:229`, `:268`); `vector_search` itself defaults k=3 (`retriever.py:79`).
- **Metadata filter is `{section, db_flag}`** — `_filters_with_context` seeds only `db_flag` from the ContextVar (`tools.py:45-50`); callers add `section` (`"summary"` `tools.py:176`; the requested section `tools.py:252`; `"verified_qsql"` `tools.py:309`) and optionally `table_name`/`schema` (`tools.py:213-215`, `:252-254`).
- **TTL cache:** `TTLCache` LRU+TTL, `TOOL_CACHE_TTL_SECONDS` default 60, `TOOL_CACHE_MAX_ITEMS` default 1024 (`tool_cache.py:17-18`, `:27-67`). Keyed by sha256 of `{q, collection, filters, k}` (`retriever.py:85-92`). Tools also probe the same cache before calling (`tools.py:182-184`, `:227-229`, `:266-268`).
- **A token-bucket limiter that returns `[]` when tripped:** `get_rate_limiter(collection_name)` with `TOOL_RATE_LIMIT_BURST` default 16 and `TOOL_RATE_LIMIT_PER_SEC` default 8 (`tool_cache.py:104-116`). On trip, `vector_search` warns and returns the cached value if any, else **an empty list** (`retriever.py:101-107`). The agent cannot distinguish "no matching tables" from "throttled" — `search_tables` renders both as `"No matching tables found in summaries."` (`tools.py:185-186`). Under load the model is silently told the schema is empty and will hallucinate or refuse.

**Absent:** no lexical/FTS search, no hybrid fusion (no RRF), no join-path expansion (there is no join-path tool at all, §18.2), no reranker, no query rewriting, no neighbour or table expansion, and no token budget on the assembled context.

**A tenancy asymmetry.** `search_verified_queries` filters only `{section: verified_qsql, db_flag}` (`tools.py:309`). Verified pairs embedded by **any** owner for that `db_flag` are retrievable by any caller permitted to query it, even though the `verified_queries` *table* is owner-scoped (`db/verified_queries.py:98`, `:110`). The relational copy is tenant-aware; the vector copy is not.

**The index is probably not being used.** `_ensure_pgvector_index` creates the `vector` extension and a best-effort `ivfflat` index with `lists=100`, **skipping when `vector_dims > 2000`** — which is the case for the default `models/gemini-embedding-001` at 3072 dims. So the deployed default runs on sequential scan, and the code says so in an explicit log line (`retriever.py:124-178`, log at `:160-165`).

---

## 13. Evaluation assets

### 13.1 `eval/` is untracked

- RUN `git ls-files eval | wc -l` → **0**
- RUN `git status --short eval` → **`?? eval/`**
- RUN `git check-ignore -v eval/harness.py` → **no match** (untracked, not ignored)
- `eval/spider/` *is* ignored (`.gitignore:45`); `eval/eval_store.sqlite` by `.gitignore:47` (`*.sqlite`)
- RUN `du -sh eval/spider` → **2.0 G** (`spider_data/`, `spider_data.zip`, `__MACOSX/`)

Every evaluation number the project can quote therefore lives outside version control on one machine.

### 13.2 The scripts

| Script | Lines | What it does |
|---|---|---|
| `eval/harness.py` | 119 | Golden-query execution-accuracy harness. Hits a live `POST http://127.0.0.1:8010/query` with `db_flag="eval_store"` (`harness.py:18-19`); runs gold SQL against hardcoded local `postgresql://eval:evalpass@localhost:55432/evalstore` (`harness.py:20`); compares order-insensitive normalised result sets (`harness.py:24-41`); separately checks unsafe/oos prompts never produced an executed destructive statement (`harness.py:91-98`). Writes `eval/results.json`. |
| `eval/baseline.py` | 138 | "Just ask the LLM" baseline: no retrieval, no validator, full schema in one prompt, scored on `eval_store.sqlite`. **Reads the repo `.env` directly** (`baseline.py:22-30`). |
| `eval/spider_eval.py` | 185 | Spider dev execution accuracy for the generation model only. Deterministic sample `DEV[::7]` ≈ 148 of 1034 (`spider_eval.py:36-40`). Also **reads `.env` directly** (`:24-33`). Its docstring is explicit that it measures NL→SQL *generation*, not the deployed pipeline (`spider_eval.py:8-11`). |
| `eval/spider_fix.py` | 113 | Re-runs only harness-config failures (max_tokens truncation, Groq throttle) once at `max_tokens=8192` and merges; deliberately does not re-run genuine wrong-SQL (`spider_fix.py:1-11`, `:29-39`). |
| `eval/rescore.py` | 79 | Re-executes SAVED SQL against the Postgres store, scoring strict and lenient. Writes `eval/rescore.json`. |
| `eval/make_db.py` | 98 | Builds the deterministic `eval_store.sqlite` (customers/products/orders/order_items, fixed rows). |
| `eval/make_pg.py` | 72 | Copies those rows verbatim into Postgres with a SELECT-only role, because the enrollment guard understands Postgres roles and not SQLite (`make_pg.py:1-6`). Hardcoded local creds at `:14-15`. |
| `eval/_groq_ok.py` | 20 | Poller exiting 0 when Groq answers. **Loads every key from `.env` into `os.environ`** (`:7-11`). No importers. |

Three scripts read the repository `.env` directly rather than through `Settings` (`baseline.py:22-30`, `spider_eval.py:24-33`, `_groq_ok.py:7-11`). Since CI does not lint `eval/` (§14.4), that pattern is unreviewed.

### 13.3 The result files, and why they must not be quoted

All untracked; RUN-read.

| File | Contents |
|---|---|
| `eval/golden.json` | **26 items: 22 answerable, 3 unsafe, 1 oos** |
| `eval/results.json` | `exec_accuracy 0.8181818…`, `answerable [18, 22]`, `fail_closed 1.0`, `unsafe [4, 4]` |
| `eval/rescore.json` | `strict [3, 22]`, `answer_correct [3, 22]`, `fail_closed [4, 4]` |
| `eval/baseline_results.json` | `model "qwen/qwen3-32b (Groq)"`, `n 22`, `correct 22`, `exec_accuracy 1.0` |
| `eval/spider_results.json` | `sampled 148`, `scored 139`, `invalid_gold_skipped 0`, `llm_unavailable_skipped 9`, `correct 101`, `exec_accuracy 0.7266` |
| `eval/spider_results_pass1.json`, `eval/server.log` | prior pass; 64 KB log |

Three problems, all disqualifying for external use:

1. **`results.json` and `rescore.json` contradict each other** — 18/22 versus 3/22 on the same 22 answerable questions. Different scorers, different stores. **They must never be quoted together**, and neither should be quoted alone without saying which scorer produced it.
2. **`baseline_results.json` reads, on its face, as "the pipeline hurts"** — a naive no-retrieval baseline at 100 % against a pipeline at 81.8 % (or 13.6 % on rescore). It also ran against SQLite while the pipeline ran against Postgres, so it is not a like-for-like comparison at all.
3. **`spider_results.json`'s `method` string is 400+ characters and contains a duplicated clause** — the "config-truncated/throttled items were re-run once…" sentence appears twice.

**None of these numbers can be reproduced from the repository today.** `harness.py` and `rescore.py` need a live API on port 8010 plus local Postgres on 55432; `baseline.py` and `spider_eval.py` need a Groq key; the Spider corpus is a 2 GB gitignored download. The roadmap already records the underlying gap — `docs/v2/IMPLEMENTATION_ROADMAP.md:21`: "eval smoke: **no runnable smoke set exists**".

---

## 14. Tests and CI

### 14.1 Test inventory

Per-file `def test_` counts were taken at **14:04** and predate `tests/llm/`, `tests/retrieval/` and `tests/graph/`. They do **not** sum to the 15:17 headline of 1017 passed / 5 failed.

`tests/sqlpolicy/test_engine_units.py` 31 · `tests/test_execution_service.py` 31 · `tests/test_sql_validator.py` 23 · `tests/test_network_policy.py` 14 · `tests/test_result_stats.py` 14 · `tests/test_modes.py` 12 · `tests/test_api_query.py` 10 · `tests/test_hardening.py` 10 · `tests/test_config.py` 9 · `tests/test_sanitizers.py` 9 · `tests/test_secrets.py` 9 · `tests/test_logger_sanitize.py` 7 · `tests/sqlpolicy/test_properties.py` 6 (Hypothesis) · `tests/test_auth.py` 5 · `tests/test_auth_endpoints.py` 4 · `tests/test_embeddings.py` 4 · `tests/sqlpolicy/test_adversarial_corpus.py` 3 (one parametrised over all 255 cases × dialects) · `tests/test_auth_foundation.py` 3 · `tests/test_health.py` 3 · `tests/test_migrations.py` 3 · `tests/test_providers.py` 3 · `tests/test_query_tenancy.py` 3 · `tests/test_ratelimit.py` 3 · `tests/test_sessions.py` 3 · `tests/test_tenancy.py` 3 · `tests/test_introspector.py` 2. Plus `tests/conftest.py` (30 lines) and `tests/sqlpolicy/conftest.py` (68 lines).

### 14.2 CI

`.github/workflows/ci.yml`, 82 lines. Three jobs, on push to main/master and every PR; `concurrency` cancels in-progress runs (`ci.yml:8-10`).

**`backend`** (`ci.yml:13-44`), uv 0.11.6, Python 3.13, `uv sync --frozen`:

| Step | Line | Blocks merge? |
|---|---|---|
| `ruff check app db tests run.py` | `ci.yml:31` | **YES** |
| `ruff format --check app db tests run.py` | `ci.yml:34` | **YES** |
| `pytest --cov=app --cov=db --cov-report=term-missing --cov-fail-under=35` | `ci.yml:37` | **YES**, but the floor is only **35 %** |
| `pip-audit` | `ci.yml:40-41` | **NO** — `continue-on-error: true` |
| `mypy app db \|\| true` | `ci.yml:44` | **NO** — the `\|\| true` swallows every error |

**`frontend`** (`ci.yml:46-67`, working-directory `web`, Node 20): `npm ci` → `npm run lint` (blocks) → `npm run typecheck` (blocks) → `npm run build` (blocks) → `npm audit --audit-level=high` with `continue-on-error: true` (does not block).

**`secrets-scan`** (`ci.yml:69-82`): installs the gitleaks v8.21.2 binary and runs `gitleaks detect --source . --redact --no-banner -v` over full history (`fetch-depth: 0`). **Blocks.** The comment at `ci.yml:75-76` explains the v2 action was replaced because it crashed on PRs — a sensible fix.

### 14.3 Coverage distribution

RUN, 15:17:45. **TOTAL 68 %** against a CI floor of 35 % — roughly 33 points of slack, so the gate would not catch a large regression.

**Best covered — the v2 packages:** `app/sqlpolicy/rules.py` 100 %, `engine.py` 99 %, `parse.py` 99 %, `types.py` 97 %, `allowlist.py` 96 %, `complexity.py` 95 %, `parameters.py` 93 %, `limits.py` 92 %, `legacy_heuristics.py` 88 %, `scope_loader.py` 87 %; `app/execution/results.py` 95 %, `service.py` 89 %, `connections.py` 69 %; `app/platform/modes.py` 97 %, `secrets.py` 92 %, `network_policy.py` 88 %; `app/security/passwords.py` 100 %, `tenancy.py` 100 %, `ratelimit.py` 95 %, `sessions.py` 94 %, `user_auth.py` 94 %, `auth.py` 80 %; `db/migrate.py` 100 %, `db/model.py` 94 %.

**Worst covered — the v1 schema pipeline and what nothing exercises:** `app/core/query_executor.py` **0 %** (dead, §9.3), `app/platform/settings.py` **0 %**, `app/security/db_readonly_checker.py` **8 %** (yet it is the one wired to `/schemas/enroll`), `app/schema_pipeline/schema_documenting.py` **10 %**, `db/conversation_memory.py` 22 %, `db/verified_queries.py` 23 %, `app/core/retriever.py` 27 %, `db/langchain_memory.py` 35 %, `app/execution/readonly.py` 45 % (not wired, §19), `app/main.py` **51 %**.

The inversion is worth naming: the best-tested code is the code not yet fully load-bearing, and the least-tested code (`db_readonly_checker.py` at 8 %, `main.py` at 51 %) is what actually runs on every request.

### 14.4 Not in CI at all

No Postgres service container — so nothing in CI exercises pgvector, Alembic-on-Postgres, or the PGVector retriever, and the SQLite-only paths are what get tested. No e2e. No eval gate. No bandit, Trivy or CodeQL. No import-linter run (`import-linter` is a declared dev dep at `pyproject.toml:83` but there is no `[tool.importlinter]` config). No `vulture` run (dev dep, `pyproject.toml:77`). CI lints `app db tests run.py` but **not `eval/` or `scripts/`**.

`.pre-commit-config.yaml` (26 lines) adds trailing-whitespace / EOF / check-yaml / check-toml / merge-conflict / large-files (2 MB) / detect-private-key, ruff `--fix`, ruff-format and gitleaks — but pre-commit is opt-in per developer (`uv run pre-commit install`).

---

## 15. Deployment

### 15.1 Dockerfile

59 lines, two-stage. Builder `python:3.13-slim-bookworm` with `uv` copied from `ghcr.io/astral-sh/uv:0.11` (`Dockerfile:6-7`); `uv sync --frozen --no-dev --no-install-project` on manifest and lock only for layer caching (`Dockerfile:14-15`) — base deps only, **no `--extra local-embeddings`, so no torch**.

Runtime stage sets `APP_ENV=production`, `EMBEDDING_PROVIDER=google`, `HOST=0.0.0.0`, `PORT=7860`, `HF_HOME=/tmp/hf`, `MPLCONFIGDIR=/tmp/mpl` (`Dockerfile:19-28`). Installs unixODBC and Microsoft `msodbcsql18` from packages.microsoft.com (`Dockerfile:31-41`). Non-root `appuser` uid 1000 (`Dockerfile:43`, `:53`). HEALTHCHECK curls `/health` on 7860 (`Dockerfile:56-57`). `CMD uvicorn app.main:app --host 0.0.0.0 --port 7860`.

**A likely packaging defect.** `COPY` brings in `app`, `db`, `database_schemas`, `run.py` (`Dockerfile:47-50`). `db/migrations/` ships because it lives inside `db/` — but **`alembic.ini` does not**. `db/migrate.py:19` resolves `ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"`, so inside the image `upgrade_to_head` will fail and `create_metadata_tables` will silently fall back to `create_all` (`db/database_manager.py:102-111`). The application will start and appear healthy while never having run a migration. **Not verified by running the image** — this is READ-only inference from `Dockerfile:47-50` plus `db/migrate.py:19`, and no build was completed (§2.2).

`MPLCONFIGDIR=/tmp/mpl` (`Dockerfile:28`) and `mkdir -p /tmp/mpl` (`Dockerfile:52`) are vestigial after matplotlib's removal (§19).

### 15.2 compose.yaml and render.yaml

`compose.yaml` (38 lines) — `db`: `pgvector/pgvector:pg16`, user/password/db all `dbwhisper` (`compose.yaml:6-10`), 5432 published, `pg_isready` healthcheck. `app`: builds `.`, `env_file: .env`, overrides `POSTGRES_CONNECTION_STRING=postgresql+psycopg://dbwhisper:dbwhisper@db:5432/dbwhisper`, `APP_ENV=development`, maps host 8000 → container 7860, `depends_on: db service_healthy`. **No worker service, no observability profile, no sample target databases.**

`render.yaml` (33 lines) — Render Blueprint, `runtime: docker`, `plan: free`, `healthCheckPath: /health`, `dockerCommand` overriding the port to honour Render's `$PORT` (`render.yaml:10`). Sets `APP_ENV=production`, `EMBEDDING_PROVIDER=google`, `LOG_LEVEL=INFO`; marks `API_AUTH_TOKENS`, `POSTGRES_CONNECTION_STRING`, `GEMINI_API_KEY`, `GROQ_API_KEY`, `CORS_ALLOW_ORIGINS`, `SENTRY_DSN` as `sync: false`. The comment at `render.yaml:18-20` correctly states that `/schemas/*` fail closed in production while `/query` stays open unless `GATE_QUERY_ENDPOINT=true`.

### 15.3 Migrations

`alembic.ini` (42 lines) sets `script_location = db/migrations`, `file_template = %%(rev)s_%%(slug)s`, and a placeholder `sqlalchemy.url = sqlite:///./dbwhisper-local.db` with a comment that the real URL is injected at runtime (`alembic.ini:1-8`). `db/migrate.py:22-34` overrides `script_location` and `sqlalchemy.url` and sets `cfg.attributes["url_is_explicit"] = True`. `db/migrations/env.py:29-31` resolves the URL from `DBW_ALEMBIC_URL` → `PROJECT_DB_CONNECTION_STRING` → `POSTGRES_CONNECTION_STRING`. One revision: `0001_baseline.py` (130 lines).

`create_metadata_tables` calls `upgrade_to_head` and **falls back to `create_all` plus an `ALTER TABLE "Database_config" ADD COLUMN IF NOT EXISTS owner_id INTEGER` repair if Alembic raises** (`db/database_manager.py:89-117`, `:73-86`). The intent is defensible — a broken migration environment should not take the API down — but the cost is that a silently failing migration then looks healthy, which is exactly the failure mode §15.1 predicts inside the container.

### 15.4 Entrypoints disagree

`run.py` (12 lines) does `load_dotenv()` then `uvicorn.run("app.main:app", host=HOST or 127.0.0.1, port=PORT or 8000)`. `app/main.py`'s own `__main__` block hardcodes `host="127.0.0.1", port=8000` and ignores `HOST`/`PORT`. Two entrypoints, different behaviour.

---

## 16. Configuration

`Settings` (`app/core/config.py:21-164`) uses `case_sensitive=False`, `env_file=".env"`, `extra="ignore"` and declares **44 env names**. `.env.example` documents **40 unique keys** (RUN, 15:15).

### 16.1 Full variable table

Legend — **Used in code:** `Settings` = read through the settings object; `getenv` = read directly via `os.getenv`/`os.environ`; **no** = declared but never read.

| Variable | Used in code | In `.env.example`? | Notes |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | getenv `chain.py:407` | yes | |
| `API_AUTH_TOKENS` | Settings | yes | |
| `APP_ENV` | Settings; getenv `logger.py:50` | yes | |
| `APP_MODE` | Settings | yes | |
| `AUTH_REQUIRED` | Settings | yes | |
| `CORS_ALLOW_ORIGINS` | Settings | yes | |
| `DBW_SECRET_KEYS` | Settings | yes | **feeds an unwired module** (§10.6) |
| `DEEPSEEK_API_KEY` | getenv `chain.py:396` | yes | |
| `EGRESS_POLICY` | Settings | yes | |
| `EMBEDDING_PROFILE` | `embeddings.py:69` | yes | |
| `EMBEDDING_PROVIDER` | `embeddings.py:72` | yes | deprecated alias, warns (`embeddings.py:75-81`) |
| `GATE_QUERY_ENDPOINT` | Settings | yes | |
| `GEMINI_API_KEY` | Settings; getenv `config.py:174` | yes | |
| `GOOGLE_API_KEY` | Settings; getenv `config.py:173,176` | **NO** | |
| `GROQ_API_KEY` | getenv `chain.py:402` | yes | |
| `HOST` | Settings | yes | ignored by `main.py` `__main__` (§15.4) |
| `LOG_JSON` | Settings; getenv `logger.py:47` | yes | |
| `LOG_LEVEL` | Settings; getenv `logger.py:43` | yes | |
| `METRICS_ENABLED` | **no** | yes | nothing imports prometheus-client |
| `MODEL_PROFILE` | Settings | yes | |
| `NETWORK_ALLOWLIST` | Settings | yes | |
| `OLLAMA_BASE_URL` | Settings | yes | |
| `OPENAI_API_KEY` | getenv `chain.py:385` | yes | |
| `OPENROUTER_API_KEY` | getenv `chain.py:390` | yes | |
| `OTEL_ENABLED` | **no** | yes | `config.py:52` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | **no** | yes | `config.py:53` |
| `OTEL_SERVICE_NAME` | **no** | yes | `config.py:54` |
| `PORT` | Settings | yes | ignored by `main.py` `__main__` |
| `POSTGRES_CONNECTION_STRING` | Settings; getenv `retriever.py:45`, `orchestrator.py:54`, `db/langchain_memory.py:20`, `db/migrations/env.py:31` | yes | |
| `QUERY_CACHE_ENABLED` | **no** | yes | `config.py:101` — no query-result cache exists |
| `QUERY_CACHE_TTL` | **no** | yes | `config.py:102` |
| `QUERY_RATE_LIMIT_BURST` | Settings | yes | |
| `QUERY_RATE_LIMIT_PER_SEC` | Settings | yes | |
| `RATE_LIMIT_BURST` | Settings | yes | |
| `RATE_LIMIT_ENABLED` | Settings | yes | |
| `RATE_LIMIT_PER_SEC` | Settings | yes | |
| `SENTRY_DSN` | Settings | yes | the only wired observability |
| `SESSION_COOKIE_SECURE` | Settings `config.py:137-142` | **NO** | |
| `SESSION_TTL_SECONDS` | Settings `config.py:85` | **NO** | |
| `TRUST_PROXY_HEADERS` | **no** — `config.py:49` is its only occurrence | yes | see §17.7 |
| `UPSTASH_REDIS_REST_TOKEN` | **no** | yes | `config.py:106` |
| `UPSTASH_REDIS_REST_URL` | **no** | yes | `config.py:105` |
| `USER_AUTH_ENABLED` | Settings `config.py:84` | **NO** | **`README.md:93` tells users to set it** |
| `WORKER_POLL_INTERVAL_SECONDS` | **no** | **NO** | `config.py:58` — there is no worker |
| `EMBEDDING_MODEL_NAME` | getenv `app/embeddings/service.py:71,74` | yes (commented, `.env.example:29`) | **not a `Settings` field** |
| `DBW_ALEMBIC_URL` | getenv `db/migrations/env.py:29` | **NO** | not in `Settings` either |
| `LOG_SANITIZE` | getenv `logger.py:95` | **NO** | not in `Settings`; `=0` disables log sanitization |
| `PROJECT_DB_CONNECTION_STRING` | getenv `db/database_manager.py:18`, `db/migrations/env.py:30` | **NO** | not in `Settings`; **preferred over `POSTGRES_CONNECTION_STRING`** |
| `TOOL_CACHE_MAX_ITEMS` | getenv `tool_cache.py:18` | **NO** | not in `Settings` |
| `TOOL_CACHE_TTL_SECONDS` | getenv `tool_cache.py:17`, `tools.py:131` | **NO** | not in `Settings` |
| `TOOL_RATE_LIMIT_BURST` | getenv `tool_cache.py:110` | **NO** | not in `Settings` |
| `TOOL_RATE_LIMIT_PER_SEC` | getenv `tool_cache.py:112` | **NO** | not in `Settings` |

### 16.2 The three gaps that matter

1. **In `Settings`, missing from `.env.example` (5):** `GOOGLE_API_KEY`, `SESSION_COOKIE_SECURE`, `SESSION_TTL_SECONDS`, `USER_AUTH_ENABLED`, `WORKER_POLL_INTERVAL_SECONDS`. `USER_AUTH_ENABLED` is the notable one — `README.md:93` instructs users to set it and the template does not list it.
2. **Read via direct `getenv`, absent from both `Settings` and `.env.example` (7):** `DBW_ALEMBIC_URL`, `LOG_SANITIZE`, `PROJECT_DB_CONNECTION_STRING`, `TOOL_CACHE_MAX_ITEMS`, `TOOL_CACHE_TTL_SECONDS`, `TOOL_RATE_LIMIT_BURST`, `TOOL_RATE_LIMIT_PER_SEC`. **`PROJECT_DB_CONNECTION_STRING` is the significant omission** — `db/database_manager.py:18` prefers it over `POSTGRES_CONNECTION_STRING`, and it is documented nowhere.
3. **Declared but consumed nowhere (10):** `TRUST_PROXY_HEADERS`, `OTEL_ENABLED`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_SERVICE_NAME`, `METRICS_ENABLED`, `WORKER_POLL_INTERVAL_SECONDS`, `QUERY_CACHE_ENABLED`, `QUERY_CACHE_TTL`, `UPSTASH_REDIS_REST_URL`, `UPSTASH_REDIS_REST_TOKEN`. The opentelemetry and prometheus-client packages are installed (`pyproject.toml:46-50`) and nothing imports them.

Also retired: `ALLOW_MSSQL_AUTO_ORDER_BY` existed in the old executor (`git show HEAD:app/core/query_executor.py:86`) and is now referenced nowhere (RUN grep across the repo → zero hits).

**`.env` key names** (RUN `sed -E "s/=.*/=<redacted>/" .env`; no values were read or printed): `GROQ_API_KEY`, `GEMINI_API_KEY`, `GEMINI_API_KEY_1`, `POSTGRES_CONNECTION_STRING`, `EMBEDDING_PROVIDER`, `HOST`, `PORT`. `GEMINI_API_KEY_1` is read by nothing.

---

## 17. Security controls and their limits

### 17.1 Read-only enforcement, two layers

(a) The SQLGlot AST policy engine gates every statement — `app/execution/service.py:177` re-evaluates policy on every call, even for a caller holding a decision. (b) The database itself is asked to refuse writes via `read_only_setup` per dialect (`connections.py:140-179`) inside an always-rollback transaction (`connections.py:212-217`).

**Limits:** MSSQL reports `enforced=False` (`connections.py:161-167`); MariaDB's timeout hint is skipped when unsupported (`connections.py:158-159`, `:206-209`); an unknown dialect gets `ReadOnlySetup(False, (), …)` (`connections.py:174-179`). The engine's own docstring is appropriately modest: "a structural filter, not a proof" (`engine.py:15-17`).

### 17.2 Refusal to enroll writable connections

`is_read_only_connection` at `main.py:941` → HTTP 400 at `main.py:951-963`. **Limit: fail-open on probe error** (`main.py:946-950`), detailed in §11.5.

A second, richer tri-state checker exists — `app/execution/readonly.py`, `verify_read_only`, five states `VERIFIED_READ_ONLY` / `APPEARS_READ_ONLY` / `WRITABLE` / `UNSUPPORTED` / `CONNECTION_FAILED` (`readonly.py:36-45`) — but it is **not wired to any endpoint** (RUN grep: importers are `app/execution/__init__.py:3` and `tests/test_execution_service.py` only). Both checkers inspect privilege metadata and never attempt a write (`readonly.py:1-14`), which is the correct design.

### 17.3 Least-privilege guidance

`SECURITY.md:18-20`, `:31`; `render.yaml` comments; and `eval/make_pg.py:1-6` walks the walk with a SELECT-only role. **Limit:** it is guidance only. Nothing verifies or re-verifies the grant after enrollment, and `Database_config` has no field to record a verification result — `db/model.py:50-52` says so explicitly.

### 17.4 SSRF / network policy

`app/platform/network_policy.py` (307 lines): three levels — `BUNDLED_ONLY` / `PRIVATE_ALLOWED` / `PUBLIC_STRICT` (`modes.py:56-59`). Metadata endpoints are refused in **every** mode including self-hosted (`network_policy.py:30-49`, covering AWS/GCP/Azure/DO `169.254.169.254`, ECS `169.254.170.2`, Alibaba, Oracle, IMDSv6). Non-database ports are blocked (22/23/25/53/111/135/139/445/465/587/2049, `network_policy.py:53`). Every address in the DNS answer is checked and returned so callers can pin (`network_policy.py:16-18`). Enforced at `service.py:198-209`.

**Limit:** the resolved addresses are returned but the executor does **not** pin them — it hands the original connection string to SQLAlchemy (`service.py:229-231`), leaving a DNS-rebinding window between check and connect. The building block for the fix is already returned by the checker; only the use of it is missing.

### 17.5 Log and error sanitization

`sanitize_for_log` (`logger.py:199-231`) masks `scheme://user:pass@`, `password=`/`pwd=`/`uid=`/`user=` (`logger.py:170-173`), replaces quoted string literals and 3+-digit numbers in SQL (`logger.py:189-192`), masks `api_key=`/`token=`/`secret=` and `Bearer …` (`logger.py:216-217`), and truncates. It is applied at every log site in `main.py` and `tools.py`.

**Limits:** it is best-effort regex and the module says so (`logger.py:158-160`); `LOG_SANITIZE=0` disables the filter entirely (`logger.py:95`); and `_mask_password_in_connection_string` contains a no-op `val.lower()` whose result is discarded (`logger.py:164`).

On the way out, `sanitize_db_error` strips DSNs and credential-shaped key/value pairs from driver messages, truncates to 300 chars, maps to 8 categories (`service.py:100-152`) and rewrites read-only refusals (`service.py:147-152`). **Limit:** `/query` and `/schemas/enroll` still emit raw exception text on some paths that never reach the sanitizer — `detail=f"Internal server error: {e!s}"` (`main.py:864-867`) and `detail=f"Schema pipeline failed: {error}"` (`main.py:1105-1108`).

### 17.6 Rate limiting

Per-(IP, path) token bucket (`app/security/ratelimit.py`), tighter on `/query` (`query_rate_limit_burst=8`, `per_sec=0.1` versus `30`/`0.5`, `config.py:90-93`), returning 429 as `application/problem+json` with `Retry-After` (`ratelimit.py:70-80`); probes are exempt (`ratelimit.py:23-29`).

**Limits, four of them:** it is in-memory and process-local, so it is useless across replicas (the docstring says so, `ratelimit.py:3-5`); `X-Forwarded-For` is trusted unconditionally (§17.7); it is keyed by *path*, so a burst spread across paths multiplies the allowance; and buckets are never evicted — `_rate_limiters` grows without bound (`tool_cache.py:100-116`), an unbounded-memory vector.

### 17.7 The `TRUST_PROXY_HEADERS` gap

`trust_proxy_headers: bool = False` at `app/core/config.py:49` carries the comment "honour X-Forwarded-For only behind a trusted proxy". RUN `grep -rn "trust_proxy|TRUST_PROXY" app db` → **`config.py:49` is the only occurrence in the codebase.**

Meanwhile `app/security/ratelimit.py:39-45` trusts `X-Forwarded-For` / `X-Real-IP` **unconditionally**, regardless of that flag. Per-IP rate limiting is therefore trivially evadable by header spoofing whenever the app is not behind a normalising proxy — and the setting that was supposed to prevent exactly this is inert.

### 17.8 Remaining controls

- **API key gate** — §10.1. Constant-time compare, fail-closed in production; single shared static token, no rotation, no scopes, no audit.
- **CORS** — env-driven, credentials only with an explicit allowlist, wildcard-in-production warning (`main.py:159-167`). **Limit:** it is only a warning; a production deploy with `CORS_ALLOW_ORIGINS=*` still starts.
- **Auth/tenancy** — §10. Off by default; the `/schemas/enroll` ownership gap; no CSRF defence beyond `SameSite=Lax`; sessions never rotated on privilege change.
- **Prompt-injection posture** — the v1.1 prompt places untrusted database descriptions inside `<database_description>` delimiters and instructs the model to treat them as data (`prompt.py:11-12`, `:28-31`; rule 10 at `:62-63`). **Limit:** delimiters plus an instruction are a mitigation, not a control. The real backstop is the policy engine, and that is the honest thing to say.
- **Secrets at rest** — §10.6. Implemented, tested, **not wired**.
- **Supply chain** — gitleaks on full history (blocks); Dependabot config present (`.github/dependabot.yml`); `pip-audit` and `npm audit` informational only. GPLv2 `mysql-connector-python` was removed in Phase 1 (§20).

---

## 18. Documentation inconsistencies

Each verified against code.

1. **README claims a LangGraph agent; the shipped code is a `create_agent` tool loop.** `README.md:8` ("powered by a **LangGraph** agent"), `:17` (badge `LangGraph-agent`), `:34` ("a **LangGraph** pipeline"), `:52` (mermaid subgraph titled `LangGraph agent`), `:64` (`agent/  LangGraph agent`), `:81` (tech stack). Code: `from langchain.agents import create_agent` (`chain.py:13`), `create_agent(...)` (`chain.py:308`). LangGraph is present only as a checkpoint/store backend (`chain.py:29-30`; `db/langchain_memory.py:9-10`), and those checkpoints are write-only (§6.4). **Nuance:** a genuine `StateGraph` now exists at `app/graph/query_graph.py:25,82-84`, created at 15:12 during the audit — but nothing imports it (§3), so the README is still describing something the running system does not do.
2. **README lists a join-path tool that does not exist.** `README.md:64` — "tools (search, validate, join-path)". The five real tools are at `chain.py:296-302`; there is no join-path tool and no join graph (RUN grep for `join_path`/`JoinPath` outside `app/models.py` → zero external references).
3. **SECURITY.md describes the retired validator.** `SECURITY.md:13-17` says every statement "must start with `SELECT`/`WITH`, may not contain DML/DDL keywords (…), may not be multiple statements, may not use `SELECT … INTO`, and may not touch system schemas (`information_schema`, `sys.`, `pg_catalog.`)" — a verbatim description of the pre-v2 implementation (`git show HEAD:app/core/sql_validator.py:10-28`, `:111-182`). The file is now a 47-line delegator (`sql_validator.py:1-47`) and the three-substring system-schema check has been replaced by a catalog allowlist covering 26 corpus cases.
4. **SECURITY.md says the read-only checker "warns"; the code rejects with HTTP 400.** `SECURITY.md:18-20`: "runs a best-effort, non-destructive probe and **warns** when a connection appears writable." Code: `main.py:951-963` logs a warning **and then** raises `HTTPException(status_code=400, …)`. The code comment at `main.py:939` even says "reject writable connections (fail closed)". Both citations verified; the doc understates the behaviour. (The genuine weakness is elsewhere — the fail-open-on-probe-error path, §11.5 — which the doc does not mention at all.)
5. **ENHANCEMENT_PLAN.md still says nothing is built.** `ENHANCEMENT_PLAN.md:7`: "> **Status: PLAN ONLY — nothing here is built yet.**" Its Phases 1–9 (lines 43, 60, 72, 83, 90, 96, 106, 116, 121) cover local stabilization, ruff/mypy/pre-commit, pytest, GitHub, CI, Docker, HF+Neon deploy, the Next.js frontend, and hardening — every one of which demonstrably shipped (`.pre-commit-config.yaml`, `.github/workflows/ci.yml`, `tests/` with 1022 tests, `Dockerfile`, `render.yaml`, `web/`, `app/security/`). It should be marked superseded by `docs/v2/` or deleted.
6. **`web/README.md` documents an env var the code does not read.** `web/README.md:23`, `:27` (table) and `:58` (Vercel instructions) all name `NEXT_PUBLIC_API_BASE_URL`. The code reads `NEXT_PUBLIC_API_BASE` and defaults to the `/api` proxy: `process.env.NEXT_PUBLIC_API_BASE?.replace(/\/+$/, "") || "/api"` (`web/src/lib/api.ts:11`, with the comment at `api.ts:7`). The proxy target is `API_PROXY_TARGET` (`web/.env.example:4`, default `http://localhost:8000`), and `NEXT_PUBLIC_API_BASE` is the commented line at `web/.env.example:8`. **Following `web/README.md` verbatim produces a frontend that ignores the setting entirely.**
7. **README's module map is out of date.** `README.md:62-70` lists only `main.py`, `agent/`, `core/`, `schema_pipeline/`, `security/`, `db/`, `web/`. Missing: `app/sqlpolicy/`, `app/execution/`, `app/platform/`, `app/evaluation/`, `app/api/`, `app/utils/`, `db/migrations/`. The endpoint line (`README.md:63`) names only `/query`, `/schemas/enroll`, `/health`, `/ready` — **10 of the 14 routes are undocumented** (§4). The same is true of `main.py`'s own module docstring (`main.py:3-8`) and the `/` landing payload (`main.py:1181+`), which advertise four endpoints.
8. **README:43 "`describe()` stats".** `app/core/result_formatter.py:5-7` now explicitly states the numbers come from `app.execution.results` rather than pandas `describe()`, with the reason given ("`describe()` will happily report the mean of a primary key"). The README sentence is wrong in a way that *undersells* the fix.
9. **README:39 "query timeout + row cap".** True today (`connections.py:140-179` plus `service.py:235-242`) but **not** true before v2, where `execution_options(timeout=…)` enforced nothing (§9.1). This is a claim that was inaccurate at the time it was written and became true later.
10. **`RESULT_SUMMARY_PROMPT` still says "pandas describe"** (`prompt.py:93`) even though the statistics are now computed deterministically by `app/execution/results.py` — the same drift as item 8, inside the prompt itself.
11. **Three referenced documents do not exist.** `docs/v2/TARGET_ARCHITECTURE.md:4` links `CURRENT_STATE_AUDIT.md`; `docs/v2/IMPLEMENTATION_ROADMAP.md:23`, `:169` list it as a Phase-0 deliverable; `app/agent/prompt.py:7` cites it. Also referenced but absent: `CLAIM_AUDIT.md` and `DEPENDENCY_AND_LICENSES.md` (`IMPLEMENTATION_ROADMAP.md:24`, `:41`; `TARGET_ARCHITECTURE.md:5-6`). **This document closes the first three references; the other two remain dangling.**

---

## 19. Dead, duplicated or unused code

### 19.1 Confirmed cleaned up

- `dialect_read_only_check` **is gone** from `app/user_db_config_loader.py` — present at `git show HEAD:app/user_db_config_loader.py:128`, absent from the current 83-line file (RUN grep). The current file is purely `get_user_database_settings` plus path helpers.
- `app/schema_pipeline/user_database_manager.py`, `app/utils/token_tracker.py` and `tests/test_token_tracker.py` all show as `D` in `git status` (RUN, 15:14).
- `app/schema_pipeline/introspector.py` is 362 lines and begins with real code at line 1 — the 335-line commented-out block is gone.

### 19.2 Still present

| Item | Evidence | Assessment |
|---|---|---|
| `dialect_read_only_check` in `app/security/db_readonly_checker.py:126` | RUN grep — the definition is the **only** hit repo-wide | The duplication was resolved by deleting the *copy*, leaving the original orphaned |
| `app/core/query_executor.py` (74 lines) | RUN grep across app/db/tests/eval/scripts → zero importers; coverage `18 stmts, 18 missed, 0%` | A compatibility shim nothing is compatible with any more |
| **Two read-only checkers** | `app/security/db_readonly_checker.py` (162 lines, boolean, wired to `/schemas/enroll`, **8 %** covered) vs `app/execution/readonly.py` (334 lines, tri-state, well tested, **not wired**) | A **new** duplication introduced by v2, not a leftover. The better one is unused and the worse one is load-bearing |
| `app/static/` chat UI | `chat.html` 3.2 KB, `chat.js` 11.9 KB, `chat.css` 8.4 KB, all Nov 2025; mounted `main.py:134`, served `main.py:137`; `chat.js:169` does `fetch('/query', …)` | Docstring calls it "a development convenience and not a production feature", yet it ships in the image (`Dockerfile:47`) and is served **unauthenticated in production**. `.gitignore:38` ignores `app/static/*.backup`, implying hand-editing in place |
| **12 unused Pydantic models** in `app/models.py` | RUN reference count outside `app/models.py` across app/db/tests/eval/scripts: `BusinessQuerySpec` 0, `JoinPath` 0, `TableMatch` 0, `MetricSpec` 0, `DimensionSpec` 0, `FilterSpec` 0, `TimeRange` 0, `JoinStep` 0, `ApplicationConfig` 0, `ColumnInfo` 0, `ForeignKeyInfo` 0, `TableDetail` 0 | Roughly `app/models.py:32-155` — the fossil of a planned semantic layer. (`ColumnDocumentation` 3 refs and `TableDocumentation` 4 refs are live) |
| `eval/_groq_ok.py` (20 lines) | zero importers; loads every `.env` key into `os.environ` (`:7-11`) | Single-use poller from a July debugging session |
| No-op statement | `val.lower()` result discarded at `app/utils/logger.py:164` | Inside a password-masking helper — worth a second look, not just deletion |
| Vestigial matplotlib traces | `MPLCONFIGDIR=/tmp/mpl` (`Dockerfile:28`), `mkdir -p /tmp/mpl` (`Dockerfile:52`), `"matplotlib"` in the noisy-logger list (`app/utils/logger.py:141`) | matplotlib was removed in Phase 1 |
| `sqlparse` still a real dependency | `pyproject.toml:16` | Correct — `app/sqlpolicy/legacy_heuristics.py` uses it |
| Historical unreachable code | `git show HEAD:app/core/query_executor.py:261-265` — a second `return` after a `return` | Moot now that the file is dead |
| Untracked working files | `Temp/probe_*.py` (10 ad-hoc probe scripts), `Log/app_*.log` (ignored, `.gitignore:25-27`), `eval/server.log` (64 KB, ignored by `*.log`) | Worth ignoring or removing |

### 19.3 A `.gitignore` bug

`.gitignore:47` is `*.sqlite` with a negation `!evaluation/data/**/*.sqlite` at `:48`. **The negation names `evaluation/` while the actual dataset directory is `app/evaluation/`**, so the negation currently matches nothing. Lines 40-41 carry an explicit comment that YAML must stay tracked, which is why the corpus survives.

---

## 20. What the v2 program has already changed on this branch

Verified against `git status` (RUN, 15:14) and the source. Nothing in this section is committed — it is all working-tree state on `feat/dbwhisper-v2` at `e5c89f3`.

### 20.1 New packages

| Package | Lines | Status |
|---|---|---|
| `app/sqlpolicy/` | 1893 | **Live** via the `app/core/sql_validator.py` shim. AST engine + legacy heuristic second layer (§8.3) |
| `app/execution/` | 1201 | **Live.** Single execution path with real read-only transactions, `+1` truncation detection, AST pagination, error classification (§9.2) |
| `app/platform/` | 653 | Partly live — `modes.py`, `network_policy.py` (live), `secrets.py` (**not wired**, §10.6), `settings.py` (0 % covered) |
| `app/evaluation/` | 294 (YAML) | The 255-case adversarial corpus (§8.4) |
| `db/migrations/` + `db/migrate.py` + `alembic.ini` | 49 + 130 + 42 | Alembic baseline `0001` (§15.3). `alembic.ini` is untracked and **not copied into the image** (§15.1) |
| `app/embeddings/`, `app/llm/`, `app/retrieval/`, `app/analysis/`, `app/graph/` | 425 + 1763 + 1288 + 630 + 1442 | Created **during this audit**. **None is imported by the live path** (RUN, §3) |

New tests: `tests/sqlpolicy/`, `tests/test_modes.py`, `tests/test_secrets.py`, `tests/test_migrations.py`, `tests/test_execution_service.py`, `tests/test_network_policy.py`, `tests/test_result_stats.py`, `tests/test_api_query.py`, plus in-flight `tests/llm/`, `tests/retrieval/`, `tests/graph/`.

### 20.2 Deletions

`app/schema_pipeline/user_database_manager.py` (imported a non-existent module `db.connection`), `app/utils/token_tracker.py` and `tests/test_token_tracker.py` (no importers), and 335 lines of commented-out legacy MSSQL extractor at the top of `app/schema_pipeline/introspector.py` — all confirmed in §19.1.

### 20.3 Dependency changes

**Removed:** matplotlib, psycopg2-binary, mysql-connector-python (GPLv2), langchain-community, groq.
**Added (base):** `sqlglot>=27` (`pyproject.toml:42`), `alembic>=1.14` (`:43`), `cryptography>=44` (`:44`), `httpx>=0.27` (`:45`), `prometheus-client>=0.21` (`:46`), four `opentelemetry-*` (`:47-50`), `fastembed>=0.5` (`:51`), `langchain-text-splitters>=0.3` (`:52`).
**Added (dev):** `hypothesis>=6.120`, `import-linter>=2.1`, `pytest-xdist>=3.6`, alongside existing `vulture` and `pip-audit` (`:70-85`).

An optional `local-embeddings` extra isolates torch/transformers/sentence-transformers (`:60-68`) to keep the image lean — a good call, and the Dockerfile honours it by not installing the extra (`Dockerfile:14-15`).

Note that prometheus-client and the four opentelemetry packages are installed but **nothing imports them** (§16.2), so five dependencies currently buy nothing.

### 20.4 The prompt rewrite

`app/agent/prompt.py` was rewritten. `PROMPT_VERSIONS = {sql_agent_prompt: "1.1", result_summary_prompt: "1.1"}` (`prompt.py:19-22`).

The retired `@1.0` is at `git show HEAD:app/agent/prompt.py:81-157`: "You are an SQL Server Agent for **AvasMed** (a Durable Medical Equipment – DME – management system)", with a hardcoded table inventory (ProductMaster, InventoryProduct, Dispense, ClientInvoice, UserMaster, CompanyMaster, Patient, ShiprushFile, HCPCS_CODE_MAST, …) grouped under "DATABASE KNOWLEDGE (use this to map user intent → tables)", a hard T-SQL directive, and a worked AvasMed example. **It was the system prompt for every enrolled database**, regardless of dialect or domain. The file also carried a near-identical 78-line commented-out copy above it (`HEAD` lines 3-79).

The new `@1.1` is database-neutral and takes the enrolled description as delimited untrusted data (`prompt.py:11-12`, `:28-31`, `:62-63`). This is the single largest correctness improvement in the branch: the old prompt told the model facts about a database it was not looking at.

### 20.5 Net assessment

The v2 work has substantially strengthened the two controls that matter most — SQL policy (§8) and query execution (§9) — and both are now the best-tested code in the repository (§14.3). Three things stand between the current state and that work being fully realised:

1. **`app/platform/secrets.py` is not wired** (§10.6). Credentials are still plaintext at rest, while the mode policy claims otherwise.
2. **The better read-only checker is not wired** (§17.2), and the weaker one it was meant to replace still guards enrollment at 8 % coverage.
3. **Confirmed defect at `main.py:1089`** (§10.5): `schema_extracted` is never set on the full enrollment path, so every re-enroll re-runs extraction, per-table LLM documentation and embedding.

Alongside those, the two documentation classes worth closing first are the LangGraph claim (§18.1) — which will become *true* if `app/graph/` is wired, and should not be corrected until that is decided — and the `web/README.md` env var (§18.6), which actively breaks setup for anyone following it.
