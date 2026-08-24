"""The v2 API: run a question through the graph, pause for a human, resume, and read the trace.

Why this exists alongside ``/query``: the v1 endpoint has a fixed request/response contract that
existing clients depend on, and it cannot express the things v2 adds - a run that *pauses*, an
approval bound to a SQL fingerprint, an evidence checklist, a step-by-step trace. Rather than break
that contract, v1 keeps working and v2 exposes the full pipeline.

A run is identified by its ``run_id``, which is also the LangGraph thread id. That is what makes
``POST /v2/runs/{run_id}/resume`` work after a restart: the state lives in the checkpointer, not in
this process.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from langgraph.types import Command
from pydantic import BaseModel, Field

from app.api.v2 import deps as v2deps
from app.core.config import get_settings
from app.graph.investigation import (
    MAX_SUBQUESTIONS,
    compile_investigation_graph,
    initial_investigation_state,
    investigation_payload,
)
from app.graph.query_graph import answer_payload, compile_query_graph, pending_interrupt
from app.graph.state import initial_state
from app.security.auth import require_api_key_if_enabled
from app.security.csrf import require_csrf

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v2", tags=["v2"])


# ---------------------------------------------------------------------------------------------
# Contracts
# ---------------------------------------------------------------------------------------------


class QueryV2Request(BaseModel):
    question: str = Field(
        ..., min_length=1, description="The analytical question, in plain English"
    )
    db_flag: str = Field(..., min_length=1, description="Which enrolled database to ask")
    plan_only: bool = Field(
        False, description="Stop for review after the SQL is written, before it runs"
    )
    user_id: str | None = None
    session_id: str | None = None
    run_id: str | None = Field(
        None, description="Supply to make the run resumable under a known id; generated otherwise"
    )


class ResumeRequest(BaseModel):
    """What the human decided. ``answer`` for a clarification, the rest for an approval."""

    answer: str | None = Field(None, description="Reply to a clarification question")
    approved: bool | None = Field(None, description="Approve or reject the proposed SQL")
    sql: str | None = Field(
        None, description="Edited SQL. It is re-validated from scratch, never trusted as approved"
    )


class InterruptPayload(BaseModel):
    type: Literal["clarification", "approval"]
    question: str | None = None
    reason: str | None = None
    sql: str | None = None
    fingerprint: str | None = None
    tables: list[str] = Field(default_factory=list)
    rationale: str | None = None
    original_question: str | None = None


class RunResponse(BaseModel):
    run_id: str
    status: str
    awaiting: InterruptPayload | None = None
    sql: str | None = None
    executed_sql: str | None = None
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int | None = None
    truncated: bool | None = None
    tables: list[str] = Field(default_factory=list)
    answer: str | None = None
    observations: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    follow_ups: list[str] = Field(default_factory=list)
    chart: dict[str, Any] | None = None
    shape: dict[str, Any] | None = None
    evidence: dict[str, Any] | None = None
    policy: dict[str, Any] | None = None
    retrieval: dict[str, Any] | None = None
    routing: list[dict[str, Any]] = Field(default_factory=list)
    versions: dict[str, str] = Field(default_factory=dict)
    steps: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None
    error_category: str | None = None


class InvestigateRequest(BaseModel):
    question: str = Field(..., min_length=1, description="A broad 'why / what changed' question")
    db_flag: str = Field(..., min_length=1)
    max_subquestions: int = Field(
        MAX_SUBQUESTIONS, ge=1, le=MAX_SUBQUESTIONS, description="Ceiling on the plan size"
    )
    auto_approve_plan: bool = Field(
        False, description="Skip the plan review. Off by default: the plan is the control."
    )
    user_id: str | None = None
    run_id: str | None = None


class InvestigationResponse(BaseModel):
    run_id: str
    status: str
    awaiting: dict[str, Any] | None = None
    question: str | None = None
    plan: dict[str, Any] | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    executive_finding: str | None = None
    observations: list[dict[str, Any]] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    uncited_statements: list[str] = Field(
        default_factory=list,
        description="Statements the model made that cite no evidence we actually hold",
    )
    causal_warnings: list[str] = Field(
        default_factory=list, description="Causal phrasing a query result cannot support"
    )
    versions: dict[str, str] = Field(default_factory=dict)
    steps: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None


class ModelHealthResponse(BaseModel):
    mode: str
    egress_policy: str
    enabled_profiles: list[str]
    excluded: dict[str, str]
    breakers: dict[str, str]
    providers: dict[str, dict[str, Any]]
    registry_version: str


# ---------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------


def _to_response(run_id: str, result: dict[str, Any]) -> RunResponse:
    payload = answer_payload(result)
    interrupt = pending_interrupt(result)
    return RunResponse(
        run_id=run_id,
        status="awaiting_input" if interrupt else str(payload.get("status") or "unknown"),
        awaiting=InterruptPayload(**interrupt) if interrupt else None,
        sql=payload.get("sql"),
        executed_sql=payload.get("executed_sql"),
        columns=payload.get("columns") or [],
        rows=payload.get("rows") or [],
        row_count=payload.get("row_count"),
        truncated=payload.get("truncated"),
        tables=payload.get("tables") or [],
        answer=payload.get("answer"),
        observations=payload.get("observations") or [],
        caveats=payload.get("caveats") or [],
        assumptions=payload.get("assumptions") or [],
        follow_ups=payload.get("follow_ups") or [],
        chart=payload.get("chart"),
        shape=payload.get("shape"),
        evidence=payload.get("evidence"),
        policy=payload.get("policy"),
        retrieval=payload.get("retrieval"),
        routing=payload.get("routing") or [],
        versions=payload.get("versions") or {},
        steps=payload.get("steps") or [],
        error=payload.get("error"),
        error_category=payload.get("error_category"),
    )


def _graph_for(source_id: str):
    try:
        graph_deps = v2deps.build_graph_deps(source_id)
    except v2deps.UnknownDataSource as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unknown or un-enrolled database '{source_id}'. "
                "Enroll it with POST /schemas/enroll first."
            ),
        ) from exc
    return compile_query_graph(graph_deps, checkpointer=v2deps.get_checkpointer().saver)


def _enforce_access(http_request: Request, db_flag: str) -> None:
    """Reuse the v1 tenancy gate so both APIs answer the same access question."""
    from app.main import _enforce_db_access

    _enforce_db_access(http_request, db_flag)


# ---------------------------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------------------------


@router.post(
    "/query",
    response_model=RunResponse,
    dependencies=[Depends(require_api_key_if_enabled), Depends(require_csrf)],
    summary="Ask a question; the run may pause for clarification or approval",
)
def query_v2(request: QueryV2Request, http_request: Request) -> RunResponse:
    _enforce_access(http_request, request.db_flag)
    run_id = request.run_id or f"run-{uuid.uuid4().hex[:16]}"
    graph = _graph_for(request.db_flag)

    state = initial_state(
        question=request.question,
        source_id=request.db_flag,
        user_id=request.user_id,
        plan_only=request.plan_only,
    )
    try:
        result = graph.invoke(state, {"configurable": {"thread_id": run_id}})
    except Exception as exc:
        logger.exception("v2 query failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"The run failed: {type(exc).__name__}",
        ) from exc
    return _to_response(run_id, result)


@router.post(
    "/runs/{run_id}/resume",
    response_model=RunResponse,
    dependencies=[Depends(require_api_key_if_enabled), Depends(require_csrf)],
    summary="Answer a clarification or decide on an approval, and continue the run",
)
def resume_run(run_id: str, request: ResumeRequest, http_request: Request) -> RunResponse:
    snapshot, graph = _load_run(run_id, http_request)
    if not snapshot.next:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This run is not waiting for input; it has already finished.",
        )

    if request.approved is not None or request.sql is not None:
        resume_value: Any = {"approved": bool(request.approved), "sql": request.sql}
    elif request.answer is not None:
        resume_value = request.answer
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Send either 'answer' (for a clarification) or 'approved' (for an approval).",
        )

    try:
        result = graph.invoke(Command(resume=resume_value), {"configurable": {"thread_id": run_id}})
    except Exception as exc:
        logger.exception("v2 resume failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Resuming the run failed: {type(exc).__name__}",
        ) from exc
    return _to_response(run_id, result)


@router.get(
    "/runs/{run_id}",
    response_model=RunResponse,
    dependencies=[Depends(require_api_key_if_enabled)],
    summary="Read a run's current state, including its trace",
)
def get_run(run_id: str, http_request: Request) -> RunResponse:
    snapshot, _graph = _load_run(run_id, http_request)
    values = dict(snapshot.values)
    if snapshot.next:
        # get_state does not replay the interrupt payload, so reconstruct what is being asked.
        values["__interrupt__"] = _interrupt_from_state(values)
    return _to_response(run_id, values)


def _interrupt_from_state(values: dict[str, Any]) -> list[Any]:
    if values.get("clarification_question"):
        payload = {
            "type": "clarification",
            "question": values["clarification_question"],
            "original_question": values.get("question"),
            "tables": values.get("tables", []),
        }
    else:
        policy = values.get("policy") or {}
        payload = {
            "type": "approval",
            "reason": values.get("approval_reason", ""),
            "sql": values.get("sql", ""),
            "tables": values.get("tables", []),
            "fingerprint": policy.get("fingerprint"),
            "rationale": values.get("rationale", ""),
        }

    class _Interrupt:
        value = payload

    return [_Interrupt()]


def _load_run(run_id: str, http_request: Request):
    """Fetch a run's snapshot and re-check that this caller may see its database."""
    saver = v2deps.get_checkpointer().saver
    config = {"configurable": {"thread_id": run_id}}

    # The graph object needs a source to build deps, and the source is recorded in the state - so
    # read the checkpoint first with a throwaway graph bound to nothing expensive.
    checkpoint = saver.get_tuple(config)
    if checkpoint is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such run.")
    source_id = (checkpoint.checkpoint.get("channel_values") or {}).get("source_id")
    if not source_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such run.")

    # Tenancy is re-checked on every resume: a run id is not an access grant.
    _enforce_access(http_request, str(source_id))

    graph = _graph_for(str(source_id))
    return graph.get_state(config), graph


