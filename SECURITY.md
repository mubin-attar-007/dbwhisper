# Security Policy

DBWhisper executes model-generated SQL against databases you enroll. That is an inherently
security-sensitive thing to do, so this document describes the controls **as they exist in the code
today**, with the limits of each one stated next to it. Where a control is implemented but not yet
wired into the request path, it says so.

A fuller treatment — assets, trust boundaries, adversaries, per-threat residual risk, and a numbered
register of what is *not* mitigated — is in [`docs/v2/THREAT_MODEL.md`](docs/v2/THREAT_MODEL.md).

---

## Reporting a vulnerability

Report security issues privately to **sk.mubinattar@gmail.com**, or through GitHub's
"Report a vulnerability" / private security advisory on this repository. Please do not open a public
issue for a security report.

- **Acknowledgement:** within 72 hours.
- **What helps:** the affected endpoint or module, the version or commit, a reproduction, and the
  impact you believe it has. A working proof of concept is welcome but not required.
- **Coordinated disclosure:** we will agree a timeline with you. The default is public disclosure
  once a fix is released, with credit if you want it.
- **Scope note:** this project is pre-1.0 (`pyproject.toml`, `version = "0.1.0"`). Fixes are applied
  to `main`; there are no maintained release branches.

Findings about a *deployment* of DBWhisper that you do not operate should go to whoever operates it.

---

## What the security model is trying to achieve

One sentence: **a question should not be able to change your data, reach a database you did not
enroll, or read a table outside the enrolled scope — and when something is refused, you should be
able to see why.**

The design assumption behind every control below is that the model can be wrong or can be
manipulated. Nothing here relies on the model behaving. The model *proposes*; deterministic code
adjudicates.

---

## Controls

### 1. SQL policy engine — an AST check, plus an independent second layer

