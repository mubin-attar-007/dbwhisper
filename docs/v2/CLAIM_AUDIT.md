# CLAIM_AUDIT.md

**Status:** living document. Governing rule, from `docs/v2/IMPLEMENTATION_ROADMAP.md:12` — *"Every public claim changed or added is recorded in `CLAIM_AUDIT.md`."*

**Audit performed:** 2026-08-24, 09:00–09:35 UTC, on branch `feat/dbwhisper-v2`, repo `d:/Software/__stashed/AI_PROJECTS/dbwhisper`, Windows 10, all commands via `uv run` from the repo root.

---

## 1. Why this document exists, and the rules it enforces

### 1.1 Purpose

DBWhisper is a substantial piece of engineering. It has an AST-based SQL policy engine with a 255-case adversarial corpus, a single audited execution path, database-enforced read-only transactions on three of four supported engines, Alembic migrations, encrypted secrets at rest, a typed application-mode policy object, and a real CI pipeline. **None of that needs to be oversold, and this document exists because parts of it currently are.**

The public surface (README, marketing page, SECURITY.md, `.env.example`, `web/README.md`) contains a mixture of three things:

1. claims that are true and demonstrable from a clean checkout;
2. claims that were true when written and have since drifted out of date;
3. claims that were never demonstrable — most importantly two headline metrics on the marketing page that cannot be reproduced, were produced by a prompt that has since been deleted, and in one case measure something other than what they say they measure.

The purpose of this audit is **not** to strip the project of its claims. It is to make every published sentence checkable by a reader with a clone of the repo and a terminal. Where a claim is defensible it is marked `PROVABLE_FROM_REPO` and **the wording is left alone**.

### 1.2 Classification vocabulary

| Class | Meaning |
|---|---|
| `PROVABLE_FROM_REPO` | A reader with a clean checkout can verify it by reading a named file or running a named command. Wording stands as-is. |
| `PARTIALLY_TRUE` | The underlying mechanism exists and works, but the wording overstates its scope, certainty, or coverage. Needs narrowing, not deletion. |
| `STALE` | Was accurate when written; the code moved and the text did not. Needs a factual refresh. |
| `UNPROVABLE_FROM_REPO` | May well be true, but nothing in the repository settles it — external/deployed state, or a metric whose harness or fixtures are not in the repo. Not the same as false. |
| `FALSE` | As written, contradicted by the code, or measures something other than what it says. Must be removed or replaced before the next publish. |

### 1.3 Wording rules — checklist (apply to every new or edited public sentence)

Copy this list into any PR that touches user-visible copy. Every box must be ticked.