def _investigation_response(run_id: str, result: dict[str, Any]) -> InvestigationResponse:
    payload = investigation_payload(result)
    interrupt = pending_interrupt(result)
    return InvestigationResponse(
        run_id=run_id,
        status="awaiting_input" if interrupt else str(payload.get("status") or "unknown"),
        awaiting=interrupt,
        question=payload.get("question"),
        plan=payload.get("plan"),
        evidence=payload.get("evidence") or [],
        executive_finding=payload.get("executive_finding"),
        observations=payload.get("observations") or [],
        contradictions=payload.get("contradictions") or [],
        assumptions=payload.get("assumptions") or [],
        limitations=payload.get("limitations") or [],
        next_steps=payload.get("next_steps") or [],
        uncited_statements=payload.get("uncited_statements") or [],
        causal_warnings=payload.get("causal_warnings") or [],
        versions=payload.get("versions") or {},
        steps=payload.get("steps") or [],
        error=payload.get("error"),
    )


@router.post(
    "/investigate",
    response_model=InvestigationResponse,
    dependencies=[Depends(require_api_key_if_enabled), Depends(require_csrf)],
    summary="Investigate a broad question as a bounded, evidence-linked plan",
)
def investigate(request: InvestigateRequest, http_request: Request) -> InvestigationResponse:
    _enforce_access(http_request, request.db_flag)
    run_id = request.run_id or f"inv-{uuid.uuid4().hex[:16]}"
    saver = v2deps.get_checkpointer().saver
    try:
        graph_deps = v2deps.build_graph_deps(request.db_flag)
    except v2deps.UnknownDataSource as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown or un-enrolled database '{request.db_flag}'.",
        ) from exc

    graph = compile_investigation_graph(graph_deps, checkpointer=saver)
    state = initial_investigation_state(
        question=request.question,
        source_id=request.db_flag,
        run_id=run_id,
        user_id=request.user_id,
        max_subquestions=request.max_subquestions,
        auto_approve_plan=request.auto_approve_plan,
    )
    try:
        result = graph.invoke(state, {"configurable": {"thread_id": run_id}})
    except Exception as exc:
        logger.exception("v2 investigation failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"The investigation failed: {type(exc).__name__}",
        ) from exc
    return _investigation_response(run_id, result)


