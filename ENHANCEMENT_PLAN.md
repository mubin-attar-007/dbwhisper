# DBWhisper — Enhancement Plan (Productionization Roadmap)

> Goal: bring **dbwhisper** (the NL→SQL "SQL Insight Agent") up to the same production standard as
> **crownwager** and **tradepulse** — versioned on GitHub, CI-gated, containerized, and deployed free on
> Hugging Face + Vercel + Neon + Upstash. Aligned to `crownwager/docs/MODERNIZATION_PLAYBOOK.md`.
>
> **Status: PLAN ONLY — nothing here is built yet. Review and adjust before we execute.**

---

## 0. Where dbwhisper stands today (assessment)

Unlike crownwager (a legacy monolith that needed a full rebuild), **dbwhisper's core engine is already
clean and well-structured** — this is a *"Reuse as-is + add the shell"* case, not a rewrite.

**What's genuinely good (keep it):**
- FastAPI app with clear endpoints: `/query`, `/schemas/enroll`, `/schemas/embeddings`, `/health`, `/chat`.
- Layered design: `app/agent/` (LangGraph agent), `app/core/` (query_executor, result_formatter,
  sql_validator, retriever), `app/schema_pipeline/` (introspect → document → embed), `app/security/`
  (read-only connection checker), `db/` (manager, conversation_memory, langchain_memory).
- Real depth: **6-provider LLM fallback** (Groq, Gemini, OpenAI, DeepSeek, Anthropic, OpenRouter),
  **PGVector** semantic schema retrieval, Postgres-backed conversation memory, log sanitization,
  read-only safety enforcement before executing SQL. This matches the playbook's "graceful degradation"
  and "security-first" principles already.

