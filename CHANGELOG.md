# Changelog

Notable changes to DBWhisper. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/), with the caveat that this project is
pre-1.0 and the `0.x` line is where breaking changes are allowed to live.

Dates are UTC. Where an entry cites a number, the number was measured on the date given and the
command that produced it is named — see `docs/v2/CLAIM_AUDIT.md` for why that rule exists.

---

## [0.2.0] — 2026-08-24 · the v2 program

The largest change since the project started. The theme: **safety and correctness became properties
of the code path rather than of the prompt**, and every public claim was re-derived from the code.

### ⚠️ Breaking changes

Read these before upgrading. Each one names what breaks and what to do.

**1. Enrolling a database now requires `DBW_SECRET_KEYS` in `self_hosted` and `production` mode.**

Target-database connection strings are encrypted at rest (Fernet, key held outside the database).
Without a key, `POST /schemas/enroll` and `POST /v2/connections/{db_flag}/enroll` fail rather than
silently storing a plaintext DSN. In `demo` mode encryption is not required, because arbitrary
connections are not permitted there either.

```bash
uv run python -m app.platform.secrets generate-key   # → DBW_SECRET_KEYS=<key>
```

Existing plaintext rows keep working: reads prefer the encrypted column and fall back to the legacy
one. A startup pass moves them across, and `app.platform.connection_secrets.encrypt_existing_rows()`
can be run by hand. The legacy column is left in place for one release so a rollback is survivable.
Rotation (`MultiFernet`: the first key encrypts, all keys decrypt) is documented in
`docs/OPERATIONS.md` §4.

**2. `EMBEDDING_PROVIDER` is deprecated in favour of `EMBEDDING_PROFILE`.**

`EMBEDDING_PROVIDER` is still read, mapped onto the new setting, and logs a deprecation warning. It
will be removed in a later release.

| Old | New | Notes |
|---|---|---|
| `EMBEDDING_PROVIDER=huggingface` | `EMBEDDING_PROFILE=auto` | local CPU model via fastembed/ONNX — **now the default**, no API key |
| `EMBEDDING_PROVIDER=google` | `EMBEDDING_PROFILE=google` | hosted; still needs `GOOGLE_API_KEY`/`GEMINI_API_KEY` |
| — | `EMBEDDING_PROFILE=fake` | deterministic and offline; what CI uses |
| — | `EMBEDDING_PROFILE=local-bge-small` | pin the local model explicitly |

Vectors are version-stamped, so embeddings produced by different models can no longer be silently
mixed in one collection. **Changing the embedding profile on an existing deployment means re-running
the embedding stage.**

**3. The container image no longer defaults to hosted embeddings.**

`Dockerfile` previously set `EMBEDDING_PROVIDER=google`; it now sets `EMBEDDING_PROFILE=auto`, so
`docker run` works with no credentials at all. The cost is a one-time model download (a few tens of
MB) into `FASTEMBED_CACHE_PATH` on first use. Deployments that want the hosted provider must now set
`EMBEDDING_PROFILE=google` explicitly — including `render.yaml`, which still names the deprecated
variable.

**4. `compose.yaml` was rewritten around profiles, and service names and ports changed.**

- `docker compose up` starts nothing on its own. Use `docker compose --profile core up -d`.
- The application database service is `postgres`, not `db`.
- Its published host port defaults to **55432**, not 5432, because 5432 is usually taken. Override
  with `DBW_POSTGRES_PORT`; every published port now has a `DBW_*_PORT` override.
- New profiles: `local-ai` (Ollama), `targets` (sample MariaDB and PostgreSQL to point DBWhisper at),
  `observability` (OpenTelemetry collector, Jaeger, Prometheus, Grafana).
- New services in `core`: a `pgvector-init` one-shot that creates the extension on every `up`, and a
  `migrate` one-shot that applies Alembic before the API and worker start.
- `env_file: .env` is gone. Compose interpolates from `.env` instead, so a clean clone with no `.env`
  starts rather than erroring.

**5. `X-Forwarded-For` and `X-Real-IP` are no longer trusted by default.**

`TRUST_PROXY_HEADERS` was declared but never read, so proxy headers were trusted unconditionally and
per-IP rate limiting could be evaded by spoofing one. The setting is now honoured and defaults to
`false`. **If you run behind a reverse proxy you control, set `TRUST_PROXY_HEADERS=true`** or every
client will be rate-limited as the proxy's single IP.

**6. Enrollment now fails closed on the read-only privilege probe.**