@router.post(
    "/investigations/{run_id}/resume",
    response_model=InvestigationResponse,
    dependencies=[Depends(require_api_key_if_enabled), Depends(require_csrf)],
    summary="Approve, edit or reject an investigation plan",
)
def resume_investigation(
    run_id: str, request: dict[str, Any], http_request: Request
) -> InvestigationResponse:
    saver = v2deps.get_checkpointer().saver
    config = {"configurable": {"thread_id": run_id}}
    checkpoint = saver.get_tuple(config)
    if checkpoint is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such investigation.")
    source_id = (checkpoint.checkpoint.get("channel_values") or {}).get("source_id")
    if not source_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such investigation.")

    _enforce_access(http_request, str(source_id))
    graph = compile_investigation_graph(v2deps.build_graph_deps(str(source_id)), checkpointer=saver)
    if not graph.get_state(config).next:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This investigation is not waiting for a decision.",
        )

    resume_value = {
        "approved": bool(request.get("approved")),
        "sub_questions": request.get("sub_questions"),
    }
    result = graph.invoke(Command(resume=resume_value), config)
    return _investigation_response(run_id, result)


class EnrollJobRequest(BaseModel):
    """Enroll a database in the background instead of holding an HTTP request open for minutes."""

    db_flag: str = Field(..., min_length=1)
    run_documentation: bool = True
    incremental_documentation: bool = True
    run_embeddings: bool = True
    include_schemas: list[str] | None = None
    exclude_schemas: list[str] | None = None


