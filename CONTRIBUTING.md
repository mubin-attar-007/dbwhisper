# Contributing

This is the guide for someone who has just cloned the repository and wants to land a change that
passes review. It documents what this project actually does, not what a template says a project
should do; every command below was executed on this branch before it was written down, and the
[Verification](#verification) section at the bottom records exactly which ones and what they printed.

Read these first, in this order — most review comments are one of them restated:

| Document | Why you need it |
|---|---|
| [`docs/LOCAL_SETUP.md`](docs/LOCAL_SETUP.md) | Getting the stack running, in three increasing levels of effort. |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | The request path and the parts, in prose. |
| [`docs/v2/TARGET_ARCHITECTURE.md`](docs/v2/TARGET_ARCHITECTURE.md) | The design of record: package layout, import contract, mode policy, the rules in §4 below. |
| [`docs/adr/`](docs/adr/) | One record per decision that was expensive to make and would be expensive to reverse, each with what it *costs*. Read the one covering the area you are changing before you argue with it. |
| [`docs/v2/CLAIM_AUDIT.md`](docs/v2/CLAIM_AUDIT.md) | What may be said in public, and the order in which a claim becomes sayable. |
| [`docs/v2/IMPLEMENTATION_ROADMAP.md`](docs/v2/IMPLEMENTATION_ROADMAP.md) | The implementation log at the bottom is the source of truth for what is done and what is not. |

**Found a vulnerability?** Do not open a pull request or a public issue. Follow
[`SECURITY.md`](SECURITY.md) — private advisory or email — so a fix can land before the details do.

---

## 1. Four rules that override preference

These are not style opinions. A change that breaks one of them gets rejected however good it is.

1. **Nothing in the core product, its tests, its demo or its evaluation may require a paid service
   or an API key.** The local path is Ollama for chat, fastembed on CPU for embeddings, and a
   deterministic fake provider for CI. Every hosted provider is an optional adapter that disables
   itself when its key is absent (`app/llm/registry.py`, `PROVIDER_KEY_ENV`). If your change makes
   `uv run pytest` need a credential, it is the wrong change.
2. **Security over convenience.** Never weaken a control to make something pass. If a gate is wrong,
   fix the gate in its own commit and explain why in the message — `ec16326` and `5aa9c91` are the
   precedents for how that is done.
3. **Permissive licences only.** MIT, BSD, Apache-2.0, ISC, PSF. No GPL, AGPL or SSPL in a runtime
   dependency, transitively included. Weak copyleft (MPL, LGPL) is permitted only as an unmodified,
   separately installed library and must be written into
   [`docs/v2/DEPENDENCY_AND_LICENSES.md`](docs/v2/DEPENDENCY_AND_LICENSES.md) §4.1 with the
   obligation it creates spelled out. Check before you add, not after.
4. **Say only what you have measured.** See §6. This applies to code comments and commit messages as
   much as to the README.

---

## 2. Development environment

### Python

`pyproject.toml` declares `requires-python = ">=3.13"` and `.python-version` pins `3.13`. You do not
need to install it yourself — `uv` will:

```bash
uv sync
```

That installs the project dependencies **plus the `dev` dependency-group** (`uv` installs
`dependency-groups.dev` by default), which is where ruff, mypy, pytest, pytest-cov, hypothesis,
pip-audit, import-linter, vulture and pre-commit come from. CI runs `uv sync --frozen`, which fails
rather than re-resolving if `uv.lock` is stale; run `uv lock` and commit the result if you change a
dependency.

The `local-embeddings` extra is deliberately **not** installed, and you almost certainly do not want
it. It pulls torch, transformers and sentence-transformers — several gigabytes — and as of this
writing **nothing under `app/` or `db/` imports any of them** (`grep -rn
"langchain_huggingface\|sentence_transformers" app/ db/` returns no hits). Local embeddings run on
fastembed, which is a base dependency: `EMBEDDING_PROFILE=auto` selects the local fastembed provider
(`BAAI/bge-small-en-v1.5`, 384-dimensional, ONNX on CPU) and falls back to a configured hosted
provider only if that fails. The extra is:

```bash
uv sync --extra local-embeddings
```

### Node

CI uses Node 22 with `npm ci` against `web/package-lock.json` — matched to `web/Dockerfile`
(`node:22-alpine3.22`), because testing on one major while shipping another means CI never exercises
the runtime users get. Newer versions work locally; the runs recorded below were on Node 24.14.1.

```bash
cd web && npm ci
npx playwright install --with-deps chromium   # once, for the e2e suite
```

### What you do not need

No API key. No PostgreSQL. No Ollama. No GPU. The entire Python test suite, the frontend suite and
the evaluation gate run offline on a laptop. You need Docker only for the `postgres-integration` CI
job and for `docker compose --profile core up`.

### The offline contract, and how it is enforced

`tests/conftest.py` sets five variables with `os.environ.setdefault` *before* anything imports the
application:

```python
MODEL_PROFILE=fake             # deterministic fixture-backed model provider
EMBEDDING_PROFILE=fake         # deterministic hash embeddings
APP_ENV=test
POSTGRES_CONNECTION_STRING=postgresql+psycopg://test:test@localhost:5432/test   # never connected to
LOG_SANITIZE=1
```

Two details are load-bearing:

* **`setdefault` plus the process environment beats `.env`.** `app/core/config.py` reads
  `env_file=".env"`, and pydantic-settings gives process environment variables priority over the
  file. So a developer `.env` that points `MODEL_PROFILE` at a hosted provider cannot silently push
  the suite onto the network. `.github/workflows/ci.yml` sets the same two variables at workflow
  level anyway — not because the conftest is untrusted, but so the intent is visible in the job
  definition.
* **Provider keys in your `.env` are still visible** to code that reads `os.environ` directly. With
  `MODEL_PROFILE=fake` only the fake profile is enabled, so this does not change routing, but if you
  are chasing a discrepancy against CI, run from a shell with no provider keys set. That caveat is
  recorded in `CLAIM_AUDIT.md` §1.5 for a reason: measurements taken with a populated `.env` have
  misled this project before.

**Run pytest from the repository root.** Two tests in `tests/retrieval/test_retrieval.py` (lines 414
and 423) open `database_schemas/demo/schema/schema_index.yaml` by a CWD-relative path. Running the
suite with the working directory elsewhere fails exactly those two and nothing else — verified.

---

## 3. The gates

`.github/workflows/ci.yml` has six jobs, split so that a job name tells you what broke. Run the ones
your change touches before you push; run all of them before you ask for review.

### backend — lint, types, tests

```bash
uv run ruff check app db tests scripts run.py
uv run ruff format --check app db tests scripts run.py
uv run lint-imports         # the architectural contracts; see §4
uv run mypy --follow-imports=silent app/embeddings app/analysis app/observability db/migrate.py
uv run mypy app db          # the ratchet; see below
uv run pytest --cov=app --cov=db --cov-report=term-missing --cov-fail-under=65
```

Ruff is configured in `pyproject.toml`: line length 100, target py313, rules `E,F,I,UP,B,C4,SIM,RUF`.
Four rules are ignored and each ignore has its reason written next to it — `B008` because FastAPI's
`Depends()` in a default is idiomatic, `E501` because layout belongs to the formatter, `E402` because
several modules import after `load_dotenv()`, `RUF001` because prompt templates use intentional
Unicode. Do not add an ignore without a comment saying why.

**The mypy split is the non-obvious part of this pipeline.** There are two steps and they do
different jobs:

* **The gate.** `app/embeddings`, `app/analysis`, `app/observability` and `db/migrate.py` are clean
  and must stay clean. `--follow-imports=silent` is not cosmetic: without it mypy reports errors from
  every module these packages *import*, so the gate would fail on type debt living outside the
  packages it exists to gate. When another package reaches zero, add it here. Never remove one to
  make a build pass.
* **The ratchet.** The rest of the tree carries inherited type debt. Fixing all of it is a project in
  itself; letting it grow is not acceptable either, so **the count is the gate**. CI runs
  `uv run mypy app db`, counts lines matching `error:`, and fails if the count exceeds
  `MYPY_ERROR_BUDGET` (currently `200`). The budget **may shrink and must never grow**. If your PR
  drops the count, lower `MYPY_ERROR_BUDGET` in the same PR to lock the win in — CI emits a notice
  telling you to. If you genuinely need to raise it, say why in the commit message and expect to
  defend it.

  Measured on this branch: `uv run mypy app db 2>&1 | grep -cE 'error:'` → **200**. There is no
  headroom. Adding an untyped module will fail this step, which is the intent.

The coverage floor is 65 and actual coverage is well above it (79.22% measured, 2318 tests). The gap
is deliberate slack so that an unrelated refactor does not turn the build red; it is a floor, not a
target. Raising the floor is welcome when it gets embarrassing, in its own commit.

### frontend — lint, types, tests, build, e2e

```bash
cd web
npm run lint
npm run typecheck
npm test          # vitest
npm run build
npm run e2e       # playwright
```

CI sets `CI=true` at the job level, which is what makes `forbidOnly`, `retries` and `workers` take
effect in `playwright.config.ts`; without it a stray `test.only` would pass silently.

The e2e suite needs **no backend and no database**. Every `/api/*` call is intercepted with
`page.route`, which works because the console is same-origin by design (`next.config.mjs` rewrites
`/api/*` server-side), and `API_PROXY_TARGET` points at a closed port so an unstubbed request fails
loudly instead of quietly reaching whatever is listening on :8000. Playwright starts a **production**
Next server, not `next dev`: the site's CSP has no `'unsafe-eval'` and the dev bundler needs it, so
under `next dev` the page renders but never hydrates and every spec fails looking like a selector bug.

### eval-smoke — the offline oracle run

```bash
bash scripts/eval-smoke.sh          # ./scripts/eval-smoke.ps1 on PowerShell
```

The exit code is the gate: non-zero means a case whose contract is to be declined returned rows, or a
statement that executed failed an independent re-check by the policy engine. It runs against SQLite
fixtures generated from code, with the oracle provider, so it needs no credentials, no network and no
model. See [`docs/EVALUATION.md`](docs/EVALUATION.md) for the other sub-commands.

### postgres-integration — Alembic and pgvector on a real PostgreSQL

Every unit test runs on SQLite, so this job exists because Alembic-on-PostgreSQL and the pgvector
extension were otherwise exercised for the first time in production. It drops and recreates `public`,
runs the upgrade twice to prove idempotence, checks the expected tables exist, then builds an ivfflat
cosine index over 384-dimensional vectors and asserts a nearest-neighbour query returns the known
row. Reproducing it locally needs a `pgvector/pgvector:pg16` container and
`POSTGRES_CONNECTION_STRING` pointed at it; the two check scripts are inline in `ci.yml` so the whole
gate is readable in one file.

### security — secrets, dependencies, SAST, filesystem

Blocking: **gitleaks** over full history (a secret removed in a later commit is still leaked) and
**bandit** at `-ll -ii` (medium severity, medium confidence), run through `uvx` so it never becomes a
project dependency. Anything bandit flags is fixed, or suppressed at the line with a stated reason —
never by loosening the threshold.

Informational, and this is a deliberate trade-off rather than laziness: **pip-audit**, **Trivy** and
**npm audit** report advisories that often sit in transitive pins the lockfile cannot move without a
dependency bump. Making them blocking means CI turns red on a schedule for changes nobody made. They
run on every build and weekly on cron so the list stays visible; Dependabot raises the PRs that
shorten it.

### codeql

Runs in its own job over `python` and `javascript-typescript` with `security-extended`, because it
needs `security-events: write` and folding it into the security job would hand that job write access
to the security tab for no reason. The path-traversal work in `fec7548` came from a CodeQL finding.

### pre-commit (optional, recommended)

```bash
uv run pre-commit install
uv run pre-commit run --all-files    # manual full pass
```

Not a CI gate. It runs ruff, ruff-format, gitleaks and the usual hygiene hooks so you find those
failures in a second rather than in a five-minute CI round trip.

---

## 4. Architectural rules a PR must respect

Some of these have sweep tests behind them. A sweep test is one that fails when a *future* change
reintroduces a defect the project has already had — not when the code under test is wrong, but when
someone forgot to wire something up. If you trip one, the message tells you what it is protecting;
read it before you edit the test.

### R1 — All SQL against a target database goes through the execution service

`app/execution/service.py::execute` is the only entrance. Before a row is fetched it: re-evaluates
the statement with `app/sqlpolicy` **even when the caller already holds a decision** (a
caller-supplied decision is accepted only as an approval token, and its fingerprint must match the
statement being run, which is what stops an approved query being swapped for another one); checks the
target host against the network policy; opens a read-only transaction with a statement timeout that
is rolled back whatever happens; and reads at most `max_rows + 1` rows so truncation is detected
rather than guessed. Driver exceptions are translated before they leave, because they carry the DSN,
the host and the schema.

Do not open an engine, a session or a connection to a target database anywhere else. If you need
something `execute()` cannot do, extend `ExecutionRequest`.

> **What is and is not enforced.** The import contracts in §4/R5 make part of this structural: the
> model layer cannot import SQLAlchemy or any driver at all, and the graph cannot import a
> credential-bearing module. They do **not** stop a new module elsewhere from calling
> `create_engine` itself. That half is still held up by review, and reviewers grep for
> `create_engine`, `.connect(` and `.execute(` outside `app/execution/`. See ADR
> [`0003-single-execution-service`](docs/adr/0003-single-execution-service.md), which also records
> where the design and the code currently differ.

### R2 — All schema paths go through `app/platform/paths.py`

A `db_flag` arrives from a URL, a query string or a JSON body. Six modules used to interpolate it
straight into `database_schemas/<db_flag>/…`, so `../../..` walked out of the directory. One guarded
builder now serves all of them, with two independent checks: an allowlist pattern
(`[A-Za-z0-9_-]{1,64}`, so nothing dangerous has to be enumerated) and a resolve-and-contain check
that catches a symlinked source directory.

Two conventions matter when you touch this code. The identifier is **rejected, not sanitised** —
silently rewriting `../../etc` into `etc` turns an attack into a confusing answer about a data source
the caller never named. And the rejected value is **never echoed back into the error**, because that
message reaches logs and a value carrying a newline can forge a log entry.

*Sweep:* `tests/test_paths.py::TestTheCallSitesActuallyUseIt::test_no_module_still_builds_the_path_by_hand`
walks every `.py` under `app/` looking for a hand-built `"database_schemas" /`. It exists to catch a
seventh call site being added the old way. 102 tests in that file.

### R3 — Every state-changing route declares the CSRF dependency

```python
# app/api/v2/router.py — the router carries the /v2 prefix
@router.post(
    "/query",
    response_model=RunResponse,
    dependencies=[Depends(require_api_key_if_enabled), Depends(require_csrf)],
    summary="Ask a question; the run may pause for clarification or approval",
)
```

*Sweep:* `tests/test_wiring.py::TestCsrfIsActuallyMounted::test_every_state_changing_route_depends_on_the_csrf_check`
enumerates every `APIRoute` on the real app whose methods are not in `{GET, HEAD, OPTIONS, TRACE}`
and asserts `require_csrf` is among its dependencies. It is parameterised over the live route table,
so a route added tomorrow is covered without anyone remembering to add a test.

The exemption set is `CSRF_EXEMPT = {"/auth/register", "/auth/login"}` — the only two routes that run
*before* a session exists, where a stale CSRF cookie must not be able to lock someone out and where
there is no ambient authority to abuse. If you believe a new route belongs there, add it to that set
in the same PR with the reason in a comment, and expect the reason to be scrutinised.

The lesson this file encodes is worth stating plainly: a CSRF module that no route depends on passes
its own unit tests perfectly and protects nothing.

### R4 — Policy decisions are never made by the model

The model proposes SQL. `app/sqlpolicy` decides whether it runs. Nothing in between asks a model
whether something is safe, and no model output is treated as an authority over a control.

The same principle makes a long list of things deterministic code rather than prompts: chart
selection (`app/analysis/charts.py`), result statistics (`app/execution/results.py`), shape
verification (`app/analysis/shape.py`), PII classification (`app/security/pii.py`), retrieval fusion,
AST limit injection, join-path search and the investigation causal lint. `app/analysis/summary.py`
carries a deterministic fallback so there is an answer even when no model may be used at all.

If you catch yourself writing a prompt that asks "is this query safe?" or "should this be allowed?",
that is this rule. It is also enforced at the import level — see the second contract in R5, which
makes it impossible for `app.sqlpolicy` to consult a model even by accident.

### R5 — Import direction, and the four contracts that enforce part of it

The intended layering, from `TARGET_ARCHITECTURE.md` §4:

```
platform  ←  llm, embeddings, catalog, retrieval, sqlpolicy, execution
          ←  graph, analysis, runs, jobs, evaluation
          ←  api  ←  main
```

Four of the security-relevant edges are machine-checked. The contracts live in `pyproject.toml`
under `[tool.importlinter]` and CI runs `uv run lint-imports`; each one is the structural form of a
claim this project makes:

| Contract | What it forbids, and why |
|---|---|
| The model layer cannot reach a database | `app.llm` and `app.embeddings` may not import `app.execution`, `db`, `sqlalchemy`, `psycopg`, `pymysql`, `sqlite3` or `pyodbc`. A provider that *could* open a connection is one prompt injection away from being asked to. |
| The policy engine decides on its own | `app.sqlpolicy` may not import `app.execution`, `app.llm`, `app.graph`, `app.api`, `app.agent` or `db`. This is R4 expressed structurally: the verdict is a pure function of the statement and the schema scope, which is also what makes the adversarial corpus mean anything. |
| No credential reaches the graph | `app.graph`, `app.analysis` and `app.retrieval` may not import `db.database_manager` or `app.platform.connection_secrets`. Graph state is checkpointed to disk; `tests/graph` asserts the runtime half, this is the structural half. |
| Observability is a leaf | `app.observability` may not import the packages it instruments. Instrumentation that imports its subject causes cycles and tempts someone to put a result row on a span. |

The third contract caught a real violation the day it was added: crediting verified-example usage had
the retrieve node importing `db.verified_queries`, so the graph reached the application database
directly. That is what a contract is for — the review had already passed.

The rest of the layering — nothing imports `api` or `main`, `graph` reaches the database only through
`execution.service` — is not yet expressed as a contract and is still a review rule. Adding one is
welcome; adding one that has to be immediately relaxed is not.

### R6 — Data, secrets and audit

* **Every schema change ships an Alembic migration** in `db/migrations/versions/`. Persisted data is
  migrated, never dropped. Never edit a revision that has already been applied somewhere.
* **Never store or log a plaintext credential.** Target DSNs are encrypted at rest via
  `app/platform/secrets.py` (MultiFernet, key held outside the database). Generate a key with
  `uv run python -m app.platform.secrets generate-key`.
* **Audit events record the statement *class* and the rules that fired — never the SQL or its
  identifiers** (`app/platform/audit.py`). If you add an audit event, keep that property.
* **v1 route contracts do not change.** New behaviour goes under `/v2`.

---

## 5. The three extension points

### Adding a database dialect

In this order. The last two are the ones people skip.

1. `app/sqlpolicy/types.py` — add the `Dialect` member. **Its value is the sqlglot dialect name**
   (`sqlglot_name` returns `self.value`), so it must match what sqlglot expects. Add aliases to
   `Dialect.from_db_type` for the spellings a user might put in a connection string, and a
   `default_schema` if the engine has one.
2. `app/sqlpolicy/rules.py` — dialect entries in `BLOCKED_FUNCTIONS` and `BLOCKED_FUNCTION_PREFIXES`.
   The `GENERIC` entry applies to every dialect and is merged with yours; add only what is specific
   to the new engine (file, network, shell, sleep and session-security functions).
3. `app/sqlpolicy/limits.py` — only if the dialect does not spell row limits as `LIMIT`. The
   `Dialect.MSSQL` branch (`TOP` / `OFFSET … FETCH`) is the worked example.
4. `app/execution/connections.py` — `_connect_args` for the driver's connect timeout, and
   `read_only_setup`, which returns the statements that put a session into read-only mode with a
   bounded runtime.
5. `app/execution/readonly.py` — a `_check_<dialect>` probe returning the tri-state report, wired
   into `verify_read_only`.
6. `app/evaluation/datasets/adversarial/sql_policy_cases.yaml` — add the dialect to the existing
   cases where the syntax applies, and add its own write, admin and file-access forms. A dialect with
   no corpus coverage is a dialect whose policy rules have never been tested.
7. `pyproject.toml` — the driver. Check its licence against §1 rule 3 *before* adding it, and record
   it in `DEPENDENCY_AND_LICENSES.md` §3.

Then: `uv run pytest tests/sqlpolicy tests/test_execution_service.py`.

**The one thing not to do:** return `ReadOnlySetup(enforced=True, …)` or a "safe" read-only report
for an engine whose behaviour you have not verified. `read_only_setup` returns `False` with an
explanation for an unknown dialect, and `verify_read_only` is tri-state precisely so that "the probe
failed" stays distinguishable from "the connection is read-only". `SECURITY.md` publishes a
per-engine enforcement table; a false row there is worse than a missing one, and enrollment fails
closed on an unverified credential by design — a bug fixed during this program was that it used to
fail open.

### Adding a model provider

1. `app/llm/providers/<name>.py` implementing the `ModelProvider` protocol in `providers/base.py`:
   `complete(request, profile)` and `health()`. Three obligations are written into that protocol and
   are what review checks — raise `ProviderError` with a normalised `FailureKind` rather than a raw
   SDK exception (the router classifies on the failure kind, not on error strings); report the model
   that *actually answered*, since aliases and server-side routing make it differ from the one
   requested; and leave token usage at zero when the backend does not report it rather than
   estimating and presenting a guess as a measurement. `health()` must not raise.
2. `app/llm/registry.py` — a `ModelProfile` in `LOCAL_PROFILES` or `REMOTE_PROFILES`, with its
   capabilities, context window and `quality_hint`. The hint is a hint: which profile becomes a
   default is decided by an evaluation run on the target hardware, not by this number.
3. `app/llm/registry.py::PROVIDER_KEY_ENV` — if it needs a key. This is the mechanism that makes a
   remote provider disable itself when the key is absent, so `enabled_profiles()` never offers
   something that cannot work.
4. `app/llm/service.py::build_provider` — the only place in the codebase that constructs a provider.
   Nothing above it knows which backend answered, which is what makes swapping one a config change.
5. `tests/llm/test_providers.py` — wire-level tests against a fake HTTP transport. No network, ever.
   The existing Ollama tests are the pattern, including the one that asserts no API key is sent.

Two constraints. `MODEL_PROFILE=fake` must continue to yield **only** the fake profile — that is what
keeps CI offline. And a remote provider is subject to `EgressPolicy` (`app/platform/modes.py`), which
governs what may leave the deployment: under `LOCAL_ONLY` no remote provider may be used at all.

### Adding an evaluation case

An evaluation case is a *contract*, not a question-and-answer pair.

1. Pick the dataset file: `app/evaluation/datasets/cases/{retail,saas_ops,synthetic_snf}.yaml`.
2. Write the case. `EvalCase` (`app/evaluation/schema.py`) validates on construction rather than
   letting a half-specified case score as a pass: `expected_behavior=answer` requires `gold_sql`,
   `clarify` requires `expected_clarification`, `refuse` requires `safety_category`.
3. **Do not hand-write `expected_tables`, `expected_columns` or `expected_relationships`.** They are
   parsed out of `gold_sql` at load time; a hand-maintained copy drifts the first time someone edits
   the query. Add them explicitly only to assert something the reference SQL does not show.
4. Tag it. Tags feed the coverage minimums asserted in `tests/evaluation/test_datasets.py`:
   `answerable` ≥ 100, `multi_table` ≥ 30, `time_range` ≥ 20, `aggregation` ≥ 20,
   `conversational` ≥ 15, and ≥ 15 ambiguous cases.
5. Run it. `tests/evaluation/test_gold_sql.py` is what makes the corpus a benchmark rather than a
   wish list: every `answer` case's reference query must parse, pass the real policy engine against
   the real enrolled scope, execute against the generated fixture, return rows, and produce a stable
   result across two executions — and every `refuse` case's statement must be *denied* by that same
   engine, because a safety case the engine would have allowed is testing nothing.

```bash
uv run pytest tests/evaluation
bash scripts/eval-smoke.sh              # ./scripts/eval-smoke.ps1 on PowerShell
```

A new *schema* rather than a new question means a generator in `app/evaluation/datasets/` plus
`uv run python -m app.evaluation.cli build` to regenerate the fixtures and print their digests.
Fixtures are generated deterministically from code and every row is fabricated; no dataset may
contain a real person, organisation, transaction or clinical record, and
`tests/evaluation/test_datasets.py` asserts that each one declares itself synthetic.

**Adversarial policy cases go somewhere else**:
`app/evaluation/datasets/adversarial/sql_policy_cases.yaml`, executed by
`tests/sqlpolicy/test_adversarial_corpus.py` against every dialect the case lists. Adding a case is
how a bypass or a false positive gets recorded. **Never delete a case that once caught a
regression** — mark it with `skip_reason` instead.

---

## 6. Claims, docs and marketing copy

If your PR touches user-visible wording — README, the marketing page, `SECURITY.md`, `.env.example`,
UI copy, a badge — `docs/v2/CLAIM_AUDIT.md` governs it. Its §1.3 checklist, condensed:

* **No absolutes.** *always*, *never*, *cannot*, *guaranteed*, *any*, *all* do not appear in a safety
  or capability claim unless a named test enumerates the whole space.
* **No "proves" / "proven" / "provably"** for a structural filter. The policy engine *checks*,
  *rejects*, *admits*, *classifies*. A parser-based filter is bounded by the parser's fidelity, and
  that is not a proof.
* **No banned adjectives:** production-ready, enterprise-grade, bulletproof, military-grade,
  bank-grade, hardened, unbreakable.
* **No percentage without a numerator and a denominator** in the same sentence or its footnote.
  "100%" alone is banned outright; "343 of 343" is fine.
* **Every metric carries provenance:** dataset and version, n, model, prompt version, date,
  execution environment, exclusions.
* **Every metric is reproducible from a clean checkout**, or it is labelled as not reproducible and
  dated.
* **Name the mechanism, not the outcome.** "A policy engine rejects writes before execution" beats
  "your data is safe".
* **State scope where behaviour varies by engine.** Read-only enforcement differs between
  PostgreSQL/MySQL/SQLite and SQL Server; say so rather than averaging.
* **No claim about deployed state** in a document that ships in the repo, unless it is framed as "the
  hosted demo at X" and can break without making the repo wrong.

A new number reaches the UI **last**, in the order set out in §5 of that document: measure it with a
harness committed inside the repository; make it re-runnable by a stranger; check what the metric
*actually* measures (a total outage or an empty result must not score as a pass); record the
provenance block; add the row to the audit with a `path:line`; run the checklist; only then publish,
and make the published text cite the reproducing command.

Your PR should also re-check the audit rows it expires. The documented re-validation triggers are:
the prompt version (`app/agent/prompt.py`), `RULESET_VERSION` / `POLICY_VERSION`
(`app/sqlpolicy/rules.py`, `app/sqlpolicy/engine.py`), the corpus version, the provider list, the
endpoint set, and any dependency that changes dialect parsing (sqlglot).

The best documentation practice in this repository is the retired-version history at the top of
`app/agent/prompt.py`: it records what `@1.0` did, why it was wrong, and when it was retired. That
history is the only reason the staleness of a headline metric could be established at all. Copy the
pattern when you retire something.

---

## 7. Commits, branches and pull requests

**Conventional commits**, lowercase subject, scope where it clarifies. Across the last 60 commits:
`feat` (24), `chore` (8), `ci` (7), `docs` (6), `fix` (5), `refactor` (1), with scopes like `(web)`,
`(security)`, `(auth)`, `(v2)`, `(config)`, `(readme)`, `(deps)`.

```
fix(security): close path traversal through the data-source identifier
ci(security): satisfy the bandit gate instead of lowering it
docs(readme): count API operations, not paths
```

**The body carries the why.** Read `923ba38` for the house style: sections, plain prose, and the
things a reviewer would otherwise have to reverse-engineer — including the defects being fixed and
what was deleted and why. "Deleted `app/security/db_readonly_checker.py`: a weaker duplicate of a
security control is how the wrong one gets imported" is the level of explanation expected. A commit
message that only restates the diff has not been written yet.

**Branches.** Work on a branch; do not commit to `main`. The v2 program branch is
`feat/dbwhisper-v2`.

**Pull requests.** `.github/pull_request_template.md` carries the checklist. Two lines of it have
drifted behind the pipeline: it says migrations are needed "once Alembic lands" (Alembic landed in
Phase 1 — migrations are required now), and its ruff invocation omits `scripts`, so it lints less
than CI does. Use the commands in §3, not the ones in the template.

CI must be green before review. If a gate is failing for a reason unrelated to your change, say so in
the PR rather than working around it, and do not relax the gate to get past it.

**Comments and docstrings explain WHY.** This codebase's comments justify decisions —
`app/execution/connections.py` explains why SQLite gets a connection-level pragma instead of a
transaction-level one, `ci.yml` explains why `--follow-imports=silent` is on the mypy gate. A comment
that restates the line below it will be asked to be deleted.

---

## 8. Known gaps in this process

Stated so nobody discovers them the hard way in review:

* **R1 and R5 are not tool-enforced.** `import-linter` is a dev dependency and the architecture
  document specifies contracts, but no contract file exists in the repository. Both rules are held up
  by review today.
* **The mypy ratchet has no headroom.** The measured count equals the budget exactly (200/200), so
  any new type error fails the build. That is the design, but it means an unrelated refactor can
  block you; fix the errors rather than raising the budget.
* **`pip-audit`, Trivy and `npm audit` do not block.** An advisory in a transitive pin will sit in
  the logs until a dependency bump moves it.
* **`postgres-integration` only runs in CI** unless you bring your own pgvector container, so an
  Alembic revision that works on SQLite can still fail on PostgreSQL after you push.
* **The coverage floor is far below actual coverage**, on purpose. Do not read 65% as the standard.

---

## Verification

Everything above was executed on this branch on **2026-08-24**, Windows 10, from the repository root
unless stated otherwise: uv 0.11.6, Python 3.13.13, Node 24.14.1, npm 11.11.0.

| Command | Result |
|---|---|
| `uv sync --frozen --dry-run` | 175 packages checked, no changes; `uv pip list` shows no torch, so the extra is genuinely not installed |
| `uv lock --check` | resolved 208 packages, lockfile current |
| `uv run ruff check app db tests scripts run.py` | All checks passed |
| `uv run ruff format --check app db tests scripts run.py` | 213 files already formatted |
| `uv run mypy --follow-imports=silent app/embeddings app/analysis app/observability db/migrate.py` | Success: no issues found in 16 source files |
| `uv run mypy app db 2>&1 \| grep -cE 'error:'` | 200 (budget 200) |
| `uv run pytest --cov=app --cov=db --cov-fail-under=65` | 2318 passed, total coverage 79.22% |
| `uv run pytest tests/sqlpolicy` / `tests/test_wiring.py` / `tests/test_paths.py` | 616 / 24 / 102 passed |
| the suite with the repo `.env` outside the dotenv search path | 2316 passed; the only two failures were the CWD-relative retrieval tests noted in §2 |
| `cd web && npm run lint / typecheck / test / build / e2e` | no ESLint warnings; `tsc --noEmit` clean; 108 Vitest tests passed; build succeeded; 6 Playwright specs passed |
| `bash scripts/eval-smoke.sh` | exit 0 — `169 cases \| unsafe executions 0 \| execution accuracy 135 of 135 \| behaviour 167 of 169 [oracle - not a model measurement]` |
| `uv run python -m app.platform.secrets --help` | `usage: python -m app.platform.secrets generate-key` |
| `uv run pre-commit --version` | 4.6.0 (the git hook itself was not installed on this machine) |

**Not run in this session**, and therefore not vouched for here: `npm ci` (it would have wiped a
working `node_modules`), the `postgres-integration` job (no container available), gitleaks, bandit,
pip-audit, Trivy and CodeQL (each needs a tool download or GitHub's runner). Their behaviour above is
described from `.github/workflows/ci.yml`, which is the authority on what blocks and why.
