"""FastAPI application for SQL Insight Agent.

Endpoints implemented:
- GET /health: lightweight health check
- GET /chat: development chat UI (static file)
- POST /query: convert natural language to SQL, execute, and return results
- POST /schemas/embeddings: generate schema embeddings and store in PGVector
- POST /schemas/enroll: enroll a database and run schema extraction/documentation/embeddings

This module focuses on defensive error handling: we sanitize all user-provided or LLM-generated
strings before logging, avoid exposing sensitive information, and return friendly HTTP errors
for known issues. The query execution pipeline uses provider fallback for LLMs and validates
SQL before executing it against the target database.
"""

from __future__ import annotations

# pylint: disable=duplicate-code
import json
import re
from datetime import datetime
from os import getenv
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.agent.chain import (
    agent_context,
    default_collection_name,
    get_available_providers,
    get_cached_agent,
    get_cached_agent_with_context,
    get_collected_tables,
    parse_structured_response,
    summarize_query_results,
)
from app.api.auth import router as auth_router
from app.api.v2 import router as v2_router
from app.core import result_formatter
from app.core.config import Settings, get_settings
from app.core.observability import init_sentry
from app.execution.readonly import verify_read_only
from app.execution.service import ExecutionRequest, ExecutionResult
from app.execution.service import execute as execute_read_only
from app.models import (
    DatabasesResponse,
    DatabaseSummary,
    DocumentationStageSummary,
    EmbeddingStageSummary,
    ExecutionMetadata,
    ExtractionStageSummary,
    HealthResponse,
    QueryRequest,
    QueryResponse,
    RunSqlRequest,
    SchemaEmbeddingRequest,
    SchemaEmbeddingResponse,
    SchemaPipelineReport,
    SchemaPipelineRequest,
    SchemaPipelineResponse,
    SchemaResponse,
    SchemaTable,
    VerifiedPair,
    VerifiedPairRequest,
    VerifiedPairReviewRequest,
    VerifiedPairsResponse,
)
from app.observability import metrics as obs_metrics
from app.observability.middleware import MetricsMiddleware
from app.observability.otel import setup_tracing, shutdown_tracing
from app.platform.audit import AuditAction, AuditOutcome
from app.platform.connection_secrets import (
    ConnectionSecretError,
    prepare_for_storage,
    read_connection_string,
)
from app.schema_pipeline import SchemaPipelineOrchestrator
from app.schema_pipeline.embedding_pipeline import (
    SchemaEmbeddingPipeline,
    SchemaEmbeddingSettings,
)
from app.security.auth import (
    require_api_key,
    require_api_key_if_enabled,
    resolve_enroll_owner,
)
from app.security.csrf import require_csrf
from app.security.ratelimit import RateLimitMiddleware
from app.sqlpolicy import Decision, Dialect
from app.user_db_config_loader import PROJECT_ROOT, get_user_database_settings
from app.utils.logger import sanitize_for_log as _sanitize
from app.utils.logger import setup_logging
from db.conversation_memory import (
    get_session_summary,
    store_query_context,
    update_or_create_session_summary,
)
from db.database_manager import (
    create_metadata_tables,
    get_project_db_connection_string,
    get_session,
)
from db.model import DatabaseConfig
from db.verified_queries import (
    delete_verified_query,
    list_verified_queries,
    save_verified_query,
    set_status,
)

# Initialize logging + optional Sentry (no-op unless SENTRY_DSN is set)
logger = setup_logging(__name__)
init_sentry()

# Create FastAPI app
app = FastAPI(
    title="DBWhisper",
    description=(
        "Ask a question in English, get an evidence-linked answer from your database. "
        "Every generated statement is checked read-only by an AST policy engine before it runs."
    ),
    version="0.1.0",
)

# Records request count, status distribution and latency against the matched *route template*, so a
# path carrying an id cannot mint a new time series per id.
app.add_middleware(MetricsMiddleware)


@app.on_event("startup")
async def _start_observability() -> None:
    """Start tracing and publish the running configuration. A no-op unless OTEL_ENABLED.

    Deliberately never fatal: an unreachable collector is an operations problem, not a reason to
    refuse to serve queries.
    """
    try:
        status = setup_tracing(app)
        logger.info("Startup: tracing %s.", status.value if hasattr(status, "value") else status)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Startup tracing skipped: %s", exc)

    try:
        from app.platform.modes import resolve_mode
        from app.sqlpolicy.rules import RULESET_VERSION

        obs_metrics.set_build_info(mode=resolve_mode().value, policy_version=RULESET_VERSION)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Startup build info skipped: %s", exc)


@app.on_event("startup")
async def _ensure_project_schema() -> None:
    """Self-heal the project metadata schema on boot (idempotent).

    ``create_metadata_tables`` runs ``create_all`` and adds ``Database_config.owner_id`` when
    missing, so a database provisioned before multi-tenancy is repaired on deploy instead of
    500-ing ``/query``. Never fatal — the underlying call is internally guarded.
    """
    try:
        connection = get_project_db_connection_string()
        create_metadata_tables(connection)
        logger.info("Startup: project metadata schema ensured.")
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Startup schema ensure skipped: %s", exc)
        return

    # Move any legacy plaintext connection strings into the encrypted column. Only runs when a key
    # is configured; without one the rows are left as they are and the warning at write time stands.
    if get_settings().secret_keys_list:
        try:
            from app.platform.connection_secrets import encrypt_existing_rows

            session = get_session(connection)
            try:
                moved = encrypt_existing_rows(session)
            finally:
                session.close()
            if moved:
                logger.info("Startup: encrypted %d stored connection string(s).", moved)
        except Exception as exc:  # pragma: no cover - never fatal
            logger.warning("Startup secret encryption skipped: %s", exc)


