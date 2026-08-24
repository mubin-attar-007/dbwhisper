# Operations

What to do when DBWhisper is running and something needs doing to it. Written for the person on
call, so every section leads with the command and explains afterwards.

Two data stores are involved and confusing them is the most expensive mistake available here:

| | The **application** database | A **target** database |
|---|---|---|
| Holds | connection registry, users, sessions, conversation memory, jobs, pgvector embeddings | your actual data |
| Written by | DBWhisper | never by DBWhisper |
| Migrated by | Alembic (`db/migrations/`) | nothing — DBWhisper only reflects it |
| Env var | `POSTGRES_CONNECTION_STRING` / `PROJECT_DB_CONNECTION_STRING` | stored (encrypted) in `Database_config` |

Everything below that says "the database" means the **application** database unless it says otherwise.

---

## 1. The mode matrix

`APP_MODE` selects one of three modes, and every behavioural difference between them lives in a
single immutable policy object (`app/platform/modes.py`). Ask the policy, not the mode name.

| Behaviour | `demo` | `self_hosted` | `production` |
|---|---|---|---|
| Auth required | no | no | **yes** |
| Anonymous query | yes | yes | no |
| User registration | no | yes | see `app/platform/modes.py` |
| Enroll arbitrary connections | **no** — bundled sources only | yes | yes |
| Network policy | `bundled_only` | `private_allowed` (loopback/RFC1918 permitted) | `public_strict` (loopback, link-local and metadata denied; optional allowlist) |
| Refuse writable connections | yes | yes, overridable | yes |
| Secret encryption required | no | **yes** | **yes** |
| Cookies `Secure` | yes | no | yes |
| Wildcard CORS allowed | no | yes | no |
| Rate limiting | strict | relaxed | configurable |
| Trace sanitisation | full | standard | full |
| Default egress policy | `SCHEMA_ONLY_REMOTE` | `LOCAL_ONLY` | see the module |
| Persist sensitive run data | no | yes | no |

`APP_ENV` is a *different* switch: it drives log format and cookie defaults only. For backward
compatibility `APP_ENV=production` with no explicit `APP_MODE` resolves to production mode **and logs
a warning** — set `APP_MODE` explicitly so the resolution is not implicit.

**Egress.** `EGRESS_POLICY` bounds what may leave the deployment towards a *remote* model provider,
ordered from `LOCAL_ONLY` (no remote provider at all) through `SCHEMA_ONLY_REMOTE`,
`MASKED_METADATA_REMOTE`, `AGGREGATES_REMOTE` to `REMOTE_ALLOWED`. Local providers (Ollama, fastembed,
fake) are never subject to it because nothing leaves the host. Setting it below the mode default
tightens; setting it above loosens, and you should be able to say why in a change description.

---

## 2. Migrations

```bash
# Where is the database now?
uv run python -c "import os;from db.migrate import current_revision;print(current_revision(os.environ['POSTGRES_CONNECTION_STRING']))"

# Apply everything pending. Idempotent; safe to run on every deploy.
uv run python -c "import os;from db.migrate import upgrade_to_head;upgrade_to_head(os.environ['POSTGRES_CONNECTION_STRING'])"

# Or with the Alembic CLI, which reads the same alembic.ini:
DBW_ALEMBIC_URL="$POSTGRES_CONNECTION_STRING" uv run alembic upgrade head
DBW_ALEMBIC_URL="$POSTGRES_CONNECTION_STRING" uv run alembic history
```

In containers, compose runs a dedicated `migrate` one-shot before the API and the worker start, so
the schema is never changed by two processes at once. Check it with `docker compose logs migrate`;
it prints `application database at revision <rev>`.

**The failure mode to know about.** `create_metadata_tables` calls `upgrade_to_head` and, if Alembic
raises, falls back to `create_all`. The intent is that a broken migration environment should not take
the API down — but the cost is that a silently failing migration then looks healthy. If you suspect
this, do not trust `/health`: check the revision with the command above. It is also why `alembic.ini`
is explicitly copied into the container image; without it, `upgrade_to_head` raises inside the
container on every boot and the fallback hides it.

