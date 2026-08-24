# ADR 0007 — Target credentials encrypted at rest, with the key outside the database

**Status:** Accepted — 2026-08-24, Phase 1/3. **Breaking change** for existing self-hosted and
production deployments (`docs/v2/IMPLEMENTATION_ROADMAP.md`, "Breaking changes on this branch").

## Context

`Database_config.connection_string` held each enrolled target's DSN — password included — as
ordinary text. Anything that could read the application database could read every customer
credential: a backup, a read replica, a support query, a SQL injection into the app's own database,
or an operator with `SELECT` on one table. The brief's non-negotiable on credentials made this the
first thing that had to change once migrations existed to change it with.

## Decision

**Authenticated encryption, keys held in the environment.** `app/platform/secrets.py` wraps
`cryptography`'s `Fernet` (AES-128-CBC + HMAC-SHA256) in a `MultiFernet`: the **first** key encrypts,
**all** configured keys decrypt. That is what makes rotation possible without downtime — add the new
key in front, re-encrypt, drop the old one. Keys live in `DBW_SECRET_KEYS`, never in the database;
`python -m app.platform.secrets generate-key` produces one.

**A prefix, so old and new rows are distinguishable.** Ciphertext is stored as `enc:v1:…`
(`ENC_PREFIX`), which is what lets `is_encrypted()` tell a migrated row from a legacy one without a
schema flag.

**A two-column, tolerant migration.** Migration `0002_connection_secret` adds
`Database_config.connection_secret` and deliberately **keeps** the plaintext column: a deployment
without a key must keep working while its operator generates one, and the revision must be
reversible. `app/platform/connection_secrets.py` writes only to the new column
(`prepare_for_storage`), reads the new column first and falls back to the old one
(`read_connection_string`), and a startup pass (`encrypt_existing_rows`) moves legacy values across
once a key is present. Secret rotation emits an audit event.

**Whether encryption is required is a mode decision** (ADR 0005): `secret_encryption_required` is
true in `self_hosted` and `production`, where a missing key refuses the *write* rather than silently
downgrading it. Demo mode stores no secrets at all.

## Consequences

**What it buys.** A dump of the application database is no longer a credential dump. Rotation is a
supported operation rather than a re-enrollment exercise. The refusal-to-store rule means a
deployment cannot believe it is encrypting when it is not.

**What it costs.**

* **Existing operators must act.** Enrolling a database in `self_hosted` or `production` now
  requires `DBW_SECRET_KEYS`. Existing rows keep working, but the first new enrollment fails without
  a key. This is a breaking change and is documented as one.
* **Losing the key loses the connections.** There is no recovery path: every affected source must be
  re-enrolled with its DSN re-entered. Key custody becomes an operational responsibility the project
  did not previously impose.
* **The threat covered is narrower than "encrypted".** The key is in the environment of the process
  that decrypts, so an attacker with code execution in the app has both. This protects the database
  at rest, backups, replicas and database-level access — not a compromised application host.
* **The plaintext column still exists.** Until a later migration drops it, a deployment can be
  half-migrated, and a reader who checks the wrong column will find plaintext. That is the price of
  a reversible migration on live data, and it is a debt with a named payer.
* **Decryption is on the request path** — once per execution, since the graph deliberately does not
  cache the credential in state (ADR 0006).