@app.on_event("shutdown")
async def _release_target_connections() -> None:
    """Close the pooled connections to enrolled target databases.

    Each enrolled source keeps a small bounded pool. Without this, a reload or a rolling restart
    leaves those sockets open on the customer's database until it times them out itself, which on a
    small connection limit is the difference between a clean restart and an outage.
    """
    try:
        from app.execution.connections import dispose_all

        dispose_all()
        logger.info("Shutdown: target database pools disposed.")
    except Exception as exc:  # pragma: no cover - shutdown must not raise
        logger.warning("Shutdown pool disposal skipped: %s", exc)

    try:
        shutdown_tracing()
    except Exception as exc:  # pragma: no cover - shutdown must not raise
        logger.warning("Shutdown tracing flush skipped: %s", exc)


# Dev-only static UI (chat page)
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    # mount under /static so files are available -- dev convenience only
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/chat")
async def chat_ui():
    """Dev UI: serve a small chat HTML page for manual testing.

    This route intentionally returns the static chat UI that posts to /query.
    It should be considered a development convenience and not a production feature.
    """
    f = static_dir / "chat.html"
    if f.exists():
        return FileResponse(f)
    # if static not present, redirect to OpenAPI docs as fallback
    return RedirectResponse(url="/docs")


# Per-IP rate limiting. Added before CORS so CORS remains the outermost middleware and
# even 429 responses carry CORS headers (browsers can read them).
app.add_middleware(RateLimitMiddleware)

# Configure CORS from environment. Browsers reject "*" together with credentials, so
# only enable credentials when an explicit origin allowlist is configured.
_settings = get_settings()
_cors_origins = _settings.cors_origins_list
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_origins != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
if _settings.is_production and _cors_origins == ["*"]:
    logger.warning(
        "CORS is wide-open ('*') in production. Set CORS_ALLOW_ORIGINS to your frontend origin(s)."
    )

# Authentication routes (/auth/register, /login, /logout, /me): public + rate-limited,
# session-cookie based. Adding them changes nothing for existing endpoints.
app.include_router(auth_router)

# The v2 pipeline: the LangGraph workflow, human-in-the-loop interrupts, evidence and traces.
# v1 routes keep their contract; nothing below changes how /query behaves.
app.include_router(v2_router)


# Models moved to `app.models` for reusability and readability


def _sanitize_sql(sql_text: str) -> str:
    """Remove formatting fences and whitespace from the agent's SQL output."""

    if not sql_text:
        return ""
    cleaned = sql_text.strip()

    code_blocks = re.findall(r"```(?:sql)?\s*([\s\S]*?)```", cleaned, flags=re.IGNORECASE)
    if code_blocks:
        cleaned = code_blocks[-1].strip()

    if cleaned.lower().startswith("sql"):
        cleaned = cleaned[3:].lstrip(" :\n")

    select_match = re.search(r"select", cleaned, flags=re.IGNORECASE)
    if select_match:
        cleaned = cleaned[select_match.start() :]

    return cleaned.strip()