**Writing a migration.**

```bash
DBW_ALEMBIC_URL="$POSTGRES_CONNECTION_STRING" uv run alembic revision --autogenerate -m "short slug"
```

Read what autogenerate produced before committing it — it does not see server defaults, index renames
or data moves. Then verify it on PostgreSQL, not only on SQLite: CI's `postgres-integration` job drops
and recreates `public`, upgrades from empty, and re-runs the upgrade to prove idempotence.

---

## 3. Backups and restore

The application database is the only thing that needs backing up. Target databases are yours, and
DBWhisper never writes to them.

**What is in it, and what losing it costs:**

| Data | Cost of loss |
|---|---|
| `Database_config` (encrypted DSNs) | re-enroll each database |
| pgvector embeddings (`langchain_pg_embedding`) | re-run the embedding stage; slow, not lossy |
| Conversation memory and checkpoints | follow-up context and any paused run is gone |
| `users`, `user_sessions` | everyone logs in again |
| `verified_queries` | **genuinely lost** — this is curated human work |

`database_schemas/<flag>/*.yaml` (the per-table catalogue, including AI-drafted descriptions) lives on
disk, not in the database. Back up that directory too, or accept re-running documentation.

**Logical dump:**

```bash
pg_dump "$POSTGRES_CONNECTION_STRING" -Fc -f "dbwhisper-$(date -u +%Y%m%dT%H%M).dump"
pg_restore --clean --if-exists -d "$POSTGRES_CONNECTION_STRING" dbwhisper-20260824T1200.dump
```

If the deployment is on Neon, point-in-time restore is the primary mechanism and needs no setup;
`docs/BACKUP.md` has the branch-and-verify procedure, including the monthly restore drill. **A backup
you have never restored is a hope, not a backup** — that document has a log table for the drill
results, and it is currently empty.

**A restore only works if you still have the encryption keys.** Ciphertext in `connection_secret` is
useless without `DBW_SECRET_KEYS`. Back the keys up somewhere that is not the database — that is the
entire point of keeping them outside it.

---

## 4. Key rotation

Stored target-database DSNs are encrypted with Fernet through `MultiFernet`: **the first key encrypts,
all keys decrypt.** That is what makes rotation a rolling change rather than an outage.

```bash
# 1. Generate the new key.
uv run python -m app.platform.secrets generate-key

# 2. Put it FIRST, keeping the old one so existing rows still decrypt.
#    DBW_SECRET_KEYS=<new>,<old>
#    Deploy this, and let it settle. Nothing has been re-encrypted yet.

# 3. Re-encrypt every stored row with the new primary key.
uv run python - <<'PY'
import os
from sqlalchemy.orm import Session
from sqlalchemy import create_engine
from app.platform.connection_secrets import encrypt_existing_rows

engine = create_engine(os.environ["POSTGRES_CONNECTION_STRING"])
with Session(engine) as session:
    print("re-encrypted rows:", encrypt_existing_rows(session))
PY

# 4. Only now drop the old key:  DBW_SECRET_KEYS=<new>
```

`encrypt_existing_rows` commits row by row and skips rows that already hold ciphertext, so it is
idempotent and survives being interrupted. Do not skip step 2: a deployment that starts with only the
new key cannot read anything encrypted with the old one, and there is no recovery from that except a
restore.

**Verify before you drop the old key.** Fetch each enrolled database through the API (`GET /databases`
then a trivial query per flag) — a decrypt failure surfaces as an error there, and it is much cheaper
to find while the old key is still in the list.

**If a key leaks**, treat every DSN it protected as leaked: rotate the *database credentials* first,
then re-enroll. Rotating the Fernet key alone does not help, because whoever has the key and a backup
already has the plaintext.

---

## 5. When a model provider is down

**What happens on its own.** `app/llm/router.py` routes by capability and holds a circuit breaker per
provider: repeated failures open the circuit and the router stops offering that provider until it
half-opens again. Failures are normalised into categories (timeout, rate-limited, permission,
provider) so a rate-limit is retried differently from a bad key.

**What you should look at, in order:**