Previously the probe's result was initialised to "read-only OK" and left that way when the probe
raised, so a connection whose privileges could not be established was enrolled. It is now tri-state
and an inconclusive probe refuses the enrollment with HTTP 400. Connections that used to enroll may
now be rejected — that is the fix, not a regression. Grant the role `SELECT` and nothing else.

**7. Internal modules removed.** `app/core/query_executor.py` (dead since the execution service
landed), `app/schema_pipeline/user_database_manager.py`, `app/utils/token_tracker.py`,
`app/security/db_readonly_checker.py`. Anything importing these must move to `app.execution.service`
and `app.execution.readonly`.

**8. Dependencies removed:** `matplotlib`, `psycopg2-binary`, `mysql-connector-python` (GPL —
replaced by `pymysql`), `langchain-community`, `groq`. If your fork imported any of them, add it
back explicitly.

### Added

- **`app/sqlpolicy/` — a SQLGlot AST policy engine** (`sql_policy@2.0.0`) replacing the keyword
  heuristics, which are retained as an independent second layer where the stricter decision wins.
  Three outcomes — allow, deny, `needs_approval`. Regression-tested against a 255-case adversarial
  corpus expanded into 577 case×dialect combinations (343 deny, 210 allow, 24 needs-approval) across
  PostgreSQL, MySQL, SQL Server and SQLite. Reproduce with `uv run pytest tests/sqlpolicy`.
- **`app/execution/` — a single execution path.** Policy is re-evaluated on every call, a
  caller-supplied decision counts only as an approval token whose fingerprint must match, the target
  host is checked against an SSRF policy, and the query runs in a read-only transaction that is
  rolled back. Read-only sessions are enforced by the database on PostgreSQL, MySQL and SQLite;
  SQL Server has no session-level read-only mode and reports `enforced=False`.
- **`app/llm/` — capability-based model routing** with a versioned profile registry, a circuit
  breaker per provider, normalised failure classes, an Ollama provider over plain HTTP (no SDK, no
  key), a deterministic fake provider, and structured output with bounded repair.
- **`app/embeddings/`** — fastembed/ONNX on the CPU by default, fake for CI, optional hosted.
- **`app/retrieval/`** — hybrid BM25 + vector + exact retrieval fused with Reciprocal Rank Fusion,
  join-graph expansion, parent-table promotion and a token-budgeted context pack.
- **`app/graph/`** — the query graph as one LangGraph `StateGraph` with typed state, bounded repair
  that re-enters validation, and **real interrupts**: clarification and approval pause the run
  durably and resume after a restart. Plus an investigation subgraph with a bounded plan and
  evidence-linked answers.
- **`app/analysis/`** — deterministic shape verification, chart selection and a statistics-only
  summary fallback.
- **`app/platform/`** — `AppMode` (`demo` / `self_hosted` / `production`) as one immutable policy
  object per mode, Fernet secrets at rest, and an SSRF network policy.
- **`app/observability/`** — Prometheus metrics on a dedicated registry with a label-cardinality
  guard, and OpenTelemetry spans behind a closed attribute allowlist. `GET /metrics` now exists;
  `METRICS_ENABLED=false` returns an honest 404 rather than an empty body.
- **`app/jobs/`** — a background worker (`python -m app.jobs.worker`) with leased jobs, so enrollment
  and embedding no longer run synchronously inside an HTTP request.
- **`app/evaluation/`** — the evaluation harness, **in the repository**, with three synthetic
  datasets, a gold-SQL oracle, multiset scoring, and a provenance block that refuses to be rendered
  with holes in it. `python -m app.evaluation.cli {build,smoke,custom,safety,retrieval,report}`.
- **`/v2` API** — `POST /v2/query`, `GET /v2/runs/{run_id}`, `POST /v2/runs/{run_id}/resume`,
  `POST /v2/investigate`, `POST /v2/investigations/{run_id}/resume`,
  `POST /v2/connections/{db_flag}/enroll`, `GET /v2/jobs/{job_id}`, `GET /v2/models/health`.
- **Alembic migrations** (`db/migrations/`, currently revision `0004`) replacing implicit
  `create_all`, and `alembic.ini` is now **copied into the container image** — without it
  `upgrade_to_head` raised inside the container and the `create_all` fallback hid it, so the image
  started healthy having never run a migration.
- **`Dockerfile.worker`** for the background worker, and a `PORT`-aware entrypoint and healthcheck so
  the same image serves Hugging Face Spaces (7860), compose (8000) and Render (`$PORT`).