**What's missing vs the blueprint (the work):**
| Blueprint piece | dbwhisper today |
|---|---|
| Git + GitHub repo | ❌ no `.git` at all — never versioned/pushed |
| Deploy (HF / Vercel / Render) | ❌ no Dockerfile / render.yaml / compose |
| GitHub Actions CI | ❌ none |
| ruff / mypy / pytest config | ❌ `pyproject.toml` has deps only, no `[tool.*]` |
| Tests | ❌ zero test files (pytest configured in VS Code, but `tests/` doesn't exist) |
| pre-commit + secret scanning | ❌ none |
| `.env.example` | ❌ missing; live `.env` holds **real API keys in plaintext** |
| docs/ + SECURITY.md | ❌ missing |
| Alembic migrations | ❌ raw `create_metadata_tables()` |
| Real frontend | ⚠️ only a static dev `chat.html/js` (no Next.js app) |
| Naming | ⚠️ pyproject `name = "mysql-agent"`; old folder was `SQL_SERVER_AGENT` |

---

## Phase 1 — Local stabilization & hygiene  *(gate: app boots, `/health` 200, no secrets stageable)*

- [ ] **Rebuild `.venv`** — DONE (was pointing at old user `C:\Users\SPC6\...`; recreated via `uv sync`).
- [ ] **Rename the project** to `dbwhisper`:
  - `pyproject.toml` `name = "mysql-agent"` → `name = "dbwhisper"`.
  - Optional: keep `prompt = "dbwhisper"` in the venv; update README title/headers.
- [ ] **Fix `.gitignore`** — current file ignores **`*.yaml`**, which would silently drop
  `database_schemas/**/*.yaml`, `docker-compose.yaml`, `render.yaml`, CI/pre-commit yaml, etc.
  Replace with a targeted ignore (`.env`, `.venv`, `__pycache__/`, `Log/`, `Temp/`, caches) and
  **un-ignore** the yaml we need to commit.
- [ ] **Secrets remediation (do BEFORE any git push):**
  - Create **`.env.example`** with every key as a placeholder + comments (mirror tradepulse's).
  - Confirm `.env` is gitignored (it is).
  - **Rotate** the `GROQ_API_KEY` and `GEMINI_API_KEY` currently in `.env` if that file was ever shared
    (since there's no git history yet, exposure risk is low — rotation is precautionary).
- [ ] **Clean stragglers:** decide on `Temp/`, `Log/`, `__pycache__/` (gitignore), and the `chat.js.backup`.

## Phase 2 — Quality tooling  *(gate: `ruff`, `mypy`, `pre-commit` all green)*

- [ ] Add `[tool.ruff]` (line-length 100, py313, select `E,F,I,UP,B,C4,SIM,ASYNC,RUF`) to `pyproject.toml`.
- [ ] Add `[tool.mypy]` (pydantic plugin; `ignore_missing_imports` overrides for `langchain.*`, `torch`,
      `pyodbc`, `transformers.*`, etc.) — start lenient, tighten over time.
- [ ] Add `[tool.pytest.ini_options]` (`testpaths = ["tests"]`, asyncio auto).
- [ ] Add `[dependency-groups] dev` = ruff, mypy, pytest, pytest-asyncio, httpx.
- [ ] Add **`.pre-commit-config.yaml`**: trailing-whitespace, end-of-file-fixer, check-yaml/toml,
      check-added-large-files, **ruff + ruff-format**, **gitleaks** (secret scan).
- [ ] Add `.editorconfig` (match crownwager: Python 4sp, LF, UTF-8).
- [ ] First clean pass: `ruff check --fix` + `ruff format` across `app/` and `db/`.

## Phase 3 — Tests  *(gate: `pytest` green in CI)*

- [ ] Create `tests/` with a `conftest.py` + FastAPI `TestClient`.
- [ ] Start with high-value, no-external-dependency units:
  - `sql_validator` (read-only enforcement, blocks DDL/DML) — security-critical, easy to test.
  - `db_readonly_checker` logic.
  - `_sanitize_sql` / `_extract_agent_output` in `app/main.py` (pure string functions).
  - `result_formatter` output shapes.
  - `/health` endpoint returns 200.
- [ ] Mock the LLM providers (no live keys in CI) to test the fallback ordering in the `/query` loop.

## Phase 4 — Git + GitHub  *(gate: repo on GitHub, history clean of secrets)*

- [ ] `git init` (set identity: `Mubin Attar` / sk.mubinattar@gmail.com to match the others).
- [ ] Verify gitleaks finds **no** secrets in the working tree before first commit.
- [ ] Initial commit → push to new repo **`github.com/mubin-attar-007/dbwhisper`** (private to start).
- [ ] `uv run pre-commit install` so hooks run locally going forward.

## Phase 5 — CI  *(gate: green check on push/PR)*

- [ ] `.github/workflows/ci.yml`: setup `uv` (Python 3.13) → `ruff check` + `ruff format --check` →
      `mypy app` → `pytest` → gitleaks history scan. (Mirror tradepulse's `ci.yml` shape.)
- [ ] (Optional) `.github/dependabot.yml` weekly for pip + Actions + Docker.

## Phase 6 — Containerize  *(gate: `docker build` succeeds; container serves `/health`)*

- [ ] **`Dockerfile`** (HF-ready): multi-stage `python:3.13-slim`, `uv sync --frozen --no-dev`,
      non-root user, `EXPOSE 7860`, healthcheck, `CMD uvicorn app.main:app --host 0.0.0.0 --port 7860`.
  - ⚠️ **MSSQL gotcha:** `pyodbc` needs the system **msodbcsql18 + unixODBC** drivers installed in the
    image (apt). Must add these or MSSQL targets fail at runtime.
  - ⚠️ **Image size:** `torch` + `sentence-transformers` make a multi-GB image. See decision **D1** below.
- [ ] **`compose.yaml`** for local: app + a Postgres-with-pgvector service (e.g. `pgvector/pgvector:pg16`).
- [ ] **`render.yaml`** blueprint (alternative host), envs `sync:false` for secrets.

## Phase 7 — Deploy free (HF + Neon + Upstash)  *(gate: live URL answers a NL query end-to-end)*

- [ ] **Neon**: project + enable `vector` extension → `POSTGRES_CONNECTION_STRING` (project/memory DB).
- [ ] **(If used) Upstash**: only if we add Redis caching/sessions — current app uses Postgres for memory,
      so Redis may be optional for v1 (note **D3**).
- [ ] **Hugging Face Space** (Docker): `README.md` HF frontmatter (`sdk: docker`, `app_port: 7860` as the
      *very first* lines) → create Space `heisenbergblue/dbwhisper` → `git remote add space …` → push →
      set all secrets in Space → Settings → Variables.
- [ ] Verify `/health`, then a real `/query` against an enrolled sample DB.

## Phase 8 — Frontend (optional, full parity)  *(gate: `next build` green; chat works against live API)*

- [ ] Promote the static `app/static/chat.*` into a **Next.js + TS + Tailwind** app under `web/`
      (or `frontend/`), typed API client, deploy to **Vercel**, set CORS to the exact Vercel origin.

## Phase 9 — Hardening  *(gate: prod guards + quality gates all green)*

- [ ] Replace `allow_origins=["*"]` CORS with an env-driven allowlist (currently wide-open in `main.py`).
- [ ] Env-driven config via **pydantic-settings** (replace scattered `os.getenv`); fail-fast in prod.
- [ ] Rate-limit the `/query` and `/schemas/*` endpoints (LLM cost protection).
- [ ] Reconcile the README with reality (it currently references a `tests/` dir and CI that don't exist).
- [ ] Add `SECURITY.md` + confirm `LICENSE` header in README.

---

## Open decisions (need your input before we build)

- **D1 — Embeddings: bundle vs API.** Keep local `sentence-transformers`+`torch` (fully free/offline, but
  multi-GB image + slow cold start on HF free tier) **or** switch schema embeddings to a hosted API
  (Gemini/HF Inference — tiny image, needs a key). *Recommendation: API for the deployed image, keep local
  as a fallback — matches the playbook's "graceful degradation."*
- **D2 — Demo target database.** `/query` runs against an *enrolled user DB*. For a public demo we need a
  sample read-only DB (e.g. a small Neon/Postgres sample, or a bundled SQLite). Which DB should the live
  demo showcase?
- **D3 — Redis/Upstash now or later.** Current memory uses Postgres, so Redis isn't strictly required for
  v1. Add Upstash only when we introduce caching/rate-limit buckets? *Recommendation: defer to Phase 9.*
- **D4 — Frontend scope.** Ship v1 with just the existing static chat UI (fastest to live), or go straight
  to the Next.js + Vercel frontend for full parity?
- **D5 — Repo visibility.** Private first (like the others) or public from the start?

## Rough effort (with me doing the work, you handling auth'd push/Space creation)
- Phases 1–5 (foundation → GitHub → CI): ~half a day of iterations.
- Phase 6–7 (Docker → live on HF): ~half a day, plus your dashboard steps for Neon/HF secrets.
- Phase 8 (Next.js frontend): +1 session if we want full parity.

---

### dbwhisper-specific gotchas (pre-empt them)
- `pyodbc`/MSSQL needs OS ODBC drivers in Docker (msodbcsql18 + unixODBC).
- pgvector requires the `vector` extension enabled on Neon.
- HF frontmatter must be the literal first lines of `README.md` (no blank/comment above `---`).
- Use `rediss://` (TLS) for Upstash if/when added.
- `torch` wheels: pin Python 3.13 in Docker; treat the container as source of truth.
- Current `.gitignore` `*.yaml` rule would drop schema + infra yaml — fix before `git init`.