```bash
curl -s localhost:8000/v2/models/health    # which profiles are usable, and why the others are not
curl -s localhost:8000/metrics | grep -E 'dbw_provider_circuit_state|dbw_provider_fallbacks_total|dbw_model_calls_total'
```

`dbw_provider_circuit_state` tells you whether the breaker is open. `dbw_provider_fallbacks_total`
rising while `dbw_model_calls_total{outcome="error"}` rises is a provider problem; both flat while
`dbw_graph_runs_total{outcome="error"}` rises is not.

**What to do:**

| Situation | Action |
|---|---|
| One remote provider is failing | Nothing, if another has credentials — the router moves on. Confirm with `/v2/models/health`. |
| Every remote provider is failing | Fall back to a local model: pull `qwen2.5-coder:1.5b` and set `MODEL_PROFILE=local-small`. No key, no egress. |
| Ollama is unreachable | Check `OLLAMA_BASE_URL` and that the container is healthy. Under compose the API reaches it at `http://ollama:11434`. |
| You need the API answering *something* now | `MODEL_PROFILE=fake` keeps every endpoint responding deterministically. It is a diagnostic, not a service: the answers are not real. |

There is no configuration in which a missing model degrades to "guess the SQL". Generation fails and
the request errors, which is the correct behaviour.

---

## 6. Reading a trace

Turn tracing on with `OTEL_ENABLED=true` and an `OTEL_EXPORTER_OTLP_ENDPOINT`; under compose, start
the `observability` profile and open Jaeger at http://localhost:16686, service `dbwhisper-api`.

**What a query run looks like.** One span per graph node, in the order the graph ran them: `retrieve`
→ `understand` → (`clarify`) → `generate` → `validate` → (`approve`) → `execute` → `verify` →
`summarize` → `finalize`. Repairs appear as a second `generate`/`validate` pair under the same run,
which is the quickest way to see that a question needed retrying.

**What the attributes mean.** Every span carries a closed set of keys (`app/observability/attributes.py`);
the useful ones:

| Attribute | Read it as |
|---|---|
| `dbw.run_id` | the LangGraph thread id — also what `GET /v2/runs/{run_id}` takes |
| `dbw.node`, `dbw.step` | where in the graph this span is |
| `dbw.policy_decision`, `dbw.policy_version` | what the policy engine decided, under which ruleset |
| `dbw.sql_fingerprint` | a stable id for the statement — **not the SQL** |
| `dbw.provider`, `dbw.model`, `dbw.capability` | which model was routed to, and for what |
| `dbw.error_category` | one of a closed vocabulary: `timeout`, `connection`, `permission`, `policy_blocked`, `validation`, `provider`, `rate_limited`, `not_found`, `cancelled`, `internal` |
| `dbw.latency_ms`, `dbw.retry_count`, `dbw.cache_hit` | the usual |

**What you will not find there, by design.** The question, the SQL, the rows, the DSN, exception
messages. An attribute is dropped unless its key is on the allowlist, and the collector deletes
`db.statement` and friends again on the way out. This is deliberate: a trace backend is an
uncontrolled second copy of the data if you let it be one.

So a trace tells you *where* and *what kind*, and it will not tell you *which row*. When you need the
statement itself, use `GET /v2/runs/{run_id}`, which returns the run's own record under the API's auth
rules rather than to everyone with trace access.

**A leak alarm you should wire up.** `dbw_span_attributes_refused_total` and
`dbw_metric_label_rejections_total` count instrumentation that tried to attach something forbidden.
They should sit at zero. A non-zero value is not a leak — the allowlist stopped it — it is a bug
report about code that tried.

---

## 7. Metrics worth an alert

`GET /metrics` (Prometheus exposition, disabled by `METRICS_ENABLED=false`, which returns an honest
404 rather than an empty body). It is unauthenticated by design and must stay on an internal network:
it carries no customer data, but it does reveal traffic shape.