- [ ] **No absolutes.** The words *always*, *never*, *cannot*, *guaranteed*, *any*, *all* do not appear in a safety or capability claim unless a named test enumerates the whole space.
- [ ] **No "proves" / "proven" / "provably"** for a structural filter. The policy engine *checks*, *rejects*, *admits*, *classifies*. It does not prove. (A parser-based filter is bounded by the parser's fidelity; that is not a proof.)
- [ ] **No banned marketing adjectives**: *production-ready*, *enterprise-grade*, *bulletproof*, *military-grade*, *bank-grade*, *hardened*, *unbreakable*.
- [ ] **No percentage without a numerator and a denominator** in the same sentence or its footnote. "100%" alone is banned outright; "343 of 343" is fine.
- [ ] **Every metric carries provenance**: dataset + version, n, model, prompt version, date, execution environment, exclusions. If it will not fit in the UI, the UI cites the section of this document that holds it.
- [ ] **Every metric is reproducible from a clean checkout**, or it is labelled as not reproducible and dated. A number whose harness is untracked is not publishable as a headline.
- [ ] **The claim names the mechanism**, not just the outcome. "A policy engine rejects writes before execution" beats "your data is safe".
- [ ] **Scope is stated where it varies by engine.** Read-only session enforcement differs between PostgreSQL/MySQL/SQLite and SQL Server; say so rather than averaging.
- [ ] **No claim about deployed/live state** in a document that ships in the repo, unless it is explicitly framed as "the hosted demo at X" and can be broken without making the repo wrong.
- [ ] **Capability claims match the shipped UI.** If the product cannot do it from the interface a visitor sees, do not describe it as something "you" do.

### 1.4 How claims were verified — RAN vs READ

This distinction is load-bearing and is carried into every Evidence cell below.

**RAN** (executed this session; exact output recorded):

| Command | Result |
|---|---|
| `uv run pytest -p no:cacheprovider --color=no` | `991 passed in 42.50s` (09:31 UTC) |
| `uv run pytest -p no:cacheprovider --color=no tests/sqlpolicy` | `616 passed in 11.28s` (09:31 UTC) |
| `uv run pytest --cov=app --cov=db` + `uv run coverage report` | `TOTAL 7080 stmts, 2369 miss, 67%` (09:33 UTC) |
| `python -c "from app.main import app; app.routes"` | 22 routes = 17 application endpoints + 4 FastAPI built-ins + 1 static mount |
| `python -c "from app.sqlpolicy import engine; engine.POLICY_VERSION"` | `sql_policy@2.0.0`; `sqlglot 30.17.0` |
| `yaml.safe_load(app/evaluation/datasets/adversarial/sql_policy_cases.yaml)` | 255 cases → 577 case×dialect expansions; deny 176→343, allow 73→210, needs_approval 6→24; 0 `skip_reason` |
| `app.core.sql_validator.validate_sql(...)` on 16 probe statements | see §4.2 |
| `ChatGoogleGenerativeAI(model="gemini-2.5-flash")` with no Google key | `RAISED: DefaultCredentialsError` |
| TCP probe 127.0.0.1:8010 / :55432 / :8000 | 8010 closed, 55432 open, 8000 closed |
| `psycopg.connect` with the DSN at `eval/rescore.py:14` | `OperationalError … FATAL: password authentication failed for user "eval"` |
| `uv run python -m app.platform.secrets generate-key` | emits a key (output redacted) |
| `git ls-files eval \| wc -l` | `0` — the eval harness is untracked |

**READ ONLY** (inspected, not executed): everything about the deployed demo, Neon, the Hugging Face Space, Vercel, the live-demo badge, `docs/BACKUP.md` procedures, the July 2026 eval runs, and the `eval/rescore.py` failure hypothesis in §4.4. Baseline lint/type/build numbers (ruff clean, mypy 82 errors/20 files, frontend lint+typecheck+build passing, Docker not exercised) are carried over from the lead engineer's 2026-08-21 run and were **not** re-executed here.

### 1.5 Two caveats a reviewer must know before trusting any count in this file

1. **The working tree moved during the audit.** `app/llm/` and `tests/llm/` (a model registry, a router with a `CircuitBreaker`, and ollama/remote/fake providers) appeared mid-session and are untracked (`git status --short app/llm` → `?? app/llm/`). The full-suite count moved **845 → 926 → 991** across ~35 minutes, and coverage moved **52% → 67%**. `tests/sqlpolicy` was stable at **616** across every run. Any statement here about `app/llm` wiring is a 09:31 UTC snapshot; re-run `grep -rn "from app.llm" app/ db/` before publishing provider copy.
2. **Measurements were taken on a developer machine with `.env` present** (GROQ + GEMINI keys configured; no OpenAI/DeepSeek/Anthropic/OpenRouter key). A CI run without those keys can take different branches. Secrets were never printed; key names were listed only through a redacting `sed`.

---

## 2. Summary — claim counts by classification

93 distinct public claims were inventoried across 11 files. These counts are machine-verified against the tables in §3 (parsed 2026-08-24); if you edit a row, re-derive them rather than hand-adjusting.

| Classification | Count | Share |
|---|---:|---:|
| `PROVABLE_FROM_REPO` | 30 | 32.3% |
| `PARTIALLY_TRUE` | 27 | 29.0% |
| `UNPROVABLE_FROM_REPO` | 15 | 16.1% |
| `STALE` | 13 | 14.0% |
| `FALSE` | 8 | 8.6% |
| **Total** | **93** | **100%** |

By file:

| File | Claims | PROVABLE | PARTIALLY | STALE | UNPROVABLE | FALSE |
|---|---:|---:|---:|---:|---:|---:|
| `web/app/(marketing)/page.tsx` | 26 | 6 | 12 | 0 | 5 | 3 |
| `README.md` | 19 | 8 | 6 | 2 | 3 | 0 |
| `.env.example` | 16 | 9 | 4 | 0 | 2 | 1 |
| `web/README.md` | 9 | 1 | 1 | 4 | 0 | 3 |
| `SECURITY.md` | 6 | 4 | 0 | 2 | 0 | 0 |
| `app/main.py` + `app/models.py` + `HealthBadge.tsx` | 6 | 0 | 2 | 3 | 0 | 1 |
| `web/src/lib/site.ts` + `web/app/components/links.ts` + `pyproject.toml` | 5 | 2 | 0 | 0 | 3 | 0 |
| `ENHANCEMENT_PLAN.md` | 3 | 0 | 1 | 2 | 0 | 0 |
| `docs/BACKUP.md` | 3 | 0 | 1 | 0 | 2 | 0 |

**The eight `FALSE` claims, listed once for triage** — these block the next publish:

1. `page.tsx:83-85` — "100%" fail-closed card (measures API failure, not validator action).
2. `page.tsx:134-136` — "always answers" / "never goes dark".
3. `page.tsx:163` — product mock shows `dbwhisper.app/app`, a domain that appears nowhere else in the repo.
4. `.env.example:67-71` — the OpenTelemetry/metrics block is inert; there is no `/metrics` route.
5. `web/README.md:23` — `NEXT_PUBLIC_API_BASE_URL` does not exist in the code.
6. `web/README.md:25-27` — the same variable in a config table.
7. `web/README.md:58` — Vercel instruction to set that variable (setting it does nothing).
8. `app/main.py:111` — `version="1.0.0"` against `pyproject.toml:3` `version = "0.1.0"`.

---

## 3. Claim inventory by source file

Columns: **Claim** (verbatim) | **Location** | **Class** | **Evidence** | **Approved replacement wording**. Where the class is `PROVABLE_FROM_REPO`, the replacement column reads *— (wording stands)*.

### 3.1 `README.md`

| Claim (verbatim) | Location | Class | Evidence | Approved replacement wording |
|---|---|---|---|---|
| "Ask your database anything — plain English in, **validated SQL + answers** out." | `README.md:5` | PROVABLE_FROM_REPO | READ `app/models.py:308-325`: `QueryResponse` carries `sql`, `validation_passed`, `data`, `natural_summary`. | — (wording stands) |
| "turns questions into **safe, read-only SQL** across PostgreSQL / MySQL / SQL Server … and a hard read-only safety layer" | `README.md:7-9` | PARTIALLY_TRUE | Mechanism real (§3.3, §4.2). "hard" is a banned intensifier; "safe" is unbounded; SQL Server has no session-level read-only mode (READ `app/execution/connections.py:161-166`, `enforced=False`). | "A natural-language-to-SQL agent that turns questions into read-only SQL for PostgreSQL, MySQL and SQL Server — a LangGraph agent, schema-aware pgvector retrieval, provider fallback across six LLMs, and an AST-based read-only policy engine between generation and execution." |
| Live Demo badge, "online", → `dbwhisper.vercel.app` | `README.md:11` | UNPROVABLE_FROM_REPO | Static shields.io badge; the word "online" is baked into the image URL and does not reflect liveness. No network request was made. | Keep the link, drop the state word: label the badge `Live Demo` only, so the badge cannot be wrong. |
| API Docs badge → `heisenbergblue-dbwhisper.hf.space/docs` | `README.md:13` | UNPROVABLE_FROM_REPO | Matches `web/app/components/links.ts:10`. External liveness not checkable from the repo. | — (link is fine; no state word to fix) |
| "Python 3.13" | `README.md:15` | PROVABLE_FROM_REPO | READ `pyproject.toml:6` `requires-python = ">=3.13"`; `.github/workflows/ci.yml:25`. | — (wording stands) |
| "finds the right tables, writes SQL, **proves it's safe**, runs it, and explains the answer" | `README.md:31-32` | PARTIALLY_TRUE | "proves" overclaims a structural filter — see §4.2 note (ii). Everything else in the sentence is demonstrable. | "…finds the right tables, writes SQL, checks it against the read-only policy engine, runs it, and explains the answer — with follow-up suggestions." |
| "a **LangGraph** pipeline: schema retrieval → SQL generation → validation → execution → natural-language summary, with conversation memory" | `README.md:34-35` | PROVABLE_FROM_REPO | READ `pyproject.toml:25` (langgraph), `app/agent/chain.py`, `db/conversation_memory.py`; follow-ups at `app/main.py:737,807`. | — (wording stands) |
| "**embedded into pgvector**; each question runs a **semantic search** to load only the relevant tables" | `README.md:36-37` | PROVABLE_FROM_REPO | READ `app/core/retriever.py:109` `store.similarity_search(query, k=k, …)`; default k=4 `app/agent/tools.py:170`, k=3 `:299`. | — (wording stands) |
| "SELECT-only enforcement (blocks DML/DDL/EXEC, multi-statements, and comment injection), read-only DB users, query timeout + row cap" | `README.md:38-39` | PARTIALLY_TRUE | DML/DDL/EXEC/multi-statement/timeout/row-cap all confirmed by RAN probes (§4.2). **"comment injection" is a stale description**: RAN `SELECT * FROM customers -- comment` → `valid=True`. The engine parses to an AST and normalizes comments away rather than blocking them. | "🛡️ **Read-only policy layer** — a SQLGlot AST engine (`sql_policy@2.0.0`) admits a single read-only SELECT and rejects DML, DDL, EXEC, multi-statements, `SELECT … INTO`, system-catalog access and blocked functions; execution uses a least-privilege role inside a rolled-back transaction (read-only at the database on PostgreSQL, MySQL and SQLite), with a query timeout and a row cap." |
| "Gemini, Groq, OpenAI, DeepSeek, Anthropic, OpenRouter — automatic failover so one provider outage doesn't take the app down" | `README.md:40-41` | PARTIALLY_TRUE | Failover loop real (READ `app/main.py:629,640-699`). But the count live at runtime equals the number of keys configured (READ `app/agent/chain.py:64-74`), and the Gemini "free tier" fallback needs a Google key — RAN: construction raises `DefaultCredentialsError` without one. | "🔁 **Provider fallback** — OpenAI, OpenRouter, DeepSeek, Groq, Anthropic, Gemini, tried in priority order; a failed or rate-limited generation moves to the next provider that has credentials configured." |
| "**Multi-database** — PostgreSQL, MySQL, SQL Server (SQLAlchemy + ODBC)" | `README.md:42` | PARTIALLY_TRUE | Drivers present: READ `pyproject.toml:36` psycopg, `:38` pyodbc, `:39` pymysql. Reflection is dialect-agnostic: READ `app/schema_pipeline/introspector.py:62,76-83`. **But end-to-end tests run only against SQLite** (READ `tests/test_execution_service.py:13,68-80`), and `.github/workflows/ci.yml` has no `services:` block. | "🗄️ **Multi-database** — PostgreSQL, MySQL and SQL Server via SQLAlchemy reflection (psycopg / pymysql / pyodbc); SQLite is supported and is what the automated tests run against." |
| "**Rich results** — JSON / CSV / table, `describe()` stats, and an LLM summary" | `README.md:43` | PROVABLE_FROM_REPO | READ `app/models.py` `QueryResultData` (`csv`, `raw_json`, `describe_text`) + `natural_summary`. | — (wording stands) |
| "Split deploy: **Next.js** frontend (Vercel) → **FastAPI** agent (Hugging Face Docker Space) → **Neon Postgres + pgvector**." | `README.md:47` | UNPROVABLE_FROM_REPO | Deployment topology. `Dockerfile` and `render.yaml` exist; the hosting arrangement is not checkable from the repo, and Docker was not exercised this session. | Frame as intent: "Deployed as: Next.js frontend on Vercel → FastAPI agent in a Docker Space → Neon Postgres with pgvector." (Statement of the current deployment, not a repo guarantee.) |
| "`main.py` FastAPI app + endpoints (`/query`, `/schemas/enroll`, `/health`, `/ready`)" | `README.md:63` | STALE | RAN route enumeration: **17 application endpoints**, not 4. Missing: `/run_sql`, `/databases`, `/schemas/{db_flag}`, `/training/pairs*`, `/auth/*`, `/chat`, `/`. | "`main.py` FastAPI app + 17 endpoints (`/query`, `/run_sql`, `/schemas/*`, `/databases`, `/training/pairs`, `/auth/*`, `/health`, `/ready`)" |
| "`core/` query_executor · sql_validator (read-only) · retriever (pgvector) · formatter" | `README.md:65` | STALE | READ: `app/core/query_executor.py` is now a 74-line compatibility shim (`:1-7`); `app/core/sql_validator.py` is a 47-line wrapper (`:1-14`). The live path is `app/execution/service.py` (imported at `app/main.py:47`). The tree omits `app/sqlpolicy/`, `app/execution/`, `app/platform/`, `app/llm/`. | Add to the tree: `sqlpolicy/  AST read-only policy engine (+ legacy heuristic layer)`, `execution/  the single execution path: policy → network → read-only session → fetch`, `platform/  app modes, secrets at rest, network policy`; mark `core/query_executor.py` and `core/sql_validator.py` as compatibility shims. |
| "The core promise: **it can read your data, never change it.**" | `README.md:74-77` | PARTIALLY_TRUE | Strong mechanism (§3.3), absolute wording. "never" is banned; SQL Server relies on privileges rather than a read-only session. | "## 🔒 Safety model — The design goal is read access only. Every statement — generated or hand-edited — is parsed and checked by the policy engine before execution; connections should use a read-only database role; results are timeout- and row-capped and run inside a transaction that is rolled back. 343 of 343 adversarial deny cases and 210 of 210 benign allow cases behaved as expected under `sql_policy@2.0.0` (`uv run pytest tests/sqlpolicy` → 616 passed, 2026-08-24). This is a structural filter, not a proof — pair it with a least-privilege role. See SECURITY.md." |
| "SQLAlchemy 2.0 · pgvector · psycopg · pyodbc" | `README.md:81` | PROVABLE_FROM_REPO | READ `pyproject.toml:15,33,36,38`. (Optionally add `pymysql`, `:39`.) | — (wording stands; adding `pymysql` would make it complete) |
| "CI (ruff · pytest · gitleaks)" | `README.md:84` | PROVABLE_FROM_REPO | READ `.github/workflows/ci.yml:30-44` (ruff, ruff-format, pytest+coverage gate, pip-audit, mypy informational), `:59-66` (web lint/typecheck/build/npm audit), `:77-82` (gitleaks full history). Understated if anything. | — (wording stands) |
| "`USER_AUTH_ENABLED=true` adds Argon2id sessions … Quality gates: `uv run ruff check` · `uv run pytest`" | `README.md:93-95` | PROVABLE_FROM_REPO | READ `pyproject.toml:41` argon2-cffi, `app/api/auth.py`; RAN pytest (991 passed). | — (wording stands) |

### 3.2 `web/app/(marketing)/page.tsx`

This file carries the project's most prominent claims and its two worst ones. See §4 for the full metric provenance and §4.4 for why the 82% cannot be republished.

| Claim (verbatim) | Location | Class | Evidence | Approved replacement wording |
|---|---|---|---|---|
| `value: "82%"` / `label: "Execution accuracy"` / "18 of 22 golden business questions returned the exact correct result set." | `page.tsx:78-80` | UNPROVABLE_FROM_REPO (and STALE) | Source is `eval/results.json` (`exec_accuracy: 0.818…`, `answerable: [18,22]`, mtime 2026-07-08). RAN `git ls-files eval` → **0**: the harness is untracked, so a fresh clone cannot reproduce it. RAN: the API it posts to (`127.0.0.1:8010`, `eval/harness.py:18`) refuses connections and the eval-store DSN (`eval/rescore.py:14`) is rejected. Measured under `sql_agent_prompt@1.0`, **retired 2026-08-21** (READ `app/agent/prompt.py:5-7`); the contamination is visible in the run itself — row 1 generated `SELECT COUNT(*) … FROM CompanyMaster;` against a 4-table demo store. | **Retire the number.** Replace card with: value `343/343`, label `Adversarial policy cases denied`, note: "Every statement in the deny corpus — 343 case/dialect combinations drawn from 255 cases — was denied by policy `sql_policy@2.0.0`. Reproduce with `uv run pytest tests/sqlpolicy` (616 tests, verified 2026-08-24)." If a query-accuracy figure must appear, use the dated Option B in §4.5. |
| `value: "100%"` / `label: "Fail-closed on unsafe input"` / "All 4 unsafe or out-of-scope prompts were refused — never a hallucinated query." | `page.tsx:83-85` | **FALSE** | READ `eval/harness.py:94`: `destructive = any(k in gen_sql.upper() for k in (…)) and valid and status == "success"`. READ `eval/harness.py:64-67`: any exception fabricates `{"status":"error","sql":"","validation_passed":False}`. So an outage, a rate-limit or a crash all score "fail-closed". In all 4 recorded rows `gen_sql` holds **English prose, not SQL**, and `validation_passed` is False — the model declined at generation and **no statement ever reached the validator**. The run therefore contains zero observations of the validator blocking a write. The clause "never a hallucinated query" also collides with row 1 of the same run, which hallucinated `CompanyMaster`. | "Unsafe prompts are handled in two independent places: the model declines, and — if it does not — the policy engine denies. In the 2026-07-08 golden run all four unsafe/out-of-scope prompts were declined at generation, so that run does not exercise the policy layer; the policy layer is measured on its own corpus (343 of 343 deny cases)." |
| `value: "0"` / "Writes to your data" / "Every query — generated or hand-edited — passes a read-only validator. Writes & DDL are always rejected." | `page.tsx:88-90` | PARTIALLY_TRUE | Both routes really do funnel through one door: READ `app/main.py:47`, `:294-324` `_run_read_only`, `/run_sql` at `:561`, `/query` at `:750`; `app/execution/service.py:1-20` "There is deliberately no second entrance". "always rejected" is an absolute over a parser-bounded filter. | value `0`, label `Write paths to your data`, note: "Generated and hand-edited SQL take the same route: an AST policy check, then execution inside a transaction that is rolled back at the end. On PostgreSQL, MySQL and SQLite that transaction is opened read-only at the database." |
| eyebrow "Measured, not claimed" / heading "Evaluated on a real golden set" | `page.tsx:288-296` | UNPROVABLE_FROM_REPO | The measurement it points at is not reproducible (row 1 above). The eyebrow is a meta-claim that the numbers beneath it cannot currently support. | Keep the eyebrow only if the cards beneath it are the reproducible policy-corpus numbers. Heading → "Evaluated on an adversarial policy corpus". |
| "Golden-set eval: 22 answerable + 4 unsafe / out-of-scope business questions … Execution accuracy = exact result-set match (order-insensitive), Spider-style." | `page.tsx:317-319` | UNPROVABLE_FROM_REPO | Same reproducibility problem, **plus the descriptor is inaccurate**: READ `eval/harness.py:35-41` `as_set()` returns a **frozenset**, so duplicate rows collapse and multiset differences are invisible. Standard Spider execution match compares multisets and respects order when the gold has `ORDER BY` — which the repo's own Spider script does do (READ `eval/spider_eval.py:5-6`). | "Corpus: `app/evaluation/datasets/adversarial/sql_policy_cases.yaml` v2.0.0 (255 cases across PostgreSQL, MySQL, SQL Server and SQLite). It tests the policy decision, not a live database. Query-accuracy figures are tracked separately in `eval/` and are re-published only when a run can be reproduced from a clean checkout." |
| "Six providers, always answers" / "Gemini's free tier is always wired as the final fallback, so the live demo never goes dark." | `page.tsx:134-136` | **FALSE** | The ordering is real (READ `app/agent/chain.py:55-62`, exactly matching the marketing arrow chain). But READ `app/agent/chain.py:71-74` appends gemini unconditionally with the comment "uses free tier", and `:410` constructs `ChatGoogleGenerativeAI(...)` with **no `api_key=`** — RAN with `GOOGLE_API_KEY`/`GEMINI_API_KEY` unset: `DefaultCredentialsError`. With no Google key the "always wired" fallback throws inside the loop and `/query` returns the last error. Also, "six" is aspirational: `get_available_providers()` returns only providers with a key present; the eval run's own log shows `attempt 1/2` (`eval/server.log:330`) — two providers live during the run that produced the headline numbers. | Title → "Provider fallback, not a single point of failure". Body → "Six providers are wired in priority order (OpenAI → OpenRouter → DeepSeek → Groq → Anthropic → Gemini). If a generation call fails or is rate-limited, the request moves to the next provider that has credentials configured. How many are live depends on which API keys you set — with none set, generation fails rather than degrading silently." |
| "automatic fallback across six LLM providers (OpenAI → OpenRouter → DeepSeek → Groq → Anthropic → Gemini)" | `page.tsx:107-109` | PARTIALLY_TRUE | Order verified (`app/agent/chain.py:55-62`); loop verified (`app/main.py:640-699`, breaks on first success at `:684`). "six" describes what is wired, not what is live. There is no health check or circuit breaker on this path — a `CircuitBreaker` exists at `app/llm/router.py` but RAN `grep -rn "from app.llm" app/ db/` shows **nothing outside `app/llm/` itself imports it**. | "A LangGraph agent writes the query, retrying across whichever of six configured LLM providers have credentials (OpenAI → OpenRouter → DeepSeek → Groq → Anthropic → Gemini)." |
| "It's proven read-only" / "Before anything runs, a deterministic validator inspects the SQL. Writes, DDL, and multi-statements are rejected — this is code between generation and execution, not a prompt the model can ignore." | `page.tsx:112-115` | PARTIALLY_TRUE | The second sentence is accurate and well-supported (RAN probes, §4.2). "proven" is not: the engine decides on the parsed shape of a statement, so its guarantee is bounded by sqlglot's parse fidelity per dialect. | Title → "Checked read-only before it runs". Body → "Before execution, a SQLGlot AST policy engine parses the statement and rejects anything that is not a single read-only SELECT — writes, DDL, multi-statements, system-catalog access and blocked functions. This is code between generation and execution, not an instruction in the prompt." |
| "'Read-only' isn't an instruction the model might ignore — it's a deterministic validator … If a query can't be proven read-only, it never runs." | `page.tsx:130-131` | PARTIALLY_TRUE | First half accurate. "proven" + "never" both banned. | "'Read-only' is not an instruction the model might ignore — it is a policy engine between generation and execution. A statement the engine will not classify as read-only is not executed." |
| "Read-only by construction" / "Every query — generated or hand-edited — passes a read-only validator. Writes, drops, and DDL are always rejected." | `page.tsx:55-56` | PARTIALLY_TRUE | Single-door execution verified (READ `app/execution/service.py:1-20`; `app/main.py:561,750`); policy re-evaluated server-side with a fingerprint check (`app/execution/service.py:9-12`). "by construction" and "always" overclaim. | Title → "Read-only enforced in code". Body → "Generated and hand-edited SQL take the same path through the policy engine; writes, drops and DDL are rejected there. Execution runs through a least-privilege database role inside a transaction that is rolled back." |
| "DBWhisper refuses to enroll a writable connection." | `page.tsx:64-66` | PROVABLE_FROM_REPO | READ `app/main.py:951-963`: logs "Refusing to enroll db_flag=%s — connection appears writable" then raises `HTTPException(400, …)`. This marketing line is **more accurate than SECURITY.md**, which still says "warns" (§3.3). | — (wording stands). Optional strengthening for honesty: add "…the probe is a backstop for the role you configure, not a substitute for it" (see the two caveats in §3.3). |
| "SQL shown before it runs … Nothing touches your data that you can't inspect first" | `page.tsx:59-61` | PARTIALLY_TRUE | True for `/run_sql`, where the user supplies the SQL. For `/query` the agent generates, validates **and executes** in one round trip — the SQL is shown *with* the results, not for pre-approval. READ `app/main.py:578+`: there is no approval gate. The policy engine has a `needs_approval` decision (24 corpus expansions) but no endpoint or UI consumes it. | "Every answer ships with the exact SQL that produced it, so you can read it, edit it and re-run it through the same policy check." |
| "No black-box code execution … never arbitrary code that gets executed on your server" | `page.tsx:69-71` | PARTIALLY_TRUE | READ `app/agent/chain.py:98-106` (`LLMResponse` structured output) and `app/agent/tools.py` (tools driven by name); no eval/exec path found. The mechanism claim is sound; "never" is an absolute over a negative that was not exhaustively searched. | "The model emits SQL and structured JSON, and calls a fixed set of named retrieval tools. There is no path that executes model-authored code on the server." |
| "Embedding similarity pulls only the tables your question needs … not a 200-table dump. **It scales to large databases** and cuts invented columns." | `page.tsx:101-103` | UNPROVABLE_FROM_REPO | Retrieval is real (`app/core/retriever.py:109`). The **scale** claim has no measurement: the largest enrolled artifact in the repo is `database_schemas/crm_db` with 16 table YAMLs; `demo` and `eval_store` have 4 tables each. No large-schema benchmark exists. | "Embedding similarity pulls the tables a question needs, so the model sees a focused schema instead of the whole catalog — smaller prompts and fewer invented columns. Retrieval is top-k over per-table summaries; we have not yet published a benchmark on a large schema." **Delete "It scales to large databases."** |
| "it works on databases with **hundreds of tables**" | `page.tsx:126-127` | UNPROVABLE_FROM_REPO | Same as above — nothing in the repo has been run at that scale. | **Delete** until a measurement exists; then republish with n, schema size and date. |
| "Connect Postgres or MySQL" (metadata / step 1 / hero) vs "Postgres · MySQL · SQL Server" (feature card) | `page.tsx:8`, `:15`, `:263-264` vs `:47-48` | PARTIALLY_TRUE | **Internally inconsistent inside one file.** Drivers exist for all three (`pyproject.toml:36,38,39`); reflection is dialect-agnostic (`app/schema_pipeline/introspector.py:62`). SQLite/DuckDB are supported by the engine (`app/sqlpolicy/types.py:17-43`) and are what the tests use, but appear in no public claim. | Pick one list and use it everywhere: "PostgreSQL, MySQL and SQL Server (SQLite for local runs)". In step 1 add: "Enrollment introspects the schema through SQLAlchemy reflection, so it is not tied to one engine; PostgreSQL, MySQL and SQLite additionally run each query inside a database-enforced read-only transaction." |
| Step 1: "Connect your database — Point DBWhisper at Postgres or MySQL with a read-only role" | `page.tsx:14-16` | PARTIALLY_TRUE | **The frontend has no enrollment UI.** READ `web/src/lib/api.ts`: exported calls are `getHealth:153`, `runQuery:179`, `listDatabases:209`, `runSql:228`, `listVerifiedPairs:252`, `saveVerifiedPair:270`, `deleteVerifiedPair:293`, `getSchema:306` — **no enroll call**. `grep -rn enroll web/src web/app` hits only a comment (`api.ts:208`), a docstring (`:305`) and marketing copy. The backend endpoint exists (`app/main.py:923-926`) and is API-key-gated in production (`.env.example:84-89`, `SECURITY.md:34`). | "Enroll a database with a read-only role. Enrollment is an API call (`POST /schemas/enroll`) — the hosted demo ships with a sample database already enrolled." |
| "Export any result to CSV, JSON, or Markdown in one click" | `page.tsx:47-48` | PARTIALLY_TRUE | READ `web/app/components/ExportMenu.tsx:27,30` — CSV and JSON download. `:32-33` — Markdown is **"Copy Markdown"** to the clipboard (`navigator.clipboard.writeText`, `:15`), not a file export. Note also that the artifact viewer blocks page-initiated downloads, so "one click" is environment-dependent. | "PostgreSQL · MySQL · SQL Server. Download any result as CSV or JSON, or copy it as a Markdown table." |
| "Get SQL + results you trust … returns a table, a chart, and a plain-English summary" | `page.tsx:24-25` | PROVABLE_FROM_REPO | READ `ResultsTable.tsx:29-39` (sortable), `ResultChart.tsx:20-24` + `web/src/lib/chart.ts` `pickChart`, `natural_summary` in `app/models.py`. | — (wording stands) |
| "The generated SQL is **always** shown — and you can edit and re-run it, still through the read-only validator." | `page.tsx:33` | PROVABLE_FROM_REPO | Mechanism verified: `/run_sql` → `_run_read_only` (`app/main.py:561`), same execution service. The `sql` field is populated on the response model. | — (mechanism stands). House style: drop "always" for consistency with the rules checklist — "The generated SQL is shown with every answer". |
| "sortable table, an auto-selected chart, and a one-line plain-English summary" | `page.tsx:37-38` | PROVABLE_FROM_REPO | READ `ResultChart.tsx:26,91` — chart kinds are **bar and line only** (no pie). The claim does not promise pie, so it stands. | — (wording stands) |
| "It suggests follow-up questions and keeps context across a session" | `page.tsx:42-43` | PROVABLE_FROM_REPO | READ `app/main.py:737,807`; `app/models.py:317-320`; `db/conversation_memory.py`. | — (wording stands) |
| Product mock chrome shows the URL `dbwhisper.app/app` | `page.tsx:163` | **FALSE** | READ `web/src/lib/site.ts:4-5`: canonical origin is `https://dbwhisper.vercel.app`. RAN grep: `dbwhisper.app` appears nowhere else in the repo. The mock implies a domain the project does not own or use. | Change to `dbwhisper.vercel.app/app`, or drop the URL chrome from the mock entirely. |
| Hero pill: "Read-only by default · open source" | `page.tsx:257` | PROVABLE_FROM_REPO | `LICENSE` present; "by default" is the correct hedge — it does not claim enforcement the code cannot deliver on every engine. | — (wording stands; this is the model for how to phrase the safety claim) |
| "Query your data in plain English. / Try it on the built-in sample database — no signup required." | `page.tsx:435-439` | PARTIALLY_TRUE | READ `database_schemas/demo/` exists; public db_flags are unauthenticated (`app/main.py:388,425`). "No signup" holds only while `GATE_QUERY_ENDPOINT=false` (`.env.example:88-89`). | "Try it on the built-in sample database — no signup required on the hosted demo." (Ties the claim to the deployment whose config makes it true.) |
| Security section heading: "Read-only by default. **Your data stays safe.**" | `page.tsx:408` | PARTIALLY_TRUE | First sentence is well-hedged and accurate. Second is an unbounded guarantee about outcomes the project does not control (network, credentials, the operator's role configuration). | "Read-only by default. You can see exactly what runs." |

### 3.3 `SECURITY.md`

| Claim (verbatim) | Location | Class | Evidence | Approved replacement wording |
|---|---|---|---|---|
| "Every generated statement passes `app/core/sql_validator.py` before execution: it must start with `SELECT`/`WITH`, may not contain DML/DDL keywords …" | `SECURITY.md:13-17` | STALE | READ `app/core/sql_validator.py:1-14` — now a 47-line wrapper: "Since v2 this is a thin wrapper over `app.sqlpolicy`". The keyword description now belongs to the **secondary** layer (`app/sqlpolicy/legacy_heuristics.py`: system-schema regex `:44`, `select`/`with`/`(` prefix check `:151`). The primary decision comes from the AST engine (`app/sqlpolicy/engine.py:40`, RAN → `sql_policy@2.0.0`). Understates the mechanism. | "**Read-only enforcement.** Every statement passes `app/sqlpolicy` (a SQLGlot AST engine, version `sql_policy@2.0.0`) before execution, with the original keyword heuristics retained as an independent second layer (`app/sqlpolicy/legacy_heuristics.py`). The engine admits a single read-only SELECT/WITH and rejects DML, DDL, GRANT/REVOKE, EXEC, multiple statements, `SELECT … INTO`, system-catalog access (`information_schema`, `pg_catalog`, `sys.*`) and a blocked-function list. Decisions are regression-tested against `app/evaluation/datasets/adversarial/sql_policy_cases.yaml` (v2.0.0, 255 cases across four dialects)." |
| "`app/security/db_readonly_checker.py` runs a best-effort, non-destructive probe and **warns** when a connection appears writable." | `SECURITY.md:18-20` | STALE (understates) | READ `app/main.py:939` comment "reject writable connections (fail closed)" and `:951-963` — it raises **HTTP 400** and refuses the enrollment. Two caveats that belong in the replacement: (i) **fail-open on probe error** — `app/main.py:942-950` catches an exception from the probe, logs a warning, and leaves `read_only_ok = True`; (ii) **weak generic branch** — `app/security/db_readonly_checker.py:105-109` reduces to `SELECT current_user` for any dialect that is not Postgres/MySQL/MSSQL, returning "no write privileges detected" on any non-null result. (Postgres checks superuser + `has_database_privilege(…, 'CREATE')` at `:85-90`; MySQL parses `SHOW GRANTS` at `:93-103`; MSSQL checks `IS_SRVROLEMEMBER('sysadmin')`/`IS_ROLEMEMBER('db_datawriter')` at `:44-60`; the inconclusive default is `(False, …)` at `:123`, which does produce a 400.) | "**Least-privilege connections.** Enrolled databases should use a SELECT-only role. Enrollment runs a non-destructive privilege probe (`app/security/db_readonly_checker.py`) and returns HTTP 400, refusing the enrollment, when the connection appears writable (`app/main.py:951-963`). The probe is best-effort: for dialects other than PostgreSQL, MySQL and SQL Server it is a generic check, and if the probe itself errors, enrollment continues — so the role you configure remains the real control." |
| "**Secrets via environment only.** No secrets are committed. `.env` is git-ignored…" | `SECURITY.md:21-23` | PROVABLE_FROM_REPO | `.gitignore` covers `.env`; `.env.example` is the template; gitleaks runs over full history (`.github/workflows/ci.yml:77-82`). Secrets-at-rest encryption additionally exists (`app/platform/secrets.py`; RAN `generate-key`). | — (wording stands; could be strengthened to mention `DBW_SECRET_KEYS` encryption of stored connection strings) |
| "`app/utils/logger.py` masks connection-string passwords, API keys, and SQL string/number literals" | `SECURITY.md:24-25` | PROVABLE_FROM_REPO | READ `app/utils/logger.py:155-177` (masks `user:password@host`, `password=`/`pwd=`), `:179-197` (SQL string and numeric literals), `:216` (`api_key\|apikey\|token\|secret=…`). | — (wording stands) |
| "`CORS_ALLOW_ORIGINS` is environment-driven; … A wildcard in production emits a startup warning." | `SECURITY.md:26-27` | PROVABLE_FROM_REPO | READ `.env.example:79-82` and the `APP_ENV` note at `:76-77`; env plumbing in `app/core/config.py`. | — (wording stands) |
| "This project is pre-1.0; security fixes are applied to `main`." | `SECURITY.md:38` | PROVABLE_FROM_REPO | READ `pyproject.toml:3` `version = "0.1.0"`. **Note:** this is contradicted by `app/main.py:111` `version="1.0.0"` — the fix belongs in `main.py`, not here (§3.7). | — (wording stands) |

**Missing bullet — add to SECURITY.md.** The read-only *session* is a real and newly-landed control that SECURITY.md does not mention at all:

> **Read-only sessions.** Queries run inside a transaction that is rolled back (`app/execution/connections.py:212-217`). PostgreSQL uses `SET TRANSACTION READ ONLY` with statement/lock/idle timeouts (`:143-152`), MySQL uses `START TRANSACTION READ ONLY` (`:200`), SQLite uses `PRAGMA query_only` (`:169-172`, also set on connect at `:128-137`). SQL Server has no session-level read-only mode (`:161-166`, `enforced=False`); there the controls are the policy engine, a least-privilege login and the driver query timeout.

### 3.4 `.env.example`

| Claim (verbatim) | Location | Class | Evidence | Approved replacement wording |
|---|---|---|---|---|
| "The agent tries providers in priority order … and falls back on rate limits." | `.env.example:6-7` | PARTIALLY_TRUE | READ `app/main.py:685-699` — the loop catches `Exception` and continues on **any** failure, not only rate limits. The comment is narrower than the code. | "…and falls back when a provider call fails, including rate limits." |
| "Google Gemini (free tier) powers BOTH chat and the default embedding provider. GEMINI_API_KEY is automatically aliased to GOOGLE_API_KEY at startup." | `.env.example:8-9` | PARTIALLY_TRUE | Alias verified: READ `app/core/config.py:168-177`. "free tier" implies no key is needed — RAN: without a Google key, `ChatGoogleGenerativeAI` raises `DefaultCredentialsError`. Also stale on embeddings: the default embedding profile is now the local `auto` model (`.env.example:26-27`, `app/embeddings/service.py:62-77`), not Gemini. | "Google Gemini's free tier powers chat when `GEMINI_API_KEY` is set (a key is still required). `GEMINI_API_KEY` is automatically aliased to `GOOGLE_API_KEY` at startup. Embeddings default to a local CPU model — see EMBEDDINGS below." |
| "The default (auto) runs BAAI/bge-small-en-v1.5 locally on the CPU through fastembed/ONNX - no API key" | `.env.example:26-27` | PROVABLE_FROM_REPO | READ `app/embeddings/service.py:62-77`, `app/embeddings/local.py:62`; `pyproject.toml` includes fastembed. (The "~50 MB" figure was **not** verified this session — leave it or drop it, but do not treat it as audited.) | — (wording stands; the "~50 MB" size is unverified and should be softened to "a small model, downloaded once on first use" if precision matters) |
| "DEPRECATED: EMBEDDING_PROVIDER (google\|huggingface) is still read and mapped onto EMBEDDING_PROFILE, with a warning." | `.env.example:34-36` | PROVABLE_FROM_REPO | READ `app/core/embeddings.py:76` emits exactly that deprecation warning. | — (wording stands) |
| "one typed policy object (app/platform/modes.py) decides auth, connection, network, secret and egress behaviour" | `.env.example:38-42` | PROVABLE_FROM_REPO | READ `app/platform/modes.py`; consumed at `app/core/config.py:150-156` and `app/main.py:289`. | — (wording stands) |
| "Comma-separated Fernet keys … Generate one with: `uv run python -m app.platform.secrets generate-key`" | `.env.example:44-48` | PROVABLE_FROM_REPO | **RAN** — the CLI runs and emits a key (output redacted). | — (wording stands) |
| `OLLAMA_BASE_URL` (`:52`) and `MODEL_PROFILE=auto` (`:54`) | `.env.example:50-54` | PARTIALLY_TRUE | RAN `grep -rn "from app.llm" app/ db/` → only `app/llm/*` imports itself. These are read by `app/llm/service.py:38,53`, which **nothing in the request path imports**. Setting them today changes nothing about `/query`. *(Snapshot 09:31 UTC — `app/llm/` is untracked and actively landing; re-check before publishing.)* | Prefix the block: "`# NOT WIRED INTO /query YET — read by app/llm/, which the request path does not import.`" Remove the prefix once the grep shows a request-path importer. |
| `EMBEDDING_PROFILE=auto` | `.env.example:55-56` | PROVABLE_FROM_REPO | **Corrected during this audit.** It *is* on the live path: `app/core/retriever.py:38` → `app/core/embeddings.py:69` (reads `EMBEDDING_PROFILE`) → `app/embeddings/service.py:62-77`. | — (wording stands) |
| `EGRESS_POLICY` (`LOCAL_ONLY` … `REMOTE_ALLOWED`) | `.env.example:57-59` | PARTIALLY_TRUE | The setting resolves (READ `app/core/config.py:154-156` `effective_egress_policy`), but the only consumers are `app/llm/service.py:49` and `app/llm/router.py:157` — off the request path — plus tests. No `/query`-path caller acts on it. | Prefix as above: "`# NOT WIRED INTO /query YET`". |
| `NETWORK_ALLOWLIST` — "Hosts / CIDRs that may be connected to in production (strict) mode" | `.env.example:61-63` | PROVABLE_FROM_REPO | READ `app/platform/network_policy.py`; `app/main.py:287-291`, passed into every execution at `:320-322`. | — (wording stands) |
| "Only honour X-Forwarded-For / X-Real-IP when the API sits behind a proxy you control." `TRUST_PROXY_HEADERS=false` | `.env.example:64-65` | UNPROVABLE_FROM_REPO | RAN `grep -rni trust_proxy app/ db/ tests/` → **one hit**, the declaration itself at `app/core/config.py:49`. No reader, no test. The setting does nothing. | Prefix: "`# NOT WIRED YET — the setting is read into Settings but no code acts on it.`" |
| `OTEL_ENABLED`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_SERVICE_NAME`, `METRICS_ENABLED=true` | `.env.example:67-71` | **FALSE** | RAN `grep -rn "prometheus_client\|opentelemetry" app/ db/ \| wc -l` → **0**, despite `pyproject.toml:46-50` installing prometheus-client and four opentelemetry packages. RAN route enumeration: **there is no `/metrics` route**. `METRICS_ENABLED=true` is a default that promises a feature the app does not have. | Either wire it or mark it: "`# NOT IMPLEMENTED — the OpenTelemetry/Prometheus dependencies are installed but no code emits spans or metrics, and there is no /metrics endpoint.`" Change `METRICS_ENABLED=true` → `METRICS_ENABLED=false` so the default does not imply a live exporter. |
| API-key gate block: "Presence enables auth; REQUIRED in production." / "Also require an API key on /query" | `.env.example:84-89` | PROVABLE_FROM_REPO | READ `app/main.py:388,425` (public db_flags unauthenticated), the `/schemas/*` gate, and `SECURITY.md:34`. | — (wording stands) |
| Rate-limiting block (per-IP token bucket) | `.env.example:91-96` | PROVABLE_FROM_REPO | READ `app/security/ratelimit.py`; `app/utils/tool_cache.py:101-118`. | — (wording stands) |
| `QUERY_CACHE_ENABLED=true`, `QUERY_CACHE_TTL=300` | `.env.example:105-107` | UNPROVABLE_FROM_REPO | RAN `grep -rni query_cache app/ db/ tests/` → only the declarations at `app/core/config.py:101-102`. The cache that actually runs is keyed off `TOOL_CACHE_TTL_SECONDS` (`app/utils/tool_cache.py:17`), which `.env.example` never mentions. | Prefix: "`# NOT WIRED YET.`" and add the real knob: "`# The live tool cache TTL is TOOL_CACHE_TTL_SECONDS (app/utils/tool_cache.py).`" |
| "OPTIONAL UPSTASH/REDIS (future distributed rate-limit/cache)" | `.env.example:109-111` | PROVABLE_FROM_REPO | Explicitly labelled "future" — this is the honest pattern the three rows above should copy. | — (wording stands; use as the template for marking unwired settings) |

### 3.5 `web/README.md`

| Claim (verbatim) | Location | Class | Evidence | Approved replacement wording |
|---|---|---|---|---|
| "A **production-ready** Next.js (App Router) frontend … a **single-page** console" | `web/README.md:3` | STALE (+ banned wording) | "production-ready" is on the banned list. "single-page" is out of date: READ `web/app/(marketing)/page.tsx`, `web/app/(app)/layout.tsx`, plus 20+ components including `AuthProvider`, `WorkspaceProvider`, `MobileNav`, `HistoryMenu`, `ResultChart`, `ExportMenu`, `StagedProgress`. | "A Next.js (App Router) frontend for the DBWhisper natural-language-to-SQL API: a marketing page and a console where you ask questions in plain English and get back the generated SQL, a summary, a sortable table and a chart." |
| "Next.js 15 (App Router) + React 18 + TypeScript (strict)" | `web/README.md:7` | PROVABLE_FROM_REPO | **Verified this session** (it was flagged as doubtful and turned out correct): READ `web/package.json:15` `"next": "15.5.19"`, `:16` `"react": "18.3.1"`. | — (wording stands) |
| "The frontend talks to the DBWhisper API at `NEXT_PUBLIC_API_BASE_URL`." | `web/README.md:23` | **FALSE** | RAN grep: `NEXT_PUBLIC_API_BASE_URL` appears **nowhere** in `web/src`, `web/app`, `web/next.config.mjs` or `web/.env.example`. The code reads `NEXT_PUBLIC_API_BASE` (`web/src/lib/api.ts:11`) and defaults to `/api`; the proxy target is `API_PROXY_TARGET` (`web/next.config.mjs:32`). | "The frontend calls its own origin at `/api/*`, which Next rewrites to the backend named by `API_PROXY_TARGET`." |
| Config table row: `NEXT_PUBLIC_API_BASE_URL` \| `http://localhost:8000` \| "Base origin of the DBWhisper backend" | `web/README.md:25-27` | **FALSE** | Same as above — documents a variable that has no reader. Setting it on Vercel silently does nothing. | Replace the table wholesale — see the block below this table. |
| "Because the variable is prefixed with `NEXT_PUBLIC_`, it is inlined into the client bundle **at build time**." | `web/README.md:35` | PARTIALLY_TRUE | The general statement about `NEXT_PUBLIC_*` is correct, but it is attached to a variable that does not exist, and the default path uses a **server-side** variable (`API_PROXY_TARGET`) that is not build-inlined. | Keep the build-time note, but attach it to `NEXT_PUBLIC_API_BASE` and state that `API_PROXY_TARGET` is read on the server at request time. |
| "API contract: `GET /health` … `POST /query`" | `web/README.md:49-50` | STALE | READ `web/src/lib/api.ts`: the client also calls `runSql:228`, `listDatabases:209`, `getSchema:306`, and the verified-pairs trio `:252,:270,:293`. | Extend the list with `POST /run_sql`, `GET /databases`, `GET /schemas/{db_flag}`, and `GET\|POST\|DELETE /training/pairs`. |
| Vercel step 3: "Add the environment variable `NEXT_PUBLIC_API_BASE_URL` and point it at the deployed API origin" | `web/README.md:58` | **FALSE** | Following this instruction produces a deployment that ignores the setting entirely. | "Add the environment variable `API_PROXY_TARGET` and point it at the deployed API origin (e.g. the Hugging Face Space URL)." |
| "> The backend must allow CORS from the Vercel domain for browser requests to succeed." | `web/README.md:61` | STALE | Obsolete on the default path: READ `web/next.config.mjs:28-33` — the browser is same-origin through the rewrite. `next.config.mjs:12` additionally pins `connect-src 'self'`, so a direct cross-origin call would be blocked by the site's own CSP. | Delete, and replace with: "The browser calls the same origin, so CORS is not involved on the default path." |
| Project tree listing `app/components/ # HealthBadge, ResultsPanel, ResultsTable, CopyButton` and `app/page.tsx # The NL → SQL console` | `web/README.md:65-76` | STALE | Neither matches the current route-group layout (`web/app/(marketing)/`, `web/app/(app)/`) or the 20+ components present. | Regenerate the tree from the current directory listing. |

**Replacement environment table for `web/README.md:21-35`:**

| Variable | Scope | Default | Description |
|---|---|---|---|
| `API_PROXY_TARGET` | server | `http://localhost:8000` | Origin that Next rewrites `/api/*` to (`next.config.mjs:32`). **Set this on Vercel.** |
| `NEXT_PUBLIC_API_BASE` | client, build-time | `/api` | Only to bypass the proxy and call the backend directly; requires backend CORS and a CSP change. |
| `NEXT_PUBLIC_SITE_URL` | build | `https://dbwhisper.vercel.app` | Canonical origin for metadata, robots and sitemap (`web/src/lib/site.ts:4-5`). |
| `NEXT_PUBLIC_SENTRY_DSN` | client | unset | Optional error tracking. |
| `NEXT_PUBLIC_AUTH_ENABLED` | client | unset | Shows the login UI; pair with backend `USER_AUTH_ENABLED`. |

### 3.6 `ENHANCEMENT_PLAN.md`

| Claim (verbatim) | Location | Class | Evidence | Approved replacement wording |
|---|---|---|---|---|
| "> **Status: PLAN ONLY — nothing here is built yet. Review and adjust before we execute.**" | `ENHANCEMENT_PLAN.md:7` | STALE | Every ❌ row in its own gap table has since shipped — see the table below. | "> **Status: superseded — historical record.** Written 2026-06 as a plan; the items below shipped between 2026-06 and 2026-08. Current work is tracked in `docs/v2/IMPLEMENTATION_ROADMAP.md`. Kept for provenance; do not read the gap table as the present state." |
| The gap table's ❌/⚠️ rows | `ENHANCEMENT_PLAN.md:27-39` | STALE | Row-by-row verification below. | Leave the table intact under the new historical header (it is useful provenance), or annotate each row with "shipped". |
| "Real depth: **6-provider LLM fallback** … read-only safety enforcement before executing SQL" | `ENHANCEMENT_PLAN.md:21-24` | PARTIALLY_TRUE | Inherits the "six" caveat from §3.2 — six are wired, the live count equals the number of keys configured. | "Provider fallback across six wired LLM providers (live count depends on configured keys); read-only policy enforcement before executing SQL." |

Row-by-row check of the gap table (all READ):

| Plan row | Plan says | Reality today |
|---|---|---|
| `:29` Git + GitHub repo | ❌ "no `.git` at all — never versioned/pushed" | `.git` present; branch `feat/dbwhisper-v2`; GitHub link at `web/app/components/links.ts:3` |
| `:30` Deploy | ❌ "no Dockerfile / render.yaml / compose" | `Dockerfile` and `render.yaml` present (no compose file) |
| `:31` GitHub Actions CI | ❌ none | `.github/workflows/ci.yml` present |
| `:32` ruff/mypy/pytest config | ❌ "deps only, no `[tool.*]`" | `pyproject.toml:122-127` carries `[tool.pytest.ini_options]`; CI runs ruff, ruff-format, pytest, mypy, pip-audit |
| `:33` Tests | ❌ "zero test files" | 23+ test modules plus `tests/sqlpolicy/` and `tests/llm/`; **RAN: 991 passed** |
| `:34` pre-commit + secret scanning | ❌ none | `.pre-commit-config.yaml`; gitleaks job `ci.yml:77-82` |
| `:35` `.env.example` | ❌ missing | present, 111 lines |
| `:36` docs/ + SECURITY.md | ❌ missing | both present |
| `:37` Alembic migrations | ❌ "raw `create_metadata_tables()`" | `alembic.ini`, `db/migrations/env.py`, `db/migrations/versions/0001_baseline.py`, `db/migrate.py`. **Partially stale**: `create_metadata_tables` is still called at `app/main.py:124` and `:939` |
| `:38` Real frontend | ⚠️ "only a static dev chat.html/js" | full Next.js App Router app under `web/` |
| `:39` Naming | ⚠️ `name = "mysql-agent"` | `pyproject.toml:2` `name = "dbwhisper"` |

**Dangling document references (fix while here):** `app/agent/prompt.py:7` cites `docs/v2/CURRENT_STATE_AUDIT.md`, and `docs/v2/TARGET_ARCHITECTURE.md:3-7` cites `CURRENT_STATE_AUDIT.md`, `CLAIM_AUDIT.md` and `DEPENDENCY_AND_LICENSES.md`. As of 2026-08-24 09:40 UTC, `docs/v2/` contains `IMPLEMENTATION_ROADMAP.md`, `TARGET_ARCHITECTURE.md`, `DEPENDENCY_AND_LICENSES.md` (which landed during this audit) and this file. **`CURRENT_STATE_AUDIT.md` is the one reference that is still dangling** — and it is the document `app/agent/prompt.py:7` points a reader to for the reason prompt@1.0 was retired, which is exactly the provenance §4.4(a) depends on. Create it or repoint that citation.

### 3.7 `app/main.py`, `app/models.py`, `HealthBadge.tsx`

| Claim (verbatim) | Location | Class | Evidence | Approved replacement wording |
|---|---|---|---|---|
| `title="SQL Insight Agent"` | `app/main.py:109` | STALE | The product is DBWhisper everywhere else (`pyproject.toml:2`, README, marketing). This title is what `/docs` shows the public. | `title="DBWhisper"` |
| `description="Natural Language to SQL query agent powered by LangChain with provider fallback"` | `app/main.py:110` | STALE | Understates: it is a **LangGraph** agent (`pyproject.toml:25`), and the description omits retrieval and the policy engine. | `description="Natural-language-to-SQL agent: LangGraph agent, pgvector schema retrieval, provider fallback, and an AST read-only policy engine."` |
| `version="1.0.0"` | `app/main.py:111` | **FALSE** | Contradicts `pyproject.toml:3` `version = "0.1.0"` and `SECURITY.md:38` "This project is pre-1.0". It is served publicly via `/docs` and `/openapi.json`. | `version="0.1.0"` (or read it from package metadata so it cannot drift again). |
| Root payload advertising 4 endpoints (`POST /query`, `POST /schemas/embeddings`, `POST /schemas/enroll`, `GET /health`) | `app/main.py:1183-1193` | STALE | **RAN** route enumeration → 17 application endpoints. Omitted, notably: `/run_sql` (the user-edited-SQL path the marketing sells at `page.tsx:33`), `/ready`, `/databases`, `/schemas/{db_flag}`, all of `/auth/*` (`app/main.py:173`, `app/api/auth.py:19`), and `/training/pairs*`. | Stop maintaining a hand-written list: `{"name": "DBWhisper", "docs": "/docs", "openapi": "/openapi.json", "health": "/health", "ready": "/ready", "note": "See /openapi.json for the full endpoint list."}` |
| `HealthResponse` fields `status="healthy"`, `message="SQL Insight Agent is running"`, `version="1.0.0"` | `app/models.py:404-409` | PARTIALLY_TRUE | These are **static literals**, and `app/main.py:241` documents `/health` as "Liveness probe — static, always 200". A 200 attests that the process is up, not that it can serve a query. `/ready` (`app/main.py:246-260`) is the endpoint with real signal — it checks Postgres and the `vector` extension. | Keep `/health` static (that is a correct liveness design), but fix the strings: `message="DBWhisper API is running"`, `version` sourced from package metadata. |
| Health badge renders "online — …(v1.0.0)" | `web/app/components/HealthBadge.tsx:21-22` | PARTIALLY_TRUE | It reflects only the static `/health` literal, so a green "online" badge can appear while the database is unreachable and every query fails. | Point the badge at `/ready`, or relabel it "API reachable" rather than "online". |

### 3.8 `docs/BACKUP.md`

| Claim (verbatim) | Location | Class | Evidence | Approved replacement wording |
|---|---|---|---|---|
| "dbwhisper's data lives in **Neon Postgres** — conversation memory + pgvector embeddings + the `DatabaseConfig` registry + the `demo` schema" | `docs/BACKUP.md:3-4` | UNPROVABLE_FROM_REPO | Deployment state. READ only; no network request made. | — (frame as "the hosted deployment stores its data in Neon Postgres") |
| "**Neon point-in-time restore (PITR)** is the primary mechanism … No setup required." | `docs/BACKUP.md:7-8` | UNPROVABLE_FROM_REPO | Provider capability, not repo state. | — (attribute to the provider: "Neon provides point-in-time restore; we rely on it as the primary mechanism.") |
| "## Monthly restore verification (do not skip) / A backup you've never restored is a hope, not a backup." | `docs/BACKUP.md:14-15` | PARTIALLY_TRUE | The procedure is documented and sound, but **has never been executed**: the verification log table at `:36-38` contains a single row, `\| _none yet_ \| \| \|`. The document exhorts a discipline the project has not yet started. | Keep the procedure verbatim; add under the log: "**Status: this procedure has not been exercised yet — the verification log below is empty.**" |

### 3.9 `pyproject.toml`, `web/src/lib/site.ts`, `web/app/components/links.ts`

| Claim (verbatim) | Location | Class | Evidence | Approved replacement wording |
|---|---|---|---|---|
| `description = "DBWhisper — a natural-language-to-SQL agent with multi-provider LLM fallback, schema-aware PGVector retrieval, and read-only safety enforcement."` | `pyproject.toml:4` | PROVABLE_FROM_REPO | Every element maps to code; no absolutes, no metrics, no banned adjectives. **This is the most defensible one-liner in the repository** and is the model the README hero should follow. | — (wording stands) |
| `SITE_URL … ?? "https://dbwhisper.vercel.app"` | `web/src/lib/site.ts:4-5` | PROVABLE_FROM_REPO | Verified as configuration. The domain's liveness is a separate, unprovable matter. | — (wording stands) |
| `github.com/mubin-attar-007/dbwhisper` | `web/app/components/links.ts:3` | UNPROVABLE_FROM_REPO | External URL; not resolved this session. | — (no change; external links are inherently unverifiable from the repo) |
| LinkedIn / Hugging Face profile links | `web/app/components/links.ts:4-5` | UNPROVABLE_FROM_REPO | External URLs. | — |
| `apiDocs: "https://heisenbergblue-dbwhisper.hf.space/docs"` | `web/app/components/links.ts:10` | UNPROVABLE_FROM_REPO | External. The adjacent comment `:6-9` accurately explains the CSP reason for going off-origin — good practice, keep it. | — |

---

## 4. Numbers we are allowed to publish today

A number may be published only with a provenance block containing: **dataset + version, n, model, prompt version, date, execution environment, exclusions, limitations.** The blocks below are complete; copy them alongside any use of the number.

### 4.1 APPROVED — Policy corpus results

> **Metric:** 343 of 343 deny cases denied; 210 of 210 allow cases allowed; 24 needs-approval expansions classified as needs-approval.
> **Dataset:** `app/evaluation/datasets/adversarial/sql_policy_cases.yaml`, version `2.0.0` (YAML key at `:11`; header comment at `:3` records `created: 2026-08-21`), 294 lines, **255 distinct cases**.
> **n:** 255 cases → **577 case×dialect expansions** (deny 176→343, allow 73→210, needs_approval 6→24). Zero cases carry `skip_reason`.
> **Dialects:** PostgreSQL, MySQL, SQL Server (tsql), SQLite — expanded per case by `tests/sqlpolicy/test_adversarial_corpus.py:34-43`.
> **System under test:** `sql_policy@2.0.0` (`app/sqlpolicy/engine.py:40`), sqlglot 30.17.0. **No model is involved** — this measures a deterministic decision function, so there is no prompt version and no temperature.
> **Date / environment:** 2026-08-24 09:31 UTC, Windows 10, Python 3.13, `uv run pytest -p no:cacheprovider --color=no tests/sqlpolicy` → **`616 passed in 11.28s`**.
> **Arithmetic closes:** 577 parametrized corpus runs + `test_corpus_is_large_enough` (`:46-50`) + `test_unsafe_cases_never_produce_execution_sql` (`:76-81`) + 31 unit tests (`test_engine_units.py`) + 6 Hypothesis property tests (`test_properties.py`) = **616**.
> **Category coverage (top 12 by case count):** fp/false-positive 61, catalog 26, admin 16, write 14, dos 12, exec 11, ddl 9, obfuscation 9, file 9, unknown_table 8, write_cte 7, sensitive 7 — plus multi-statement, lock, network, session, cartesian, txn, cross_db, recursive.
> **Exclusions:** none — every case in the file runs.
> **Limitations (must be stated):** (a) this measures the **policy decision only**; `tests/sqlpolicy/test_adversarial_corpus.py:1-6` — "Nothing here is ever sent to a real database". (b) It is a **structural filter, not a proof**: the decision is made on the parsed AST, so the guarantee is bounded by sqlglot's parse fidelity for each dialect. (c) A corpus measures the attacks someone thought to write down; 343/343 is evidence of no *known* bypass, not of no bypass.
> **Reproduce:** `uv run pytest tests/sqlpolicy` from a clean checkout. **This is the only headline-quality number in the project that a stranger can reproduce.**

### 4.2 APPROVED — Behavioural probes of the validator (RAN 2026-08-24)

`app.core.sql_validator.validate_sql(..., dialect='postgres')`, 16 statements:

| Statement | valid | Reason returned |
|---|---|---|
| `DELETE FROM orders` | False | `Only SELECT statements are permitted (got Delete)` |
| `DROP TABLE customers` | False | `Only SELECT statements are permitted (got Drop)` |
| `UPDATE products SET price = 0` | False | `Only SELECT statements are permitted (got Update)` |
| `SELECT 1; DROP TABLE customers` | False | `Multiple statements are not permitted` |
| `SELECT * FROM information_schema.tables` | False | `Access to system catalogs is not permitted: information_schema.tables` |
| `SELECT * FROM pg_catalog.pg_user` | False | `Access to system catalogs is not permitted: pg_catalog.pg_user` |
| `SELECT 1 --\nDROP TABLE customers` | False | `SQL parse error: Invalid expression / Unexpected token. Line 2` |
| `SELECT * INTO newtbl FROM customers` | False | `SELECT ... INTO is not permitted` |
| `WITH x AS (DELETE FROM orders RETURNING *) SELECT * FROM x` | False | `DELETE is not permitted` |
| `SELECT pg_sleep(10)` | False | `Blocked function(s): pg_sleep` |
| `COPY customers TO PROGRAM 'curl evil'` | False | `Only SELECT statements are permitted (got Copy)` |
| `SELECT * FROM customers` | True | `SQL passed read-only policy` |
| `SELECT name FROM customers` | True | `SQL passed read-only policy` |
| `SELECT * FROM customers -- comment` | **True** | `SQL passed read-only policy` |
| the row-23 refusal prose from the eval run | False | `SQL parse error` |

**Two nuances that must travel with these results:** (i) a trailing SQL comment on a valid SELECT is **allowed** — the engine parses to an AST and normalizes comments away rather than blocking them, so "blocks comment injection" (`README.md:39`, `README.md:76`) misdescribes the mechanism; (ii) these are probes on a chosen sample, not a proof of the space — they demonstrate behaviour, they do not bound it.

### 4.3 APPROVED — Repository and pipeline facts

> **Test suite:** `991 passed in 42.50s` — RAN 2026-08-24 09:31 UTC, `uv run pytest -p no:cacheprovider --color=no`, Windows 10, Python 3.13, developer `.env` present (GROQ + GEMINI keys; no OpenAI/DeepSeek/Anthropic/OpenRouter key).
> **⚠️ Volatility:** this count moved **845 → 926 → 991** within ~35 minutes as `app/llm/` and `tests/llm/` landed untracked in the working tree. Publish it as "991 tests as of 2026-08-24" or, better, publish the stable sub-figure (616 policy tests) and let CI report the total.
> **Coverage:** `TOTAL 7080 statements, 2369 missed, 67%` — RAN 09:33 UTC, `uv run pytest --cov=app --cov=db` + `uv run coverage report`. Same volatility caveat (52% at 09:0x on 6347 statements, before `app/llm/` landed). CI enforces a floor of 35% (`.github/workflows/ci.yml:37` `--cov-fail-under=35`). The v2 roadmap's pre-work baseline was 41% (`docs/v2/IMPLEMENTATION_ROADMAP.md:19-20`). **If coverage is published, publish the CI gate next to it** — a 67% figure with a 35% gate is a fair description; a 67% figure alone implies enforcement that does not exist.
> **API surface:** **17 application endpoints** (22 routes total: 4 FastAPI built-ins + 1 static mount). RAN via `app.routes`. Full list: `GET /chat`, `POST /auth/register`, `POST /auth/login`, `POST /auth/logout`, `GET /auth/me`, `GET /health`, `GET /ready`, `GET /databases`, `POST /training/pairs`, `GET /training/pairs`, `DELETE /training/pairs/{pair_id}`, `GET /schemas/{db_flag}`, `POST /run_sql`, `POST /query`, `POST /schemas/embeddings`, `POST /schemas/enroll`, `GET /`.
> **Policy engine version:** `sql_policy@2.0.0`, sqlglot `30.17.0` — RAN.
> **Read-only session enforcement by dialect** (READ `app/execution/connections.py:140-179`): PostgreSQL `enforced=True` (`SET TRANSACTION READ ONLY` + statement/lock/idle timeouts); MySQL `enforced=True` (`START TRANSACTION READ ONLY` at `:200` + best-effort `MAX_EXECUTION_TIME`); SQLite `enforced=True` (`PRAGMA query_only`); **SQL Server `enforced=False`**; unknown dialect `enforced=False`. Every connection rolls back in a `finally` block (`:212-217`). **Publish the per-dialect breakdown, never an average.**
> **Python:** `requires-python = ">=3.13"` (`pyproject.toml:6`), CI on 3.13 (`ci.yml:25`).
> **Not verified this session** (carried from the lead's 2026-08-21 run, do not re-publish as fresh): ruff clean; mypy 82 errors across 20 files, informational in CI; frontend lint/typecheck/build passing. **Docker was never exercised** — no claim about the image building may be published.

### 4.4 NOT PUBLISHABLE — metrics that must not appear until re-run

**(a) "82% execution accuracy" / "18 of 22".** Four independent disqualifiers, any one of which is sufficient:

1. **Not reproducible.** RAN `git ls-files eval` → `0`. The entire harness (`eval/harness.py`, `eval/golden.json`, `eval/results.json`) is untracked. A fresh clone does not contain the measurement.
2. **Not re-runnable here either.** RAN: `127.0.0.1:8010` (the API both `eval/harness.py:18` and `eval/rescore.py:15` post to) refuses connections; `127.0.0.1:55432` is open but the eval-store DSN at `eval/rescore.py:14` returns `FATAL: password authentication failed for user "eval"`. Even the no-new-LLM-calls re-score cannot run.
3. **Measured with a deleted prompt.** The run is dated 2026-07-08 (`eval/results.json` mtime). `app/agent/prompt.py:5-7` records that `sql_agent_prompt@1.0` "Hard-coded to a single SQL Server 'DME' schema … used for *every* enrolled database. Retired on 2026-08-21". The contamination is visible **inside the measured run**: answerable row 1 generated `SELECT COUNT(*) AS total_customers FROM CompanyMaster;` against a four-table demo store whose tables are `customers, orders, order_items, products`. The number describes software that no longer exists.
4. **The scoring is looser than advertised.** READ `eval/harness.py:35-41` — `as_set()` returns a **frozenset**, collapsing duplicate rows and hiding multiset differences. `page.tsx:319` calls this "Spider-style"; standard Spider execution match compares multisets and respects order under `ORDER BY`, which the repo's own Spider script does (`eval/spider_eval.py:5-6`).

**What the four misses actually were** (from `eval/results.json` rows) — worth recording because it is more flattering than the headline, and still unpublishable:

| id | Detail | Reading |
|---|---|---|
| 1 | `no result (status=error, valid=False)`, sql `SELECT COUNT(*) … FROM CompanyMaster;` | hallucinated table leaked from prompt@1.0 |
| 8 | `gen=[('Mumbai', 3)] gold=[('Mumbai',)]` | right answer, one extra column |
| 18 | `gen=[('Mechanical Keyboard', 3499), …] gold=[('Mechanical Keyboard',), …]` | right answer, one extra column |
| 22 | `gen=[('Aisha Khan', 3)] gold=[('Aisha Khan',)]` | right answer, one extra column |

Three of the four "failures" are projection-shape mismatches, not wrong answers.

**(b) "100% fail-closed on unsafe input".** Not a stale number — **the wrong measurement**. See §3.2. The scoring predicate (`eval/harness.py:94`) requires `valid and status == "success"` before it can register a failure, and `:64-67` fabricates an error response when the API call throws, so an outage scores as a pass. All four recorded `gen_sql` values are English prose with `validation_passed=False`: the model declined at generation and **nothing reached the validator**. Publishing "100% fail-closed" describes a property this run did not test.

**(c) `eval/rescore.json` "3 of 22".** Do not publish this either — **it is not evidence that accuracy is 14%; it is evidence that the re-score script is broken.** READ `eval/rescore.py:28-30`: `run()` calls `cur.connection.rollback()` **only on success**, and the call sites (`:51-53`, `:60-61`) swallow the exception without rolling back. Under PostgreSQL semantics the connection then sits in a failed transaction and every later `execute` raises `InFailedSqlTransaction`. Row id 1 is the first answerable row and its saved SQL references the non-existent `CompanyMaster`, poisoning the connection for the rest of the loop. Downstream (`:60-69`): gold SQL also raises → `g = []` → `gset = frozenset()`; if the live-API retry succeeds, a non-empty `genset` scores **wrong**; if the retry fails, `gen = None` → empty-vs-empty scores **right**. So "3" most consistently reads as *3 rows whose API retry failed*. Corroboration: both `strict` and `answer_correct` are 3/22, which is itself a tell — the lenient path (`:68`) requires `len(gen)==len(g)` and with `g` always empty it can only agree with the empty-match cases; and `eval/server.log` (mtime 17:55, the same minute as `rescore.json`) shows the API serving `/query` at 17:54–17:55 and stopping mid-request at `17:55:10`. **Status: UNPROVABLE (harness defect).** This mechanism is derived by READING, not reproduced — see §5's uncertainty note.

**(d) `eval/baseline_results.json` "22/22, exec_accuracy 1.0".** The naive no-retrieval, no-validator baseline scored **higher** than the pipeline's advertised 82% on the same golden questions. Do not publish the pipeline number without this one; do not publish this one without its confounders: the baseline ran against `eval/eval_store.sqlite` (`eval/baseline.py:32,59-65`), **not** Postgres, and its prompt says "Return ONLY the SQL" (`:33-39`), so it naturally produced minimal projections and avoided the extra-column misses that cost the pipeline 3 of its 4 points. The file also contradicts itself: the docstring says "Same model (gemini-2.5-flash)" (`:3`) while the code uses `ChatGroq(model="qwen/qwen3-32b")` (`:81`) and the recorded field says `"qwen/qwen3-32b (Groq)"` (`:67`).

**(e) "It scales to large databases" / "hundreds of tables"** (`page.tsx:101-103`, `:126-127`). No measurement exists at any scale. Largest enrolled artifact in the repo: `database_schemas/crm_db`, 16 table YAMLs. Not publishable in any form until a benchmark exists.

**(f) Any live-demo uptime or availability claim.** Nothing in the repo settles it and no network request was made during this audit.

### 4.5 CONDITIONALLY PUBLISHABLE — with the full block attached

**Spider dev, generation model only.** `eval/spider_results.json`: `benchmark: "Spider dev"`, `model: "qwen/qwen3-32b (Groq)"`, `sampled: 148`, `scored: 139`, `correct: 101`, `exec_accuracy: 0.7266`, `llm_unavailable_skipped: 9`, mtime 2026-07-10. `eval/spider_eval.py:1-11` is admirably explicit about scope. Publishable **only** as:

> "Spider dev sample, generation model only (qwen/qwen3-32b at temperature 0.1, schema in context, no retrieval or policy layer): 101 of 139 scored questions matched the gold result set (72.7%); 9 of 148 sampled questions were dropped as transport failures rather than scored. Recorded 2026-07-10; the Spider dataset is not vendored (`.gitignore:45`), so re-running needs the dataset and a provider key."

Note the exclusion honestly: dropping 9 transport failures rather than counting them wrong moves the figure up. The floor if all 9 were counted wrong is 101/148 = 68.2%; publish the range if the reader deserves it.

**Golden-set accuracy, if it must appear at all.** Only in this dated, caveated form, and never as a stat card:

> "On a 22-question golden set run on 2026-07-08 against a four-table PostgreSQL store, 18 of 22 generated queries matched the reference result set exactly; three of the four misses returned the correct rows with one extra column. That run used prompt `sql_agent_prompt@1.0`, which was retired on 2026-08-21, and it has not been reproduced since."

**The standing recommendation is to publish neither** and to lead with §4.1, which anyone can reproduce.

---

## 5. How to add a new claim

A claim reaches the UI last, not first. The order is non-negotiable because it is the order that makes the claim true.

1. **Measure it.** Write the harness **inside the repository** and commit it. If `git ls-files <harness-dir>` returns nothing, the number does not exist for publication purposes — this is exactly how the 82% became unpublishable. The harness must run from a clean checkout with documented prerequisites.
2. **Make it re-runnable by a stranger.** Fixtures committed or downloadable by a scripted step; no dependence on a service that happens to be running on the author's laptop, and no credentials baked into a DSN. If a provider key is required, say so and make the failure mode loud.
3. **Check what the metric actually measures.** Read the scoring predicate line by line and ask: *what else would produce this number?* If a total outage, a rate-limit, or an empty result would score as a pass, the metric is measuring availability or emptiness, not the property you are naming. (See §4.4(b) and (c) — both defects are of exactly this kind.)
4. **Record the provenance block**: dataset + version, n, model, prompt version, date, execution environment, exclusions, limitations. A metric without a prompt version is not re-checkable after the next prompt change.
5. **Add a row to this document** — verbatim claim, `path:line`, classification, evidence, and the wording you intend to publish. A claim that cannot be written down in this table with a path and a line number is not ready.
6. **Run the §1.3 checklist** against the proposed sentence. Every box.
7. **Only then write it in the UI**, and make the UI cite this document (or the reproducing command) so a sceptical reader has somewhere to go.

**Re-validation triggers.** Re-run this audit's affected rows whenever any of these change: the prompt version (`app/agent/prompt.py` `PROMPT_VERSIONS`), `RULESET_VERSION` / `POLICY_VERSION` (`app/sqlpolicy/rules.py`, `engine.py:40`), the corpus version (`sql_policy_cases.yaml:11`), the provider list (`app/agent/chain.py:55-62`), the endpoint set (`app/main.py`), or any dependency that changes dialect parsing (sqlglot). A claim's row should be considered expired when the version it cites no longer matches.

**When a claim is retired, say so.** The pattern at `app/agent/prompt.py:5-7` — a version history that records what `@1.0` did, why it was wrong, and when it was retired — is the best documentation practice in this repository. Copy it. It is the single reason the staleness of the 82% could be established at all.

---

## 6. Open uncertainties in this audit

Stated plainly so no reader over-trusts the sections above.

1. **The `eval/rescore.py` failure mechanism (§4.4(c)) is READ-derived, not reproduced.** The API endpoint refuses connections and the eval-store credentials are rejected. Treat the `InFailedSqlTransaction` cascade as a well-supported hypothesis. What is **certain** regardless: `3/22` is not a measurement of query accuracy, and the script never rolls back on the error path.
2. **The working tree moved during the audit.** `app/llm/` and `tests/llm/` are untracked and landed mid-session; the suite total moved 845→926→991 and coverage 52%→67%. Every statement about `MODEL_PROFILE` / `EGRESS_POLICY` / `OLLAMA_BASE_URL` wiring, and the "no circuit breaker on the live path" finding, is a **09:31 UTC snapshot**. Re-run `grep -rn "from app.llm" app/ db/` before publishing provider copy.
3. **Live-deployment claims were not tested.** No network request was made. The live-demo badge, the API-docs badge, the HF Space / Vercel / Neon topology, and everything in `docs/BACKUP.md` about Neon PITR are `UNPROVABLE_FROM_REPO` — **not** false.
4. **Two notes from earlier working drafts were wrong and are corrected here**, which is itself a reason to re-verify rather than inherit: `EMBEDDING_PROFILE` *is* wired into the live retrieval path (`app/core/retriever.py:38` → `app/core/embeddings.py:69` → `app/embeddings/service.py:62`), and `web/README.md:7`'s "React 18" *is* correct (`web/package.json:16` `"react": "18.3.1"`).
5. **Several `.env.example` line numbers in earlier drafts had drifted** and were re-derived for this document; the file is 111 lines. If it is edited, re-derive again rather than trusting these citations.
6. **Docker was not exercised**, so nothing about the image building, or the msodbcsql18/pyodbc runtime requirement, is verified.
7. **Measurements were taken with a developer `.env` present.** CI, without those keys, may take different code paths.
8. **`eval/` is untracked and its scripts parse the repo `.env` directly** (`eval/baseline.py:22`, `eval/spider_eval.py`). Any published eval number must also state that a fresh clone contains neither the harness nor the fixtures.
