# syntax=docker/dockerfile:1
#
# The DBWhisper API image.
#
# Three decisions are encoded here and each one is worth stating, because each one was a choice
# between two defensible options:
#
# 1. **uv comes from PyPI, not from a container registry.** The previous version copied the binary
#    out of `ghcr.io/astral-sh/uv:0.11`, which is faster but makes the build depend on a second
#    registry: on a host with a stale ghcr credential the build fails at line 1 with
#    `failed to authorize: denied` and never reaches the application at all. `pip install uv==<pin>`
#    depends only on the index the lockfile already trusts, and the version is pinned to the same
#    one CI uses (.github/workflows/ci.yml).
#
# 2. **`alembic.ini` is copied in.** It is not decoration: `db/migrate.py` resolves
#    `PROJECT_ROOT / "alembic.ini"`, and without it `upgrade_to_head` raises inside the container.
#    `db/database_manager.py` catches that and falls back to `create_all`, so the API starts, reports
#    healthy, and has silently never run a migration. An image that boots green while its schema is
#    unversioned is worse than one that refuses to boot.
#
# 3. **Embeddings default to the local CPU model, not a hosted one.** `EMBEDDING_PROFILE=auto` runs
#    BAAI/bge-small-en-v1.5 through fastembed/ONNX with no API key, so `docker run` works for someone
#    who has no credentials at all. It costs a one-time model download (a few tens of MB) into
#    FASTEMBED_CACHE_PATH on first use. Deployments that would rather pay a hosted provider than wait
#    for that download set `EMBEDDING_PROFILE=google` and supply `GOOGLE_API_KEY`.
#
# Two stages, and no GPU is required or expected anywhere. The `local-embeddings` extra (torch,
# transformers) is deliberately NOT installed: fastembed covers the same job in tens of megabytes.

# ── Builder: resolve and install the locked dependency set into a venv ────────────────────────────
FROM python:3.13-slim-bookworm AS builder

# Pinned to the version CI uses, so a build here and a build there resolve identically.
ARG UV_VERSION=0.11.6
RUN pip install --no-cache-dir "uv==${UV_VERSION}"

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app
# Manifest and lock only, so the (slow) dependency layer is cached independently of the source.
# `--no-dev` drops pytest/ruff/mypy; `--no-install-project` because this repo runs from source.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# ── Runtime ──────────────────────────────────────────────────────────────────────────────────────
FROM python:3.13-slim-bookworm AS runtime

LABEL org.opencontainers.image.title="DBWhisper API" \
      org.opencontainers.image.description="Natural-language-to-SQL agent with an AST read-only policy engine." \
      org.opencontainers.image.source="https://github.com/mubin-attar-007/dbwhisper" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app" \
    APP_ENV=production \
    EMBEDDING_PROFILE=auto \
    HOST=0.0.0.0 \
    PORT=7860 \
    HF_HOME=/app/.cache/hf \
    FASTEMBED_CACHE_PATH=/app/.cache/fastembed

# System packages, and why each one is here:
#   unixodbc + msodbcsql18 - SQL Server targets go through pyodbc, which needs a driver manager and
#                            a driver. Microsoft ships no wheel; this is the supported install path.
#   curl                   - the HEALTHCHECK below. Nothing else uses it at runtime.
# gnupg is installed to verify the Microsoft repository key and removed in the same layer.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl gnupg unixodbc ca-certificates \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
        | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
        > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
    && apt-get purge -y gnupg \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 appuser
WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --chown=appuser:appuser app ./app
COPY --chown=appuser:appuser db ./db
COPY --chown=appuser:appuser scripts ./scripts
COPY --chown=appuser:appuser database_schemas ./database_schemas
# alembic.ini must sit at PROJECT_ROOT: see decision (2) in the header.
COPY --chown=appuser:appuser alembic.ini ./alembic.ini
COPY --chown=appuser:appuser run.py ./run.py

# .cache holds the fastembed model download; mount a volume over it to survive container restarts.
RUN mkdir -p /app/Log /app/.cache/hf /app/.cache/fastembed \
    && chown -R appuser:appuser /app/Log /app/.cache
USER appuser

# ── API ──────────────────────────────────────────────────────────────────────────────────────────
FROM runtime AS api

EXPOSE 7860
# Shell form on purpose: the exec form does not expand ${PORT}, and the port is deployment-chosen
# (7860 for Hugging Face Spaces, 8000 under compose, $PORT on Render).
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=5 \
    CMD curl -fsS "http://127.0.0.1:${PORT:-7860}/health" || exit 1

CMD ["sh", "-c", "exec uvicorn app.main:app --host \"${HOST:-0.0.0.0}\" --port \"${PORT:-7860}\""]