- **Documentation**: `docs/ARCHITECTURE.md`, `docs/LOCAL_SETUP.md`, `docs/OPERATIONS.md`,
  `docs/EVALUATION.md`, this changelog, and the five audit documents in `docs/v2/`.

### Changed

- **CI restructured** into `backend`, `frontend`, `postgres-integration`, `eval-smoke`, `security`
  and `codeql`. The coverage floor rose from **35% to 65%**. `mypy app db || true` — which swallowed
  every error — was replaced by a real gate on the packages that are clean plus a ratchet on the
  total so the debt can shrink but not grow. New: a PostgreSQL service container that exercises
  Alembic and pgvector for real, an evaluation smoke gate, bandit, Trivy and CodeQL. No model is
  downloaded in CI.
- **The prompt was rewritten and versioned.** `sql_agent_prompt@1.0` was hardcoded to a single SQL
  Server schema and used for *every* enrolled database; it was retired 2026-08-21. Current versions
  are recorded in every evaluation run's provenance block.
- **The test suite runs fully offline**, pinned to the fake model and embedding providers in
  `tests/conftest.py`.
- **`.env.example` now documents the variables read through `os.getenv` rather than `Settings`** —
  `PROJECT_DB_CONNECTION_STRING` in particular, which `db/database_manager.py` prefers over
  `POSTGRES_CONNECTION_STRING` and which was documented nowhere — along with `DBW_ALEMBIC_URL`,
  `LOG_SANITIZE` and the `TOOL_CACHE_*` / `TOOL_RATE_LIMIT_*` knobs. Three are still undocumented as
  of this release: `GOOGLE_API_KEY`, `CSRF_ENFORCED` (also readable as `DBW_CSRF_ENFORCED`) and
  `DBW_EVAL_FIXTURE_DIR`.
- **`app/main.py` reports version `0.1.0`**, matching `pyproject.toml`. It previously claimed `1.0.0`.

### Fixed

- **`_mark_schema_extracted` was unreachable on the normal path** — it sat inside the `else` of
  `if run_embeddings:`, so the flag was never set when embeddings ran. Every re-enroll therefore
  repeated extraction, per-table LLM documentation and re-embedding.
- The enrollment read-only guard failing open (breaking change 6 above).
- `TRUST_PROXY_HEADERS` being ignored (breaking change 5 above).
- Migrations being impossible inside the container image (missing `alembic.ini`).
- A race where the worker's `ensure_tables()` reached its foreign key to `users` before the API had
  created it. Compose now serialises the schema change through a `migrate` one-shot.

### Removed

- The **82% execution-accuracy** and **100% fail-closed** figures. Four independent disqualifiers on
  the first (untracked harness, dead endpoint, deleted prompt, set-based scoring that collapsed
  duplicate rows) and a wrong measurement on the second: its scoring predicate required a query to be
  valid *and* successful before it could register a failure, so an outage scored as a pass, and in
  every recorded unsafe case nothing had reached the validator at all. `docs/v2/CLAIM_AUDIT.md` §4.4
  has the full post-mortem. The replacement is the policy corpus, which anybody can reproduce.
- Claims about scaling to "hundreds of tables". No measurement exists at any scale; the largest
  enrolled schema in this repository has 16 tables. Deleted rather than softened.

### Known limitations

Stated here so they are not discovered later.

- **SQL Server has no session-level read-only mode.** The controls there are the policy engine, a
  least-privilege login and the driver timeout.
- **`mypy app db` reports 200 error lines** (measured 2026-08-24). CI gates the four packages that
  are clean and ratchets the total.
- **v1 `/query` executes without asking.** The `needs_approval` decision and the approval interrupt
  exist in the v2 graph; v1 still generates, validates and executes in one round trip.
- **The container image is ~920 MB** (measured 2026-08-24), dominated by the ONNX embedding runtime
  and the Microsoft ODBC driver.
- **`pip-audit` reports advisories** in transitive pins the lockfile cannot move without a dependency
  bump. It runs on every build and weekly, report-only, and Dependabot raises the fixes.

---

## [0.1.0] — pre-2026-08-21

The pre-v2 baseline: a tool-calling LangChain agent with keyword-based SQL validation, synchronous
schema enrollment, pgvector retrieval, provider fallback across six LLMs, a Next.js console, and CI
running ruff, pytest and gitleaks. `docs/v2/CURRENT_STATE_AUDIT.md` describes it in detail, including
the parts of it that did not work.