class JobResponse(BaseModel):
    job_id: str
    kind: str
    status: str
    source_id: str | None = None
    attempts: int = 0
    percent: int = 0
    stages_done: int = 0
    stages_total: int = 0
    stages: list[dict[str, Any]] = Field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


def _job_session():
    from db.database_manager import get_project_db_connection_string, get_session

    return get_session(get_project_db_connection_string())


@router.post(
    "/connections/{db_flag}/enroll",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_api_key_if_enabled), Depends(require_csrf)],
    summary="Queue an enrollment; poll the returned job for progress",
)
def enqueue_enrollment(
    db_flag: str, request: EnrollJobRequest, http_request: Request
) -> JobResponse:
    _enforce_access(http_request, db_flag)

    from app.jobs import queue as jobq
    from app.jobs.handlers import register_all
    from app.jobs.runner import progress

    register_all()
    session = _job_session()
    try:
        jobq.ensure_tables(str(session.bind.url))
        job = jobq.enqueue(
            session,
            kind="enroll",
            source_id=db_flag,
            payload={
                "source_id": db_flag,
                "run_documentation": request.run_documentation,
                "incremental_documentation": request.incremental_documentation,
                "run_embeddings": request.run_embeddings,
                "include_schemas": request.include_schemas,
                "exclude_schemas": request.exclude_schemas,
            },
        )
        return JobResponse(**{**progress(session, job.job_id), "stages": []})
    finally:
        session.close()


@router.get(
    "/jobs/{job_id}",
    response_model=JobResponse,
    dependencies=[Depends(require_api_key_if_enabled)],
    summary="How far a background job has got",
)
def get_job_progress(job_id: str, http_request: Request) -> JobResponse:
    from app.jobs import queue as jobq
    from app.jobs.handlers import register_all
    from app.jobs.runner import progress

    register_all()
    session = _job_session()
    try:
        job = jobq.get_job(session, job_id)
        if job is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such job.")
        if job.source_id:
            # A job id is not an access grant: re-check who may see this data source.
            _enforce_access(http_request, job.source_id)
        return JobResponse(**progress(session, job_id))
    finally:
        session.close()


@router.get("/models/health", response_model=ModelHealthResponse, summary="Which models are usable")
def model_health() -> ModelHealthResponse:
    """Answers 'why did it pick that model?' and 'why is nothing available?' without a query."""
    from app.llm.registry import registry_version
    from app.llm.service import get_router, health_report

    settings = get_settings()
    described = get_router().describe()
    return ModelHealthResponse(
        mode=settings.mode.value,
        egress_policy=settings.effective_egress_policy.value,
        enabled_profiles=list(described["enabled_profiles"]),
        excluded=dict(described["excluded"]),
        breakers=dict(described["breakers"]),
        providers={
            name: {
                "available": health.available,
                "detail": health.detail,
                "models": health.models[:10],
            }
            for name, health in health_report().items()
        },
        registry_version=registry_version(),
    )


__all__ = ["router"]