def _extract_agent_output(agent_result: Any) -> str:
    """Normalize the agent response into a plain string."""

    def _stringify_segments(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    text_value = item.get("text")
                    if text_value:
                        parts.append(str(text_value))
                else:
                    text_attr = getattr(item, "text", None)
                    if text_attr:
                        parts.append(str(text_attr))
                    else:
                        parts.append(str(item))
            return "\n".join(part for part in parts if part)
        return str(content)

    if isinstance(agent_result, dict):
        messages = agent_result.get("messages")
        if isinstance(messages, list) and messages:
            final_message = messages[-1]
            content = getattr(final_message, "content", None)
            if content is not None:
                return _stringify_segments(content)
        for key in ("output", "content", "answer"):
            value = agent_result.get(key)
            if value:
                return _stringify_segments(value)
    return _stringify_segments(agent_result)


# Endpoints
@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Liveness probe — static, always 200 (used by the container/platform healthcheck)."""
    logger.debug("Health check requested")
    return HealthResponse()


@app.get("/metrics", include_in_schema=False)
async def metrics_endpoint() -> Response:
    """Prometheus exposition for the scrape job in ``ops/``.

    Off by a single setting rather than by removing the route, so a deployment that does not want
    to expose metrics returns an honest 404 instead of an empty body that looks like a working
    scrape target. The registry is dedicated and every label passes a cardinality guard, so this
    cannot leak a question, a row, or a connection string.
    """
    if not get_settings().metrics_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Metrics are disabled.")
    payload, content_type = obs_metrics.render()
    return Response(content=payload, media_type=content_type)


@app.get("/ready")
async def readiness_check() -> JSONResponse:
    """Readiness probe — verifies the project Postgres is reachable and pgvector is installed."""
    checks = {"postgres": False, "pgvector": False}
    try:
        from sqlalchemy import text

        from db import database_manager

        conn = database_manager.get_connection(get_project_db_connection_string())
        try:
            conn.execute(text("SELECT 1"))
            checks["postgres"] = True
            row = conn.execute(
                text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
            ).fetchone()
            checks["pgvector"] = row is not None
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("Readiness check failed: %s", _sanitize(str(exc), max_len=300))
    ok = all(checks.values())
    return JSONResponse(status_code=200 if ok else 503, content={"ready": ok, "checks": checks})


SUMMARY_SAMPLE_ROWS = 20
SUMMARY_SAMPLE_CHARS = 2000
_LLM_TAG = re.compile(r"<think[\s\S]*?>[\s\S]*?</think>|<[^>]+>", re.IGNORECASE)


def _clean_summary(text: str | None, max_lines: int = 3) -> str | None:
    """Strip model scaffolding tags and keep the summary to a few lines."""
    if not text:
        return None
    cleaned = _LLM_TAG.sub("", text).strip()
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    if not lines:
        return None
    return " ".join(lines[:max_lines])


def _network_policy_for(settings: Settings) -> tuple[object, list[str], list[str]]:
    """Network level, allowlist and bundled hosts for the active application mode."""
    policy = settings.mode_policy
    bundled = [h for h in (settings.network_allowlist_list or []) if h]
    return policy.network_policy, settings.network_allowlist_list, bundled


def _run_read_only(
    sql: str,
    db_flag: str,
    db_config: dict[str, Any],
    *,
    page: int | None,
    page_size: int | None,
    include_total: bool | None,
) -> ExecutionResult:
    """Send one statement through the single execution path (policy -> network -> read-only run)."""
    settings = get_settings()
    level, allowlist, bundled = _network_policy_for(settings)
    resolved_include_total = (
        include_total if include_total is not None else bool(page and page_size)
    )
    return execute_read_only(
        ExecutionRequest(
            sql=sql,
            connection_string=str(db_config["connection_string"]),
            dialect=Dialect.from_db_type(db_config.get("db_type")),
            db_flag=db_flag,
            max_rows=int(db_config.get("max_rows", 1000)),
            timeout_seconds=int(db_config.get("query_timeout", 30)),
            page=page,
            page_size=page_size,
            include_total=resolved_include_total,
            network_level=level,
            network_allowlist=allowlist,
            bundled_hosts=bundled,
        )
    )


def _metadata_from(result: ExecutionResult) -> ExecutionMetadata:
    policy = result.policy
    frame = result.frame
    return ExecutionMetadata(
        execution_time_ms=frame.duration_ms if frame else None,
        total_rows=(frame.total_rows if frame and frame.total_rows is not None else None),
        policy_version=policy.policy_version if policy else None,
        policy_decision=policy.decision.value if policy else None,
        sql_fingerprint=policy.fingerprint if policy else None,
        tables_used=[str(t) for t in policy.tables] if policy and policy.tables else None,
        truncated=frame.truncated if frame else None,
        read_only_enforced=result.read_only_enforced if result.success else None,
        error_category=result.error_category,
    )


def _response_from(
    result: ExecutionResult,
    sql: str,
    output_format: str,
    *,
    selected_tables: list[str] | None = None,
    follow_up_questions: list[str] | None = None,
    natural_summary: str | None = None,
    db_flag: str | None = None,
    http_request: Request | None = None,
) -> QueryResponse:
    """Build the API response from an execution result, success or failure."""
    metadata = _metadata_from(result)
    if not result.success:
        # A policy refusal is reported as validation_passed=False; anything later (network, the
        # database itself) passed validation but did not produce rows.
        validation_passed = bool(result.policy and result.policy.decision is not Decision.DENY)
        return QueryResponse(
            status="error",
            sql=sql,
            validation_passed=validation_passed,
            data=None,
            error=result.error,
            selected_tables=selected_tables or None,
            follow_up_questions=follow_up_questions,
            metadata=metadata,
        )

    formatted = result_formatter.format_frame(
        result.frame, sql=sql, output_format=output_format, stats=result.stats
    )

    # Gated on CSV rather than fired on every query: a download is the event worth reviewing, and
    # auditing every read would bury it. `cells_neutralized` being non-zero on a table that has
    # never had one is itself a signal.
    if output_format == "csv" and result.frame is not None:
        payload = formatted.get("data") or {}
        _audit(
            AuditAction.DATA_EXPORTED,
            subject=db_flag or "unknown",
            outcome=AuditOutcome.SUCCESS,
            request=http_request,
            user_id=_resolve_owner(http_request) if http_request is not None else None,
            detail={
                "row_count": result.frame.row_count,
                "truncated": result.frame.truncated,
                "cells_neutralized": payload.get("cells_neutralized", 0),
            },
        )

    return QueryResponse(
        status="success",
        sql=sql,
        validation_passed=True,
        data=formatted["data"],
        error=None,
        selected_tables=selected_tables or None,
        follow_up_questions=follow_up_questions,
        metadata=metadata,
        natural_summary=natural_summary,
    )


def _audit(
    action: AuditAction,
    *,
    subject: str,
    outcome: AuditOutcome,
    request: Request | None = None,
    user_id: int | None = None,
    reason: str = "",
    detail: dict[str, Any] | None = None,
) -> None:
    """Record a security-relevant event, deriving the actor from the request.

    Never raises: an audit sink failure must not turn a denial into an allow, nor a successful
    request into a 500.
    """
    from app.platform.audit import ActorKind, AuditActor, record

    kind = ActorKind.USER if user_id is not None else ActorKind.ANONYMOUS
    client = getattr(request, "client", None) if request is not None else None
    record(
        action,
        subject=subject,
        outcome=outcome,
        actor=AuditActor(
            kind=kind,
            id=str(user_id) if user_id is not None else None,
            ip=getattr(client, "host", None),
        ),
        reason=reason,
        detail=detail or {},
    )


def _enforce_db_access(http_request: Request, db_flag: str) -> None:
    """Tenancy gate, active only when ``user_auth_enabled``.

    Public db_flags (owner_id NULL, e.g. the demo) are open to anyone; owned ones require the
    owner's session. No-op when the flag is off, so default behavior is unchanged.
    """
    settings = get_settings()
    if not settings.user_auth_enabled:
        return
    from app.security.tenancy import user_can_access_db_flag
    from app.security.user_auth import get_current_user

    user = get_current_user(http_request)
    user_id = user.id if user else None
    if not user_can_access_db_flag(db_flag, user_id):
        # Audited: one tenant reaching for another's data is the event an incident review needs,
        # and a 403 alone leaves no record of who asked for what.
        _audit(
            AuditAction.TENANT_ACCESS_DENIED,
            subject=db_flag,
            outcome=AuditOutcome.DENIED,
            request=http_request,
            user_id=user_id,
            reason="the caller does not own this data source",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this database.",
        )


def _resolve_owner(http_request: Request) -> int | None:
    """Owning user id for training data (None when auth is off or the caller is anonymous)."""
    settings = get_settings()
    if not settings.user_auth_enabled:
        return None
    from app.security.user_auth import get_current_user

    user = get_current_user(http_request)
    return user.id if user else None


@app.get(
    "/databases",
    response_model=DatabasesResponse,
    dependencies=[Depends(require_api_key_if_enabled)],
)
async def list_databases(http_request: Request) -> DatabasesResponse:
    """List the enrolled databases the caller may query.

    Public databases (``owner_id`` NULL, e.g. the shared demo) are visible to everyone; when
    auth is enabled a signed-in user additionally sees the databases they own. Returns only
    non-sensitive metadata — never connection strings.
    """
    settings = get_settings()
    user_id: int | None = None
    if settings.user_auth_enabled:
        from app.security.user_auth import get_current_user

        user = get_current_user(http_request)
        user_id = user.id if user else None

    session = get_session(get_project_db_connection_string())
    try:
        rows = session.query(DatabaseConfig).all()
    finally:
        session.close()

    items: list[DatabaseSummary] = []
    for row in rows:
        is_public = row.owner_id is None
        if settings.user_auth_enabled and not is_public and row.owner_id != user_id:
            continue
        items.append(
            DatabaseSummary(
                db_flag=row.db_flag,
                db_type=row.db_type or "",
                description=row.description,
                is_public=is_public,
            )
        )
    # Public (e.g. demo) first, then alphabetical.
    items.sort(key=lambda d: (not d.is_public, d.db_flag))
    return DatabasesResponse(databases=items)


@app.post(
    "/training/pairs",
    response_model=VerifiedPair,
    dependencies=[Depends(require_api_key_if_enabled), Depends(require_csrf)],
)
async def save_training_pair(request: VerifiedPairRequest, http_request: Request) -> VerifiedPair:
    """Save a human-approved question -> SQL pair. The SQL must be read-only (validated here)."""
    _enforce_db_access(http_request, request.db_flag)
    try:
        pair = save_verified_query(
            request.db_flag, request.question, request.sql, _resolve_owner(http_request)
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return VerifiedPair(**pair)


@app.get(
    "/training/pairs",
    response_model=VerifiedPairsResponse,
    dependencies=[Depends(require_api_key_if_enabled)],
)
async def list_training_pairs(
    http_request: Request, db_flag: str | None = None
) -> VerifiedPairsResponse:
    """List saved verified pairs (scoped to the caller / the shared public set)."""
    pairs = list_verified_queries(db_flag, _resolve_owner(http_request))
    return VerifiedPairsResponse(pairs=[VerifiedPair(**p) for p in pairs])


@app.post(
    "/training/pairs/{pair_id}/review",
    response_model=VerifiedPair,
    dependencies=[Depends(require_api_key_if_enabled), Depends(require_csrf)],
)
async def review_training_pair(
    pair_id: int, request: VerifiedPairReviewRequest, http_request: Request
) -> VerifiedPair:
    """Move a pair through its lifecycle.

    The common use is re-approving a pair that schema drift marked ``stale``: enrollment retires it
    automatically, and a human puts it back into circulation once they have checked the SQL still
    answers the question against the new schema. Approving re-embeds it; anything else removes it
    from the retrieval index so it stops being reachable, not merely relabelled.
    """
    pair = set_status(
        pair_id,
        _resolve_owner(http_request),
        request.status,
        reason=request.reason,
        reviewer=request.reviewer,
    )
    if pair is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Verified pair not found."
        )
    _enforce_db_access(http_request, pair["db_flag"])
    return VerifiedPair(**pair)


@app.delete(
    "/training/pairs/{pair_id}",
    dependencies=[Depends(require_api_key_if_enabled), Depends(require_csrf)],
)
async def delete_training_pair(pair_id: int, http_request: Request) -> dict[str, bool]:
    if not delete_verified_query(pair_id, _resolve_owner(http_request)):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Verified pair not found."
        )
    return {"deleted": True}


@app.get(
    "/schemas/{db_flag}",
    response_model=SchemaResponse,
    dependencies=[Depends(require_api_key_if_enabled)],
)
async def get_schema(db_flag: str, http_request: Request) -> SchemaResponse:
    """Return the enrolled schema (tables + columns + PKs) for the schema browser."""
    _enforce_db_access(http_request, db_flag)
    import yaml

    index_path = Path(PROJECT_ROOT) / "database_schemas" / db_flag / "schema" / "schema_index.yaml"
    if not index_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No schema indexed for '{db_flag}'. Enroll the database first.",
        )
    try:
        data = yaml.safe_load(index_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to read schema: {exc}",
        ) from exc

    tables = [
        SchemaTable(
            table=str(t.get("table", "")),
            schema_name=str(t.get("schema", "")),
            columns=[str(c) for c in (t.get("column_names") or [])],
            primary_key=[str(c) for c in (t.get("primary_key") or [])],
            has_foreign_keys=bool(t.get("has_foreign_keys", False)),
            description=(t.get("short_description") or None),
        )
        for t in (data.get("tables") or [])
    ]
    return SchemaResponse(db_flag=db_flag, database_name=data.get("database_name"), tables=tables)


@app.post(
    "/run_sql",
    response_model=QueryResponse,
    dependencies=[Depends(require_api_key_if_enabled), Depends(require_csrf)],
)
async def run_sql(request: RunSqlRequest, http_request: Request) -> QueryResponse:
    """Execute user-provided (edited) SQL, bypassing LLM generation.

    The SQL goes through the SAME execution service as generated SQL - AST policy engine, network
    policy, read-only session, row cap - so editing the query cannot widen what it may do.
    """
    _enforce_db_access(http_request, request.db_flag)

    try:
        db_settings = get_user_database_settings(request.db_flag)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown database: {exc!s}"
        ) from exc

    result = _run_read_only(
        request.sql,
        request.db_flag,
        db_settings.model_dump(),
        page=request.page,
        page_size=request.page_size,
        include_total=request.include_total,
    )
    if not result.success:
        logger.info(
            "run_sql refused or failed (%s): %s",
            result.error_category,
            _sanitize(result.error, max_len=300),
        )
    return _response_from(
        result,
        request.sql,
        request.output_format,
        db_flag=request.db_flag,
        http_request=http_request,
    )


@app.post(
    "/query",
    response_model=QueryResponse,
    dependencies=[Depends(require_api_key_if_enabled), Depends(require_csrf)],
)
async def execute_query(request: QueryRequest, http_request: Request) -> QueryResponse:
    """Execute a natural language SQL query.

    Args:
        request: QueryRequest with query, db_flag, and output_format

    Returns:
        QueryResponse with status, SQL, validation result, and formatted data

    Raises:
        HTTPException: If execution fails or database is unavailable
    """
    _enforce_db_access(http_request, request.db_flag)
    try:
        try:
            logger.info(
                "Received query request: query=%s, db_flag=%s, format=%s",
                _sanitize(request.query),
                request.db_flag,
                request.output_format,
            )
        except Exception:
            # Guard against unexpected failures inside log sanitization (should not crash request)
            logger.info(
                "Received query request for db_flag=%s format=%s",
                request.db_flag,
                request.output_format,
            )
        logger.debug(
            "Conversation identifiers user_id=%s session_id=%s",
            _sanitize(request.user_id),
            _sanitize(request.session_id),
        )

        try:
            db_settings = get_user_database_settings(request.db_flag)
        except KeyError as exc:  # pragma: no cover - handled explicitly
            logger.error("Configuration error loading db_flag=%s: %s", request.db_flag, str(exc))
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown database: {exc!s}",
            ) from exc

        db_config = db_settings.model_dump()
        collection_name = default_collection_name(request.db_flag)

        providers = get_available_providers()

        logger.debug("Provider order determined by environment: %s", providers)

        agent_output: dict[str, Any] | None = None
        selected_tables: list[str] = []
        last_error: Exception | None = None
        successful_provider: str | None = None
        follow_up_questions: list[str] | None = None

        # Use context-aware agent if user_id and session_id are provided
        for provider_idx, provider in enumerate(providers):
            try:
                if request.user_id and request.session_id:
                    # Use conversation-aware agent
                    agent = get_cached_agent_with_context(
                        provider,
                        request.db_flag,
                        user_id=request.user_id,
                        session_id=request.session_id,
                        db_type=db_config.get("db_type"),
                        db_description=db_config.get("description"),
                    )
                    logger.debug(
                        f"Using context-aware agent for user={request.user_id}, session={request.session_id}"
                    )
                else:
                    # Use stateless agent (backward compatible)
                    agent = get_cached_agent(
                        provider,
                        request.db_flag,
                        db_type=db_config.get("db_type"),
                        db_description=db_config.get("description"),
                    )
                    logger.debug("Using stateless agent (no user/session context)")

                with agent_context(
                    request.db_flag,
                    collection_name,
                    user_id=request.user_id,
                    session_id=request.session_id,
                ):
                    agent_output = agent.invoke(
                        {
                            "messages": [
                                {
                                    "role": "user",
                                    "content": request.query,
                                }
                            ]
                        }
                    )
                    selected_tables = get_collected_tables()
                logger.info("Generated SQL using provider=%s", provider)
                successful_provider = provider
                break
            except Exception as exc:
                last_error = exc
                error_str = str(exc).lower()
                is_rate_limit = "429" in error_str or ("rate" in error_str and "limit" in error_str)

                # Always surface why a provider failed (previously only rate-limit/provider
                # errors were logged, so genuine failures fell back silently and undiagnosed).
                logger.warning(
                    "Provider %s failed during SQL generation (attempt %d/%d, rate_limit=%s): %s",
                    _sanitize(provider),
                    provider_idx + 1,
                    len(providers),
                    is_rate_limit,
                    _sanitize(str(exc), max_len=400),
                )

                # Continue to next provider if available
                if provider_idx < len(providers) - 1:
                    logger.info("Falling back to next provider: %s", providers[provider_idx + 1])
                    continue

        if agent_output is None:
            detail = (
                f"LLM providers unavailable: {last_error}"
                if last_error
                else "All LLM providers failed"
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=detail,
            )

        structured_llm_response = parse_structured_response(agent_output)
        contextual_insights: str | None = None
        if structured_llm_response:
            raw_output = structured_llm_response.sql_query
            contextual_insights = structured_llm_response.query_context
            # Also strip LLM tags from contextual insights
            try:
                if contextual_insights:
                    import re

                    contextual_insights = re.sub(
                        r"<think[\s\S]*?>[\s\S]*?<\/think>",
                        "",
                        contextual_insights,
                        flags=re.IGNORECASE,
                    )
                    contextual_insights = re.sub(r"<[^>]+>", "", contextual_insights).strip()
            except Exception:
                pass
            # Preserve empty list (do not coerce to None) for API clients that expect an array
            follow_up_questions = structured_llm_response.follow_up_questions
        else:
            raw_output = _extract_agent_output(agent_output)
        sql_generated = _sanitize_sql(raw_output)
        logger.info("Generated SQL (masked): %s", _sanitize(sql_generated, max_len=1000))
        if not sql_generated:
            logger.error("Agent returned empty SQL output")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Agent returned empty SQL output",
            )

        # One path to the database: policy engine -> network policy -> read-only session.
        result = _run_read_only(
            sql_generated,
            request.db_flag,
            db_config,
            page=request.page,
            page_size=request.page_size,
            include_total=request.include_total,
        )
        if not result.success:
            logger.info(
                "Query refused or failed (%s): %s",
                result.error_category,
                _sanitize(result.error, max_len=300),
            )
            return _response_from(
                result,
                sql_generated,
                request.output_format,
                selected_tables=selected_tables,
                follow_up_questions=follow_up_questions,
                db_flag=request.db_flag,
                http_request=http_request,
            )

        elapsed_ms = result.frame.duration_ms or 0.0
        logger.info(
            "Query execution completed: rows=%s truncated=%s elapsed_ms=%.1f",
            result.frame.row_count,
            result.frame.truncated,
            elapsed_ms,
        )

        natural_summary = None
        if successful_provider:
            # The summary model sees deterministic statistics and a bounded, masked row sample -
            # never the full result set (see docs/v2/TARGET_ARCHITECTURE.md, grounded summaries).
            sample = json.dumps(
                (result.frame.to_records() if result.frame else [])[:SUMMARY_SAMPLE_ROWS],
                default=str,
            )
            natural_summary = summarize_query_results(
                successful_provider,
                result.stats.describe_text() if result.stats else "",
                _sanitize(sample, max_len=SUMMARY_SAMPLE_CHARS),
            )
            natural_summary = _clean_summary(natural_summary)
            if not natural_summary:
                logger.debug("No natural summary generated")

        # Store query context in conversation history if user_id and session_id provided
        if request.user_id and request.session_id:
            try:
                store_query_context(
                    user_id=request.user_id,
                    session_id=request.session_id,
                    db_flag=request.db_flag,
                    query_text=request.query,
                    sql_generated=sql_generated,
                    tables_used=selected_tables or [],
                    follow_up_questions=follow_up_questions or [],
                    contextual_insights=contextual_insights,
                    execution_time=elapsed_ms / 1000.0 if elapsed_ms else None,
                )
                # Update session summary with new context
                update_or_create_session_summary(
                    user_id=request.user_id,
                    session_id=request.session_id,
                    db_flag=request.db_flag,
                )
                session_summary = get_session_summary(
                    user_id=request.user_id,
                    session_id=request.session_id,
                    db_flag=request.db_flag,
                )
                if session_summary:
                    logger.debug(
                        "Session summary updated for user=%s, session=%s: %s",
                        _sanitize(request.user_id),
                        _sanitize(request.session_id),
                        _sanitize(session_summary.get("summary"), max_len=500),
                    )
                logger.debug(
                    "Stored query context for user=%s, session=%s",
                    _sanitize(request.user_id),
                    _sanitize(request.session_id),
                )
            except Exception as exc:
                logger.warning(
                    "Failed to store conversation history: %s", _sanitize(str(exc), max_len=500)
                )
        else:
            logger.debug(
                "Skipping conversation persistence (missing identifiers) user_id=%s session_id=%s",
                _sanitize(request.user_id),
                _sanitize(request.session_id),
            )

        return _response_from(
            result,
            sql_generated,
            request.output_format,
            selected_tables=selected_tables,
            follow_up_questions=follow_up_questions,
            natural_summary=natural_summary,
            db_flag=request.db_flag,
            http_request=http_request,
        )

    except ValueError as e:
        logger.error("Validation error: %s", str(e))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid request: {e!s}",
        ) from e
    except Exception as e:
        logger.exception(
            "Unexpected error during query execution: %s", _sanitize(str(e), max_len=800)
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {e!s}",
        ) from e


@app.post(
    "/schemas/embeddings",
    response_model=SchemaEmbeddingResponse,
    dependencies=[Depends(require_api_key), Depends(require_csrf)],
)
async def generate_schema_embeddings(request: SchemaEmbeddingRequest) -> SchemaEmbeddingResponse:
    """Convert every schema YAML into embeddings stored in Postgres."""

    logger.info("Generating schema embeddings for db_flag=%s", request.db_flag)
    connection_string = getenv("POSTGRES_CONNECTION_STRING")
    if not connection_string:
        logger.error("Postgres connection string missing for embeddings")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Missing POSTGRES_CONNECTION_STRING",
        )

    try:
        settings = SchemaEmbeddingSettings(
            schema_root=SchemaEmbeddingPipeline.DEFAULT_SCHEMA_ROOT,
            minimal_output_root=SchemaEmbeddingPipeline.DEFAULT_OUTPUT_ROOT,
            collection_name=request.collection_name,
        )
        pipeline = SchemaEmbeddingPipeline(
            db_flag=request.db_flag,
            connection_string=connection_string,
            settings=settings,
        )
        result = pipeline.run()
        output_directory = pipeline.settings.minimal_output_root / request.db_flag

        message = (
            "Embeddings stored successfully"
            if result.document_chunks > 0
            else "No schema files were processed"
        )

        return SchemaEmbeddingResponse(
            db_flag=request.db_flag,
            output_directory=str(output_directory),
            processed_files=[path.name for path in result.minimal_files],
            message=message,
        )
    except HTTPException:
        raise
    except Exception as error:
        logger.exception("Schema embedding pipeline failed: %s", _sanitize(str(error), max_len=600))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Schema embedding pipeline failed: {error}",
        ) from error


@app.post(
    "/schemas/enroll",
    response_model=SchemaPipelineResponse,
    dependencies=[Depends(require_csrf)],
)
async def enroll_database(
    request: SchemaPipelineRequest,
    owner_id: int | None = Depends(resolve_enroll_owner),
) -> SchemaPipelineResponse:
    """Enroll and extract a database schema, run documentation and embeddings."""
    logger.info("Running schema pipeline for db_flag=%s", request.db_flag)
    # POSTGRES_CONNECTION_STRING is now handled internally by the orchestrator

    try:
        project_connection = get_project_db_connection_string()
        create_metadata_tables(project_connection)
        db_row = _fetch_or_create_database_config(request, project_connection, owner_id=owner_id)
        # Verify the credential cannot write, and fail CLOSED when that cannot be established.
        # The previous version initialised this to True and left it True when the probe raised,
        # so a connection whose privileges could not be read was enrolled as if it were safe.
        try:
            resolved = read_connection_string(
                secret=db_row.connection_secret, plaintext=db_row.connection_string
            )
            report = verify_read_only(resolved, request.db_type)
            read_only_ok = report.is_safe
            read_only_msg = f"{report.status.value}: {report.message}"
        except Exception as check_exc:
            read_only_ok = False
            read_only_msg = f"the privilege check itself failed ({type(check_exc).__name__})"
            logger.warning(
                "Read-only verification errored for db_flag=%s: %s", request.db_flag, check_exc
            )
        if not read_only_ok:
            logger.warning(
                "Refusing to enroll db_flag=%s — connection appears writable: %s",
                request.db_flag,
                read_only_msg,
            )
            _audit(
                AuditAction.CONNECTION_ENROLL_REFUSED,
                subject=request.db_flag,
                outcome=AuditOutcome.DENIED,
                user_id=owner_id,
                reason=read_only_msg,
                detail={"dialect": request.db_type or "unknown"},
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Refusing to enroll '{request.db_flag}': the connection appears writable "
                    f"({read_only_msg}). Provide a read-only database role."
                ),
            )

    except ConnectionSecretError as err:
        # Misconfiguration, not a server fault: tell the operator exactly what to set.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err)) from err
    except SQLAlchemyError as err:
        logger.error("DatabaseConfig check/insert failed: %s", err)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"DatabaseConfig check/insert failed: {err}",
        ) from err

    if db_row.schema_extracted and not request.incremental_documentation:
        logger.info(
            "Schema already extracted for db_flag=%s and incremental=False. Skipping.",
            request.db_flag,
        )
        extraction_output = PROJECT_ROOT / "database_schemas" / request.db_flag / "schema"
        extraction_summary = ExtractionStageSummary(
            status="success",
            output_directory=str(extraction_output),
            tables_exported=0,
            message="Database already enrolled and schema extraction is up to date",
        )
        documentation_stage = DocumentationStageSummary(
            status="skipped",
            tables_total=0,
            documented=0,
            failed=0,
            message="Documentation skipped because schema already exists",
        )
        embeddings_stage = EmbeddingStageSummary(
            status="skipped",
            minimal_files=0,
            document_chunks=0,
            output_directory=str(SchemaEmbeddingPipeline.DEFAULT_OUTPUT_ROOT / request.db_flag),
            message="Embedding skipped because schema already exists",
        )
        report = _build_pipeline_report(extraction_summary, documentation_stage, embeddings_stage)
        return SchemaPipelineResponse(
            db_flag=request.db_flag,
            extraction=extraction_summary,
            documentation=documentation_stage,
            embeddings=embeddings_stage,
            report=report,
        )

    if db_row.schema_extracted:
        logger.info(
            "Database %s already enrolled. Proceeding with update/refresh (incremental=True).",
            request.db_flag,
        )

    # Now run the pipeline as before
    try:
        orchestrator = SchemaPipelineOrchestrator(
            request.db_flag,
            include_schemas=request.include_schemas,
            exclude_schemas=request.exclude_schemas,
            run_documentation=request.run_documentation,
            incremental_documentation=request.incremental_documentation,
            run_embeddings=request.run_embeddings,
        )
        outcome = orchestrator.run()

        extraction_summary = ExtractionStageSummary(
            status="success",
            output_directory=str(outcome.extraction_output),
            tables_exported=outcome.tables_exported,
            message="Schema extraction completed",
        )

        logger.info("Schema extraction completed: tables_exported=%d", outcome.tables_exported)

        if request.run_documentation:
            doc_summary = outcome.documentation_summary
            if doc_summary is None:
                documentation_stage = DocumentationStageSummary(
                    status="failed",
                    tables_total=0,
                    documented=0,
                    failed=0,
                    message="Documentation stage did not produce a summary",
                )
            else:
                documentation_stage = DocumentationStageSummary(
                    status="success",
                    tables_total=doc_summary.tables_total,
                    documented=doc_summary.documented,
                    failed=doc_summary.failed,
                    message="Documentation completed",
                )
        else:
            documentation_stage = DocumentationStageSummary(
                status="skipped",
                tables_total=0,
                documented=0,
                failed=0,
                message="Documentation stage was skipped",
            )

        embeddings_output_dir = SchemaEmbeddingPipeline.DEFAULT_OUTPUT_ROOT / request.db_flag
        if request.run_embeddings:
            embedding_result = outcome.embedding_result
            if embedding_result is None:
                embeddings_stage = EmbeddingStageSummary(
                    status="failed",
                    minimal_files=0,
                    document_chunks=0,
                    output_directory=str(embeddings_output_dir),
                    message="Embedding stage did not produce results",
                )
            else:
                embeddings_stage = EmbeddingStageSummary(
                    status="success",
                    minimal_files=len(embedding_result.minimal_files),
                    document_chunks=embedding_result.document_chunks,
                    output_directory=str(embeddings_output_dir),
                    message="Embedding stage completed",
                )
        else:
            embeddings_stage = EmbeddingStageSummary(
                status="skipped",
                minimal_files=0,
                document_chunks=0,
                output_directory=str(embeddings_output_dir),
                message="Embedding stage was skipped",
            )

        # Runs on every successful enrollment. It used to sit inside the `else` above, so on the
        # normal path (run_embeddings=True) the flag was never set: the "already enrolled" fast
        # path could never trigger and every re-enroll repeated extraction, per-table LLM
        # documentation and re-embedding.
        _mark_schema_extracted(request.db_flag)

        _audit(
            AuditAction.CONNECTION_ENROLLED,
            subject=request.db_flag,
            outcome=AuditOutcome.SUCCESS,
            user_id=owner_id,
            detail={
                "dialect": request.db_type or "unknown",
                "tables": extraction_summary.tables_exported,
            },
        )

        report = _build_pipeline_report(extraction_summary, documentation_stage, embeddings_stage)
        return SchemaPipelineResponse(
            db_flag=request.db_flag,
            extraction=extraction_summary,
            documentation=documentation_stage,
            embeddings=embeddings_stage,
            report=report,
        )
    except HTTPException:
        raise
    except Exception as error:
        logger.exception("Schema pipeline failed: %s", _sanitize(str(error), max_len=600))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Schema pipeline failed: {error}",
        ) from error


def _fetch_or_create_database_config(
    request: SchemaPipelineRequest, project_connection: str, owner_id: int | None = None
) -> DatabaseConfig:
    session = get_session(project_connection)
    try:
        db_row = session.query(DatabaseConfig).filter_by(db_flag=request.db_flag).first()
        if db_row:
            # A db_flag belongs to whoever enrolled it. Handing the existing row back to any caller
            # let one tenant re-run another tenant's pipeline against that tenant's credential -
            # recorded as G-8 in docs/v2/THREAT_MODEL.md. A public row (owner_id NULL, e.g. the
            # demo) stays shared on purpose; an owned one does not.
            if db_row.owner_id is not None and db_row.owner_id != owner_id:
                _audit(
                    AuditAction.CONNECTION_ENROLL_REFUSED,
                    subject=request.db_flag,
                    outcome=AuditOutcome.DENIED,
                    user_id=owner_id,
                    reason="the identifier is already enrolled by another owner",
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="This database identifier is already enrolled by another owner.",
                )
            return db_row

        stored = prepare_for_storage(
            request.connection_string,
            encryption_required=get_settings().mode_policy.secret_encryption_required,
        )
        db_row = DatabaseConfig(
            db_flag=request.db_flag,
            db_type=request.db_type,
            # Plaintext only when the mode permits it and no key is configured; the column is
            # NOT NULL, so an encrypted row stores a placeholder rather than the real DSN.
            connection_string=stored.plaintext or "",
            connection_secret=stored.secret,
            description=request.description,
            intro_template=request.intro_template,
            exclude_column_matches=request.exclude_column_matches,
            owner_id=owner_id,
            # Set defaults internally for removed fields
            max_rows=10000,
            query_timeout=30,
        )
        session.add(db_row)
        try:
            session.commit()
            session.refresh(db_row)
            logger.info("Inserted new DatabaseConfig for db_flag=%s", request.db_flag)
        except IntegrityError:
            session.rollback()
            db_row = session.query(DatabaseConfig).filter_by(db_flag=request.db_flag).first()
            if not db_row:
                raise
        return db_row
    finally:
        session.close()


def _mark_schema_extracted(db_flag: str) -> None:
    session = get_session(get_project_db_connection_string())
    try:
        db_row = session.query(DatabaseConfig).filter_by(db_flag=db_flag).first()
        if not db_row:
            return
        db_row.schema_extracted = True
        db_row.schema_extraction_date = datetime.utcnow()
        session.commit()
    finally:
        session.close()


def _build_pipeline_report(
    extraction_summary: ExtractionStageSummary,
    documentation_stage: DocumentationStageSummary,
    embeddings_stage: EmbeddingStageSummary,
) -> SchemaPipelineReport:
    documentation_skipped = max(
        0,
        documentation_stage.tables_total
        - documentation_stage.documented
        - documentation_stage.failed,
    )
    return SchemaPipelineReport(
        extracted_files=extraction_summary.tables_exported,
        documentation_tables_total=documentation_stage.tables_total,
        documentation_documented=documentation_stage.documented,
        documentation_failed=documentation_stage.failed,
        documentation_skipped=documentation_skipped,
        embeddings_minimal_files=embeddings_stage.minimal_files,
        embeddings_document_chunks=embeddings_stage.document_chunks,
    )


@app.get("/")
async def root():
    """Root endpoint with API documentation link."""
    return {
        "message": "SQL Insight Agent API",
        "docs": "/docs",
        "health": "/health",
        "endpoints": {
            "POST /query": "Execute natural language SQL query",
            "POST /schemas/embeddings": "Convert schema YAML definitions to embeddings",
            "POST /schemas/enroll": "Enroll a database, extract schema, document, and embed",
            "GET /health": "Health check",
        },
    }


if __name__ == "__main__":
    import uvicorn

    logger.info("Starting SQL Insight Agent API server")
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
        log_level="info",
    )
