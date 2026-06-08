# Backup & restore

dbwhisper's data lives in **Neon Postgres** — conversation memory + the pgvector schema
embeddings (`langchain_pg_embedding`) + the `DatabaseConfig` registry + the `demo` schema.

## Backups
- **Neon point-in-time restore (PITR)** is the primary mechanism: Neon retains WAL history
  (window depends on plan — free ~24h, paid longer). No setup required.
- **Optional logical dumps** for longer retention — periodically dump to object storage:
  ```bash
  pg_dump "$POSTGRES_CONNECTION_STRING" -Fc -f "dbwhisper-$(date -u +%Y%m%d).dump"
  ```

## Monthly restore verification (do not skip)
A backup you've never restored is a hope, not a backup.
1. In the Neon console, **create a branch** from a recent point in time (an isolated copy).
2. Point a checkout at it: `export POSTGRES_CONNECTION_STRING="<branch URL, +psycopg>"`.
3. Verify connectivity + content:
   ```bash
   uv run python - <<'PY'
   import os
   from sqlalchemy import create_engine, text
   e = create_engine(os.environ["POSTGRES_CONNECTION_STRING"])
   with e.connect() as c:
       print("tables:", c.execute(text(
           "select count(*) from information_schema.tables where table_schema in ('public','demo')"
       )).scalar())
       print("embeddings:", c.execute(text("select count(*) from langchain_pg_embedding")).scalar())
   PY
   ```
4. Confirm the `demo` schema + embeddings are present (non-zero).
5. **Delete the Neon branch.**
6. Record the date + result in the log below.

## Restore-verification log
| Date (UTC) | Result | Notes |
|---|---|---|
| _none yet_ | | |