Every statement — generated, hand-edited through `/run_sql`, or issued by an evaluation run — is
parsed to an abstract syntax tree with [sqlglot](https://github.com/tobymao/sqlglot) in the declared
dialect and evaluated by `app/sqlpolicy` (version string `sql_policy@2.0.0`,
`app/sqlpolicy/engine.py`). The engine admits a single read-only `SELECT` or set operation, and
rejects DML, DDL, `GRANT`/`REVOKE`, `EXEC`, multiple statements, `SELECT … INTO`, system-catalog
access (`information_schema`, `pg_catalog`, `sys.*`), cross-database references, unknown tables and
unresolvable columns, a blocked-function list (file, network, sleep, lock, sequence, configuration
introspection), and statements over the complexity limits for the active policy level.

The original keyword heuristics were kept as a **second, independent layer**
(`app/sqlpolicy/legacy_heuristics.py`) rather than deleted. Both layers run and **the stricter
decision wins**, so a bypass has to defeat a parser and a regex that fail in different ways.

Decisions are regression-tested against
`app/evaluation/datasets/adversarial/sql_policy_cases.yaml` (v2.0.0, 255 cases expanded across
PostgreSQL, MySQL, SQL Server and SQLite) — run `uv run pytest tests/sqlpolicy` from a clean
checkout to reproduce.

> **Limit.** The decision is made on the parsed AST, so it is bounded by sqlglot's parse fidelity for
> each dialect. The engine's own docstring calls it "a structural filter, not a proof". A corpus
> measures the attacks somebody thought to write down; passing it is evidence of no *known* bypass,
> not of no bypass. That is exactly why the layers below exist.

### 2. One execution path

`app/execution/service.py::execute` is the only function in the codebase that runs statement SQL
against a target database. Editing SQL by hand does not widen what it may do, because `/run_sql`
goes through the same function — which **re-evaluates policy on every call**, even when the caller
already holds a decision. A caller-supplied decision is accepted only as an *approval token*: its
fingerprint has to match the statement being run, so substituting a different statement for an
approved one is rejected as `approval_mismatch` rather than executed.

Before a row is fetched: policy, then the network check, then a read-only session with a statement
timeout, then a bounded read of at most `max_rows + 1` rows so truncation is detected rather than
guessed.

> **Limit.** "One path" is an architectural invariant maintained by code review, not yet by a test.

### 3. Read-only sessions — and what that means per engine

Queries run inside a transaction that is rolled back in a `finally` block whatever happens
(`app/execution/connections.py`). The session-level enforcement differs by engine, and averaging it
would be misleading, so here it is in full:

| Engine | Session enforcement | Reported `enforced` |
|---|---|---|
| PostgreSQL | `SET TRANSACTION READ ONLY` + `statement_timeout` + `lock_timeout` + `idle_in_transaction_session_timeout` | `True` |
| MySQL / MariaDB | `START TRANSACTION READ ONLY`; `MAX_EXECUTION_TIME` best-effort (MariaDB spells it differently and it is skipped when unsupported) | `True` |
| SQLite | `PRAGMA query_only = ON`, plus `mode=ro` when the URL is a file | `True` |
| **SQL Server** | **None — SQL Server has no session-level read-only mode.** The driver query timeout still applies. | **`False`** |
| Anything else | No server-side read-only mode could be applied | `False` |

On SQL Server, and on any dialect the code does not recognise, enforcement rests on the policy
engine (§1) and on a least-privilege login (§4). The code reports this honestly rather than claiming
uniform behaviour: `read_only_setup` returns `enforced=False` and a message explaining why.

### 4. Least-privilege connections, verified once at enrollment

Enrolled databases should use a **SELECT-only** role. That is the layer that holds when everything
above it is defeated, so it is worth doing properly.

At enrollment, `app/execution/readonly.py::verify_read_only` inspects the credential's privilege
metadata. **No probe attempts a write** — creating a table to test whether writes are possible would
be the behaviour this product exists to avoid. The result is a five-state report:
`VERIFIED_READ_ONLY`, `APPEARS_READ_ONLY`, `WRITABLE`, `UNSUPPORTED`, `CONNECTION_FAILED`.

`POST /schemas/enroll` **fails closed**: anything other than the two safe states returns HTTP 400 and
refuses the enrollment — and so does the probe itself raising an exception. (An earlier version
initialised the flag to "safe" and left it that way when the probe errored; that has been fixed.)

> **Limits.** The check happens once. Nothing re-verifies the grant afterwards, and there is no field
> on the connection record to store a verification result — so a role that gains `INSERT` next month
> is not noticed. Coverage also varies by dialect: PostgreSQL, MySQL and SQL Server have specific
> checks; other dialects fall back to a generic one.

### 5. Connection secrets encrypted at rest

Stored DSNs are encrypted with Fernet (`app/platform/secrets.py`), with an `enc:v1:` prefix and
multi-key support so a key can be rotated without downtime — the first key encrypts, all configured
keys decrypt. Generate one with:

```bash
uv run python -m app.platform.secrets generate-key
```

and set it as `DBW_SECRET_KEYS` (comma-separated; put the new key first when rotating).

Reads prefer the encrypted column and fall back to the legacy plaintext one so a database that has
not been migrated yet keeps working; `encrypt_existing_rows` moves the remaining values across, one
committed row at a time so it is safe to interrupt. **In self-hosted and production modes a missing
key is refused outright** rather than silently downgraded to plaintext — a deployment that thinks it
is encrypting and is not is worse than one that knows it is not.

> **Limit.** Encryption protects the DSN at rest in the application database. It does not protect it
> from anyone who can read the process environment or memory.

### 6. Network policy (SSRF) on target connections

Before any connection is opened, the target host is checked by
`app/platform/network_policy.py::check_target`:

- **Cloud metadata endpoints are refused in every mode, self-hosted included** — AWS/GCP/Azure/
  DigitalOcean `169.254.169.254`, ECS `169.254.170.2`, the Alibaba and Oracle equivalents, the IPv6
  metadata address, and the corresponding hostnames.
- **Non-database ports are blocked**: 22, 23, 25, 53, 111, 135, 139, 445, 465, 587, 2049.
- **Three levels**, chosen by application mode: `BUNDLED_ONLY` (demo — only data sources shipped
  with the app), `PRIVATE_ALLOWED` (self-hosted — loopback and RFC1918 permitted),
  `PUBLIC_STRICT` (production — loopback, link-local and metadata denied, with an optional
  `NETWORK_ALLOWLIST` of hosts or CIDRs).
- **Every address in the DNS answer is checked**, not just the first, and the resolved addresses are
  returned so a caller can pin them.

> **Limit — DNS rebinding.** The executor does not yet pin the addresses the checker resolved; it
> hands the original connection string to SQLAlchemy. That leaves a window between check and connect
> in which a hostile DNS server can answer differently. Tracked as **G-4** in the threat model.

### 7. Application modes

`APP_MODE` selects one of `demo`, `self_hosted`, `production`, and every behavioural difference
lives in a single immutable policy object (`app/platform/modes.py::AppModePolicy`) rather than
scattered `if is_production` checks. It governs whether authentication is required, whether
arbitrary connections may be enrolled, the network policy level, whether writable connections are
refused, whether secret encryption is required, cookie `Secure`, whether a CORS wildcard is
permitted, the rate-limit profile, trace sanitisation and the default egress policy.

`APP_ENV=production` without an explicit `APP_MODE` still resolves to production mode, with a
deprecation warning.

### 8. Egress policy — what may leave for a remote model provider

`EgressPolicy` is ordered from `LOCAL_ONLY` (nothing leaves the host) through
`SCHEMA_ONLY_REMOTE`, `MASKED_METADATA_REMOTE`, `AGGREGATES_REMOTE` to `REMOTE_ALLOWED`. Self-hosted
defaults to `LOCAL_ONLY`; demo and production default to `SCHEMA_ONLY_REMOTE`. Local providers
(Ollama, fastembed) are outside the policy because nothing leaves.

### 9. Authentication, sessions and multi-tenancy

- **API key gate.** `X-API-Key`, compared with `hmac.compare_digest`. **Fail-closed in production:**
  if no `API_AUTH_TOKENS` are configured, gated endpoints return 503 until the operator provides
  one. Limits: a single shared static token, no rotation, no per-key identity, no scopes.
- **Passwords.** Argon2id via `argon2-cffi`, with rehash-on-login when parameters change, and a
  dummy hash on the login path so a missing user and a wrong password take the same time.
- **Sessions.** Opaque `secrets.token_urlsafe(32)` tokens; **only the SHA-256 is stored**, so a
  leak of that table does not yield replayable session tokens. Cookie is `HttpOnly`, `SameSite=Lax`, `Secure` in
  production, and uses the `__Host-` prefix when secure. Limits: the TTL is 14 days, sessions are
  not rotated on privilege change, and `/auth/login` has no lockout beyond the per-IP limiter.
- **Tenancy.** A connection with `owner_id IS NULL` is public (the shared demo); otherwise only its
  owner may reach it. **`USER_AUTH_ENABLED` defaults to `false`**, which makes every request the
  anonymous public tenant — correct for a single-user self-hosted install, and something a
  multi-tenant deployment must change.

> **Known defect, disclosed rather than deferred.** `POST /schemas/enroll` returns an existing
> connection record for any `db_flag` regardless of its owner. With `USER_AUTH_ENABLED=true`, one
> authenticated user can trigger re-enrollment of another user's `db_flag`, causing model cost, an
> overwrite of that tenant's catalog, and use of that tenant's connection. It does **not** disclose
> the connection string, which no endpoint returns in any response. Tracked as **G-8** in the threat
> model and the highest-priority open fix. Until it lands, restrict who can call `/schemas/enroll`.

### 10. CSRF

`app/security/csrf.py` implements an Origin/Referer check plus a double-submit token
(`X-CSRF-Token` echoing the `dbw_csrf` cookie, compared in constant time), with a per-mode policy:
production enforces, self-hosted is opt-in via `DBW_CSRF_ENFORCED=1`, and demo is *not applicable*
because it has no login and therefore no ambient authority for a cross-site request to borrow.
Requests carrying no session cookie are exempt by design — an API-key client has nothing for a
forgery to abuse.

> **Status.** Implemented and tested (`tests/test_csrf.py`); **not yet mounted on the routes**. Until
> it is, `SameSite=Lax` is the only active layer. Tracked as **G-1**.

### 11. Export safety (spreadsheet formula injection)

A cell beginning `=`, `+`, `-`, `@`, TAB or CR is executed by Excel, LibreOffice Calc and Google
Sheets. Since result rows come from your database and a database is a place attackers put things,
`app/security/export_safety.py` neutralises such cells — including column names — with an escape
that `restore_cell` inverts, so a programmatic consumer recovers the original value. Signed numbers
(`-42`, `+3.5e2`) are deliberately left alone.

> **Status.** Implemented and tested (`tests/test_export_safety.py`); **the `csv` response field
> does not route through it yet**. Tracked as **G-1**.

### 12. Data classification and masking

`app/security/pii.py` classifies columns deterministically — from name, declared type and
constraints, with no model involved — into General / Identifier / Personal / Health / Financial /
Credential / Secret / FreeText / Unknown, and provides masking strategies per class. **A suggestion
is not enforcement:** every result starts as `SUGGESTED` and only a human-confirmed classification is
acted upon, which is what stops a heuristic false positive from redacting a column an analyst needed.

> **Status.** The classifier and the review state machine exist; the review surface that would let a
> human confirm a classification does not. In practice the enforced set is empty unless an operator
> populates it. Tracked as **G-7**.

### 13. Audit record

`app/platform/audit.py` defines typed, append-only events for the security-relevant actions:
enrollment and enrollment refusal, writable-connection override, approval granted/rejected, policy
denial, tenant-access denial, secret rotation, data export and CSRF rejection. Each carries actor,
action, subject, outcome, timestamp and a sanitised detail. Credential-shaped keys are redacted and
every detail value is coerced to a scalar, so **result rows and connection strings do not travel into
the audit log** — asserted in `tests/test_audit.py`.

> **Limits.** Events are written to the `dbwhisper.audit` logger with no hash chaining or write-once
> storage, so log-write access is history-rewrite access (**G-12**). The endpoints do not emit events
> yet (**G-1**).

### 14. Rate limiting

Per-`(IP, path)` token bucket, tighter on `/query` (burst 8, 0.1/s) than elsewhere (burst 30,
0.5/s), returning 429 as `application/problem+json` with `Retry-After`. Health probes are exempt.
`X-Forwarded-For` is honoured **only** when `TRUST_PROXY_HEADERS` is set, which a deployment behind
a proxy should set and a directly-exposed one must not.

> **Limits.** In-memory and process-local, so it does not coordinate across replicas. Keyed by path,
> so a burst spread across paths multiplies the allowance. Buckets are not evicted (**G-3**).

### 15. Log and error sanitisation

`app/utils/logger.py::sanitize_for_log` masks `scheme://user:pass@` authorities, `password=`/`pwd=`/
`uid=`/`user=` pairs, `api_key=`/`token=`/`secret=` values, `Bearer …` headers, and SQL string and
numeric literals, then truncates. On the way out, `sanitize_db_error` strips DSNs and
credential-shaped pairs from driver messages, truncates to 300 characters and maps the result to one
of eight categories.

> **Limits.** Both are best-effort regex and the modules say so. `LOG_SANITIZE=0` disables the log
> filter entirely. A few `/query` and `/schemas/enroll` paths still emit raw exception text that does
> not reach the sanitizer (**G-5**).

### 16. Prompt-injection posture

Untrusted database descriptions are wrapped in `<database_description>` delimiters and the prompt
instructs the model to treat the contents as data rather than instructions
(`app/agent/prompt.py`). **Delimiters plus an instruction are a mitigation, not a control** — the
real backstop is that anything the model is persuaded to emit still has to pass the policy engine,
still resolves against the enrolled schema scope, and still runs on a read-only session with a row
cap. Saying otherwise would overstate what prompt engineering can do.

### 17. Secrets in the repository, and supply chain

No secrets are committed. `.env` is git-ignored; `.env.example` is the template. gitleaks runs over
full git history in CI and blocks on a finding. Dependencies are fully pinned in `uv.lock` and
Dependabot is configured.

> **Limit.** `pip-audit` and `npm audit` are informational in CI — they report, they do not block.
> There is no SBOM and no artefact signing (**G-11**).

---

## Deployment checklist

- [ ] Use a dedicated **SELECT-only** database role for every enrolled database. This is the control
      that holds when the others do not.
- [ ] Set `APP_MODE=production` explicitly (not just `APP_ENV`).
- [ ] Set `API_AUTH_TOKENS`. Production fails closed without it, so the service will return 503 until
      you do.
- [ ] Set `CORS_ALLOW_ORIGINS` to your exact frontend origin(s). A wildcard in production currently
      warns rather than refusing (**G-14**), so this one is on you.
- [ ] Set `DBW_SECRET_KEYS` before enrolling anything, so no DSN is ever written in plaintext.
- [ ] Set `USER_AUTH_ENABLED=true` for any deployment with more than one tenant — and read the
      `/schemas/enroll` disclosure in §9 first.
- [ ] Set `TRUST_PROXY_HEADERS=true` **only** when a proxy you operate overwrites
      `X-Forwarded-For`; leave it false when the app is directly exposed.
- [ ] Restrict who can reach `/schemas/enroll` and `/schemas/embeddings` — both are powerful and
      `/schemas/embeddings` has no owner check (**G-9**).
- [ ] Keep `LOG_SANITIZE` at its default. Setting it to `0` disables log redaction.
- [ ] Ship the `dbwhisper.audit` logger somewhere append-only, separate from application logs.
- [ ] Rotate any provider key that may have been exposed, and prefer a local model provider
      (`EGRESS_POLICY=LOCAL_ONLY`) when the schema itself is sensitive.

---

## Supported versions

Pre-1.0. Security fixes are applied to `main`; there are no maintained release branches, and no
version other than the current `main` receives fixes.
