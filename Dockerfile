# syntax=docker/dockerfile:1
# DBWhisper container — lean image (hosted Google embeddings, no torch).
# Targets Hugging Face Spaces (Docker SDK) on port 7860; also runnable via compose/Render.

# ── Builder: resolve & install base deps into a venv with uv ──────────────────
FROM python:3.14-slim-bookworm AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /uvx /bin/
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Manifest + lock first for layer caching. Base deps only (no --extra local-embeddings,
# no dev) → no torch → small image.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# ── Runtime ───────────────────────────────────────────────────────────────────
FROM python:3.14-slim-bookworm AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app" \
    APP_ENV=production \
    EMBEDDING_PROVIDER=google \
    HOST=0.0.0.0 \
    PORT=7860 \
    HF_HOME=/tmp/hf \
    MPLCONFIGDIR=/tmp/mpl

# System deps: ODBC runtime + Microsoft SQL Server driver (MSSQL targets) + curl (healthcheck).
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
COPY --chown=appuser:appuser database_schemas ./database_schemas
COPY --chown=appuser:appuser run.py ./run.py

RUN mkdir -p /app/Log /tmp/hf /tmp/mpl && chown -R appuser:appuser /app /tmp/hf /tmp/mpl
USER appuser

EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=5 \
    CMD curl -fsS http://127.0.0.1:7860/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]
