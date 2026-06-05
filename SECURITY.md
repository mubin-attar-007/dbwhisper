# Security Policy

## Reporting a vulnerability

Please report security issues privately to **rsshah412@gmail.com** (or via GitHub's
"Report a vulnerability" / private security advisory). Do not open a public issue for
security reports. We aim to acknowledge within 72 hours.

## Security model

DBWhisper executes LLM-generated SQL against user databases. Defense in depth:

- **Read-only enforcement.** Every generated statement passes `app/core/sql_validator.py`
  before execution: it must start with `SELECT`/`WITH`, may not contain DML/DDL keywords
  (`INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `TRUNCATE`, `CREATE`, `GRANT`, `EXEC`, …),
  may not be multiple statements, may not use `SELECT … INTO`, and may not touch system
  schemas (`information_schema`, `sys.`, `pg_catalog.`).
- **Least-privilege connections.** Enrolled target databases should use a DB user with
  **SELECT-only** privileges. `app/security/db_readonly_checker.py` runs a best-effort,
  non-destructive probe and warns when a connection appears writable.
- **Secrets via environment only.** No secrets are committed. `.env` is git-ignored; use
  `.env.example` as the template. Provider keys and connection strings are injected through
  the host's secret store (HF Spaces / Render dashboard).
- **Log sanitization.** `app/utils/logger.py` masks connection-string passwords, API keys,
  and SQL string/number literals before anything is written to logs.
- **CORS.** `CORS_ALLOW_ORIGINS` is environment-driven; production should pin it to the exact
  frontend origin(s). A wildcard in production emits a startup warning.

## Hardening checklist for deployers

- [ ] Use a dedicated, **read-only** DB user for every enrolled database.
- [ ] Set `APP_ENV=production` and a non-wildcard `CORS_ALLOW_ORIGINS`.
- [ ] Rotate any API key that may have been exposed.
- [ ] Restrict who can call `/schemas/enroll` and `/schemas/embeddings` (these are powerful).

## Supported versions

This project is pre-1.0; security fixes are applied to `main`.
