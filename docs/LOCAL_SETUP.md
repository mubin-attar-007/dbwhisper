# Local setup

Running DBWhisper end to end on your own machine, at zero cost: no API key, no cloud account, no
GPU. Three paths, in increasing order of how much you want to look at.

| You want | Go to | Time |
|---|---|---|
| To see it run | [Path A — containers](#path-a--containers) | ~5 min, mostly image pulls |
| To see it answer a question with a real model | [Path B — a local model](#path-b--add-a-local-model) | +10 min for the model pull |
| To develop on it | [Path C — from source](#path-c--from-source) | ~10 min |
| To exercise the pipeline with **no model at all** | [The fake-model path](#the-fake-model-path) | ~2 min |

Everything below was run on this branch on 2026-08-24. Where a command's output is quoted, it is the
output that command actually produced, not an illustration.

---

## Prerequisites

- **Docker** for paths A and B (Docker Desktop on Windows or macOS; Docker Engine + Compose v2 on Linux).
- **Python 3.13 and [uv](https://docs.astral.sh/uv/)** for path C. `uv` manages the interpreter too,
  so you do not need to install Python 3.13 yourself.
- **Node 20** only if you want the web console.
- No GPU. Nothing in this document uses one, and the compose file deliberately does not declare one.

Ports the stack wants, all overridable (see [Ports](#ports-and-collisions)): 8000 (API), 55432
(PostgreSQL), 11434 (Ollama).

---

## Path A — containers

```bash
git clone https://github.com/mubin-attar-007/dbwhisper
cd dbwhisper
docker compose --profile core up -d
```

That starts four things and finishes with two: `postgres` (pgvector), a one-shot `pgvector-init` that
creates the extension, a one-shot `migrate` that applies the Alembic history, and then `api` and
`worker`. The one-shots exit 0 and stay exited; that is success, not a crash.

Watch it become ready:

```bash
docker compose --profile core ps
curl localhost:8000/ready
```

```json
{"ready":true,"checks":{"postgres":true,"pgvector":true}}
```

`/ready` is the honest probe: it asserts the application database is reachable **and** that the
`vector` extension is installed. `/health` is liveness only and returns 200 as soon as the process is
up, so do not use it to decide the stack is working.

Also worth a look while you are here:

```bash
curl localhost:8000/metrics | head          # Prometheus exposition
open http://localhost:8000/docs             # the OpenAPI console
docker compose logs migrate                 # "application database at revision 0004"
```

**Stopping.** `docker compose --profile core down` keeps your data; add `-v` to delete the volumes
and start clean next time.

### What you do *not* have yet

A database to ask questions about, and a model to write the SQL. The next two sections add each.

### Sample databases to point it at

The `targets` profile starts a MariaDB and a PostgreSQL **to be queried by DBWhisper** — never
confuse them with the application's own `postgres` service, which holds the registry, memory and
embeddings.

```bash
docker compose --profile core --profile targets up -d
```

They come up empty with a `reader` / `reader` account on database `sample`. Load whatever schema you
like into them; enrollment reflects it.

If you would rather use the project's own demo dataset (customers / products / orders / order_items)
inside the application database, seed it from a checkout:

```bash
POSTGRES_CONNECTION_STRING="postgresql+psycopg://dbwhisper:dbwhisper@127.0.0.1:55432/dbwhisper" \
  uv run python scripts/seed_demo_db.py
```

The script is idempotent and creates a `demo` schema.

### Enrolling a database

Enrollment introspects the schema, writes a per-table YAML catalogue under `database_schemas/<flag>/`,
optionally documents each table with the model, and embeds the summaries for retrieval.

In `self_hosted` and `production` mode, stored connection strings are **encrypted at rest**, so
enrollment needs a key. Generate one:

```bash
uv run python -m app.platform.secrets generate-key
```

Put it in `.env` next to `compose.yaml` — compose interpolates from there, and `.env` is git-ignored:

```dotenv
DBW_SECRET_KEYS=<the key you just generated>
```

Then restart the stack (`docker compose --profile core up -d`) and enroll:

```bash
curl -X POST localhost:8000/schemas/enroll \
  -H 'Content-Type: application/json' \
  -d '{
        "db_flag": "sample",
        "db_type": "postgres",
        "connection_string": "postgresql+psycopg://reader:reader@postgres-target:5432/sample",
        "description": "Sample target database",
        "run_documentation": false
      }'
```

Two things will (correctly) stop you:

- **HTTP 400, "connection appears writable".** Enrollment refuses a credential that can write. Grant
  the enrolling role `SELECT` and nothing else. This is a backstop for the role you configure, not a
  substitute for configuring it.
- **A secrets error.** `DBW_SECRET_KEYS` is missing or unreadable. See above.

`"run_documentation": false` skips the per-table LLM pass, which is what makes enrollment slow and is
the only part that needs a model at all. Turn it on once you have one.

---

## Path B — add a local model

[Ollama](https://ollama.com) runs an open-weights model on your CPU. The `local-ai` profile starts it
on the same compose network, so the API reaches it at `http://ollama:11434` with no configuration.

```bash
docker compose --profile core --profile local-ai up -d
docker compose exec ollama ollama pull qwen2.5-coder:1.5b
```

Then pin the model profile and restart the API:

```bash
echo "MODEL_PROFILE=local-small" >> .env
docker compose --profile core --profile local-ai up -d
curl localhost:8000/v2/models/health
```

### Which model to pull

The registry (`app/llm/registry.py`) ships three local profiles. All three are Apache-2.0. The sizes
are the quantised defaults Ollama pulls and the RAM figures are approximate — **they are Ollama's
published numbers, not something this project measured.**

| `MODEL_PROFILE` | Ollama model | Size | Runs well on |
|---|---|---|---|
| `local-small` | `qwen2.5-coder:1.5b` | ~2 GB | **A laptop CPU. Start here.** |
| `local-balanced` | `qwen2.5-coder:7b` | ~6 GB | CPU if you are patient; comfortable on a small GPU |
| `local-quality` | `qwen2.5-coder:14b` | ~10 GB | GPU recommended |

`MODEL_PROFILE=auto` (the default) enables every local profile plus any remote provider whose API key
is set, and lets the router pick per capability. Pinning a name restricts routing to that one profile,
which is what you want while you are comparing models.

### If you would rather use a hosted provider

Set the key and nothing else; the profile becomes available automatically.

```dotenv
GEMINI_API_KEY=...      # or GROQ_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY,
                        # DEEPSEEK_API_KEY, OPENROUTER_API_KEY
```

`GET /v2/models/health` reports which profiles are usable and, for each one that is not, the reason —
usually "…API key is not set". Note that a remote provider means your schema (and, depending on
`EGRESS_POLICY`, more than that) leaves the host. `EGRESS_POLICY=LOCAL_ONLY` refuses remote providers
outright.

---

## Path C — from source

```bash
git clone https://github.com/mubin-attar-007/dbwhisper
cd dbwhisper
uv sync                      # installs Python 3.13 and the locked dependencies
cp .env.example .env
```

You still need a PostgreSQL with pgvector for the application database. The cheapest way is to borrow
the one from compose:

```bash
docker compose --profile core up -d postgres pgvector-init
```

Then point `.env` at it and migrate:

```dotenv
POSTGRES_CONNECTION_STRING=postgresql+psycopg://dbwhisper:dbwhisper@127.0.0.1:55432/dbwhisper
APP_MODE=self_hosted
MODEL_PROFILE=local-small
EMBEDDING_PROFILE=auto
DBW_SECRET_KEYS=<uv run python -m app.platform.secrets generate-key>
```

```bash
uv run python -c "from db.migrate import upgrade_to_head; import os; upgrade_to_head(os.environ['POSTGRES_CONNECTION_STRING'])"
uv run uvicorn app.main:app --reload --port 8000
```

The API also migrates on startup, so the explicit step is only there so you see it happen.

### The web console

```bash
cd web
npm ci
npm run dev            # http://localhost:3000
```

The browser calls the console's own origin at `/api/*`, which Next rewrites to `API_PROXY_TARGET`
(default `http://localhost:8000`). There is no CORS on that path, and no `NEXT_PUBLIC_API_BASE_URL` —
that variable is documented in some older text and has no reader in the code.

### The developer loop

```bash
uv run pytest                                    # offline; conftest pins the fake providers
uv run ruff check app db tests scripts run.py
uv run ruff format app db tests scripts run.py
uv run mypy app/embeddings app/analysis app/observability db/migrate.py
scripts/eval-smoke.sh                            # the evaluation gate; ./scripts/eval-smoke.ps1 on Windows
```

---

## The fake-model path

You can exercise the entire pipeline — retrieval, generation, policy, execution, verification,
summarisation — with **no model installed and no key**, by selecting the deterministic fake provider:

```bash
MODEL_PROFILE=fake EMBEDDING_PROFILE=fake uv run pytest
MODEL_PROFILE=fake EMBEDDING_PROFILE=fake scripts/eval-smoke.sh
```

or, in containers:

```bash
MODEL_PROFILE=fake EMBEDDING_PROFILE=fake docker compose --profile core up -d
```

This is not a stub that returns nothing. `app/llm/providers/fake.py` returns deterministic structured
responses and `app/embeddings` returns deterministic vectors, which is exactly what lets CI run the
whole graph offline and get the same answer every time. What it does **not** do is tell you anything
about how well a real model writes SQL — see `docs/EVALUATION.md` for that distinction, which the
evaluation harness enforces by refusing to call an oracle run a model measurement.

---

## Observability, if you want to watch it work

```bash
OTEL_ENABLED=true docker compose --profile core --profile observability up -d
```

| Service | URL | Notes |
|---|---|---|
| Prometheus | http://localhost:9090 | Targets should show `dbwhisper-api` and `otel-collector` **up** |
| Grafana | http://localhost:3001 | Anonymous admin; dashboards provisioned from `ops/grafana/dashboards` |
| Jaeger | http://localhost:16686 | Search for service `dbwhisper-api` |

The OpenTelemetry collector deletes `db.statement`, `db.connection_string`, `url.query` and similar
keys before exporting — a second copy of the scrubbing the application already does, running outside
the process that could have the bug. Reading a trace is covered in `docs/OPERATIONS.md`.

---

## Ports and collisions

Every published port is overridable, because at least one of them collides on most developer
machines. Set these in `.env` or on the command line:

| Variable | Default | Service |
|---|---|---|
| `DBW_API_PORT` | 8000 | API |
| `DBW_POSTGRES_PORT` | 55432 | Application PostgreSQL |
| `DBW_OLLAMA_PORT` | 11434 | Ollama |
| `DBW_MARIADB_PORT` | 13306 | MariaDB target |
| `DBW_POSTGRES_TARGET_PORT` | 15432 | PostgreSQL target |
| `DBW_PROMETHEUS_PORT` | 9090 | Prometheus |
| `DBW_GRAFANA_PORT` | 3001 | Grafana |
| `DBW_JAEGER_UI_PORT` | 16686 | Jaeger UI |

```bash
DBW_POSTGRES_PORT=55439 DBW_API_PORT=18100 docker compose --profile core up -d
```

---

## When it does not work

**`Bind for 0.0.0.0:55432 failed: port is already allocated`** — something else owns the port. Use the
table above.

**`/ready` returns `{"ready":false,"checks":{"postgres":true,"pgvector":false}}`** — PostgreSQL is up
but the extension is not installed. The `pgvector-init` one-shot creates it on every `up`; check its
exit code with `docker compose logs pgvector-init`. This is also what you see if you pointed the app
at a stock `postgres` image instead of `pgvector/pgvector`.

**The worker exits with `relation "users" does not exist`** — it started before migrations ran. In
compose both processes wait on the `migrate` one-shot, so this means you started the worker on its
own. Run `migrate` first.

**Enrollment returns 400 "connection appears writable"** — working as intended. Use a `SELECT`-only
role.

**Enrollment fails on secrets** — set `DBW_SECRET_KEYS`, or run in `APP_MODE=demo`, where connection
encryption is not required (and arbitrary connections are not permitted either).

**The first question after enrollment is slow** — with `EMBEDDING_PROFILE=auto` the embedding model is
downloaded once on first use into the shared `model-cache` volume. Subsequent runs skip it. Set
`EMBEDDING_PROFILE=fake` if you want no download at all, at the cost of meaningless retrieval.

**Generation fails with no provider available** — no local model is pulled and no API key is set. Pull
`qwen2.5-coder:1.5b` (Path B) or set `MODEL_PROFILE=fake` to see the plumbing without a model.

**A test run collides on the evaluation fixtures** (`PermissionError` on
`Temp/eval-fixtures/*.sqlite`, Windows) — two pytest processes are building the same fixture files.
Run one at a time, or point `DBW_EVAL_FIXTURE_DIR` at separate directories.