| Metric | Watch for |
|---|---|
| `dbw_http_requests_total`, `dbw_http_request_duration_seconds` | error rate and latency by route |
| `dbw_policy_decisions_total{decision="deny"}` | a spike means either an attack or a prompt regression — check which |
| `dbw_query_repairs_total` | rising = the model is producing worse SQL than it was |
| `dbw_graph_runs_total{outcome=...}` | end-to-end success |
| `dbw_provider_circuit_state` | 1 = open = a provider is being skipped |
| `dbw_worker_queue_depth` | rising and not falling = the worker is dead or wedged |
| `dbw_database_query_duration_seconds` | target-database latency, not the app's |
| `dbw_span_attributes_refused_total`, `dbw_metric_label_rejections_total` | should be zero |

Quantiles are computed in Prometheus with `histogram_quantile` over the `_bucket` series, not in the
process — a per-process quantile cannot be aggregated across replicas. The dashboards in
`ops/grafana/dashboards/` do that arithmetic, and a test asserts every PromQL expression in them
references a metric that exists.

---

## 8. The worker

```bash
python -m app.jobs.worker                       # from source
python -m app.jobs.worker --kinds enrollment    # only some job kinds
docker compose --profile core up -d --scale worker=3
```

One job at a time per process; you scale by running more processes, and jobs are claimed with a lease
so replicas do not collide. It stops cleanly on SIGINT/SIGTERM — the job in flight finishes its
current stage, the lease is released, and the next worker resumes from the following stage. Give it a
real stop grace period (compose sets 60s); the Docker default of 10s will SIGKILL it mid-stage.

**It has no healthcheck on purpose.** "The process exists" would report green for a worker wedged on a
dead lease. The real signal is `dbw_worker_queue_depth` and the `jobs` table.

**The worker does not run migrations.** It calls `ensure_tables()` at startup, which needs the schema
to exist already. Starting it before the API on a fresh database produces
`relation "users" does not exist` — run migrations first (compose serialises this for you).

---

## 9. Routine checks

**On every deploy**

```bash
curl -sf "$BASE/ready"        # {"ready":true,"checks":{"postgres":true,"pgvector":true}}
curl -s  "$BASE/v2/models/health"
uv run python -c "import os;from db.migrate import current_revision;print(current_revision(os.environ['POSTGRES_CONNECTION_STRING']))"
```

**Weekly**

- Read the CI `security` job artifacts (bandit, Trivy) and the pip-audit output. Both are
  report-only, which means somebody has to actually read them — the weekly scheduled run exists so
  advisories surface on a repository nobody has pushed to.
- `dbw_policy_decisions_total{decision="deny"}` trend. A slow rise is usually a prompt or schema
  change, not an attacker.

**Monthly**

- The restore drill in `docs/BACKUP.md`. Record the result in its log table.
- Re-run `scripts/eval-smoke.sh` and compare against the last run. It is deterministic, so any
  movement is a real change.
- Re-check the claims in `docs/v2/CLAIM_AUDIT.md` whose versions have moved: prompt version, policy
  version, corpus version, provider list, endpoint set.

---

## 10. Incident notes

**Suspected prompt injection through data.** The policy engine is between generation and execution, so
an injected instruction cannot become a write — it becomes a denied statement. Confirm with
`dbw_policy_decisions_total{decision="deny"}` and the run record. Then check the *content* path: an
injected instruction can still influence the summary. The verification step checks the summary against
the returned rows, but it is a shape check, not a semantic proof.

**A target database is being hammered.** Row caps and statement timeouts are per-execution
(`app/execution/`); per-IP token buckets are per-request (`RATE_LIMIT_*`, `QUERY_RATE_LIMIT_*`).
Neither bounds total load across users. Tighten `QUERY_RATE_LIMIT_PER_SEC`, and remember the target
database's own `statement_timeout` is the backstop you control independently of this application.

**Someone enrolled a writable connection.** They should not have been able to — enrollment probes and
returns 400. But the probe is best-effort: for dialects other than PostgreSQL, MySQL and SQL Server it
is a generic check. Audit `Database_config`, and fix it at the database by revoking write privileges
from the role, not by adding a check here.

**SQL Server specifically.** There is no session-level read-only mode on SQL Server, so a statement
that gets past the policy engine has only the login's privileges standing between it and the data.
Give SQL Server logins `db_datareader` and nothing more, and mean it.
