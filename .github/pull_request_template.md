## What & why
<!-- One or two sentences: what changed and the motivation. -->

## Checklist
- [ ] `uv run ruff check app db tests run.py` and `uv run pytest` pass locally
- [ ] `cd web && npm run build` passes (if the frontend changed)
- [ ] No secrets committed (`.env` stays untracked; gitleaks clean)
- [ ] Read-only SQL validation / API-key gates not weakened
- [ ] `.env.example` + docs updated if config or behavior changed
- [ ] Migrations included (once Alembic lands) for any DB schema change
