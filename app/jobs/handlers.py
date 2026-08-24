"""Job handlers: enrollment and re-embedding, expressed as resumable stages.

Enrollment is the one that mattered. It used to be a single synchronous call inside the HTTP
request; here it is a sequence of named stages, each idempotent, so a worker restart resumes rather
than repeating the model calls.

The stage list is also documentation: reading it tells you exactly what enrolling a database does,
in order, which the previous monolithic call did not.
"""

from __future__ import annotations

import logging
from typing import Any

from app.jobs.runner import JobHandler, Stage, StageFailed, register

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------------------------
# Enrollment
# ---------------------------------------------------------------------------------------------


def _source_id(payload: dict[str, Any]) -> str:
    source_id = str(payload.get("source_id") or payload.get("db_flag") or "").strip()
    if not source_id:
        raise StageFailed("The job payload names no data source.", retryable=False)
    return source_id


def _verify_read_only(payload: dict[str, Any], _context: dict[str, Any]) -> dict[str, Any]:
    """Refuse to go further unless the credential is verifiably unable to write."""
    from app.api.v2.deps import UnknownDataSource, resolve_source
    from app.execution.readonly import verify_read_only

    source_id = _source_id(payload)
    try:
        target = resolve_source(source_id)
    except (UnknownDataSource, KeyError) as exc:
        raise StageFailed(f"Data source '{source_id}' is not registered.", retryable=False) from exc

    report = verify_read_only(target.connection_string, target.dialect)
    if not report.is_safe:
        raise StageFailed(
            f"Refusing to enroll '{source_id}': {report.status.value} - {report.message}",
            retryable=False,
        )
    return {"status": report.status.value, "detail": report.message}


def _extract_schema(payload: dict[str, Any], _context: dict[str, Any]) -> dict[str, Any]:
    """Reflect the schema and write the YAML artefacts. Idempotent: the writer merges."""
    from app.schema_pipeline.orchestrator import SchemaPipelineOrchestrator

    source_id = _source_id(payload)
    orchestrator = SchemaPipelineOrchestrator(
        source_id,
        include_schemas=payload.get("include_schemas"),
        exclude_schemas=payload.get("exclude_schemas"),
        run_documentation=False,
        run_embeddings=False,
    )
    outcome = orchestrator.run()
    return {
        "tables_exported": outcome.tables_exported,
        "output": str(outcome.extraction_output),
        "detail": f"{outcome.tables_exported} table(s) extracted",
    }


def _document_schema(payload: dict[str, Any], _context: dict[str, Any]) -> dict[str, Any]:
    """Optional per-table model documentation. The expensive stage, hence its own resume point."""
    from app.schema_pipeline.orchestrator import SchemaPipelineOrchestrator

    source_id = _source_id(payload)
    orchestrator = SchemaPipelineOrchestrator(
        source_id,
        run_documentation=True,
        incremental_documentation=bool(payload.get("incremental_documentation", True)),
        run_embeddings=False,
    )
    outcome = orchestrator.run()
    summary = outcome.documentation_summary
    if summary is None:
        return {"detail": "documentation produced no summary"}
    return {
        "tables_total": summary.tables_total,
        "documented": summary.documented,
        "failed": summary.failed,
        "detail": f"{summary.documented}/{summary.tables_total} table(s) documented",
    }


def _build_index(payload: dict[str, Any], _context: dict[str, Any]) -> dict[str, Any]:
    """Embed and index the enrolled schema for retrieval."""
    from app.api.v2.deps import build_index, invalidate_index

    source_id = _source_id(payload)
    invalidate_index(source_id)
    index = build_index(source_id)
    stats = index.stats()
    return {
        "documents": stats["documents"],
        "embedding_versions": stats["embedding_versions"],
        "detail": f"{stats['documents']} document(s) indexed",
    }


def _snapshot_schema(payload: dict[str, Any], _context: dict[str, Any]) -> dict[str, Any]:
    """Fingerprint the schema *as it stands* so the next stage can tell what extraction changed.

    Runs before extraction, and only reads artefacts already on disk. On a first enrollment there is
    nothing to read, and the empty result is what tells the drift check it has no baseline.
    """
    from app.schema_pipeline.drift import fingerprint_schema

    before = fingerprint_schema(_source_id(payload))
    return {"fingerprints": before, "detail": f"{len(before)} table(s) previously known"}


def _detect_drift(payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Compare the re-extracted schema against the snapshot and retire pairs the change invalidates.

    This is the half of the verified-query flywheel that keeps it honest: an approval is an approval
    *against a schema*, so when the schema moves, the approval has to be re-earned rather than
    quietly continuing to vouch for SQL that may no longer be correct.
    """
    from app.schema_pipeline.drift import apply_to_verified_queries, diff, fingerprint_schema

    source_id = _source_id(payload)
    before = (context.get("snapshot_schema") or {}).get("fingerprints") or {}
    report = diff(before, fingerprint_schema(source_id))

    retired = 0
    try:
        retired = apply_to_verified_queries(source_id, report)
    except Exception as exc:  # drift reporting must never block an enrollment
        logger.warning("Could not retire verified pairs for %s: %s", source_id, exc)

    if report.has_drift:
        logger.info("Schema drift on %s: %s", source_id, report.describe())
    return {
        **report.as_dict(),
        "verified_pairs_retired": retired,
        "detail": f"{report.describe()}; {retired} verified pair(s) sent back for review",
    }


def _mark_enrolled(payload: dict[str, Any], _context: dict[str, Any]) -> dict[str, Any]:
    """Record that the schema is extracted, so a re-enroll can take the fast path."""
    from datetime import UTC, datetime

    from db.database_manager import get_project_db_connection_string, get_session
    from db.model import DatabaseConfig

    source_id = _source_id(payload)
    session = get_session(get_project_db_connection_string())
    try:
        row = session.query(DatabaseConfig).filter_by(db_flag=source_id).first()
        if row is None:
            raise StageFailed(f"'{source_id}' disappeared during enrollment.", retryable=False)
        row.schema_extracted = True
        row.schema_extraction_date = datetime.now(UTC)
        session.commit()
    finally:
        session.close()
    return {"detail": "marked as enrolled"}


ENROLL_HANDLER = JobHandler(
    kind="enroll",
    stages=[
        Stage("verify_read_only", _verify_read_only),
        Stage("snapshot_schema", _snapshot_schema),
        Stage("extract_schema", _extract_schema),
        Stage("detect_drift", _detect_drift),
        Stage(
            "document_schema",
            _document_schema,
            when=lambda payload: bool(payload.get("run_documentation", True)),
        ),
        Stage(
            "build_index",
            _build_index,
            when=lambda payload: bool(payload.get("run_embeddings", True)),
        ),
        Stage("mark_enrolled", _mark_enrolled),
    ],
)


# ---------------------------------------------------------------------------------------------
# Re-embedding
# ---------------------------------------------------------------------------------------------

REEMBED_HANDLER = JobHandler(
    kind="reembed",
    stages=[Stage("build_index", _build_index)],
)


def register_all() -> list[str]:
    """Register every handler. Called by the worker at start-up and by tests."""
    from app.jobs.runner import registered_kinds

    register(ENROLL_HANDLER)
    register(REEMBED_HANDLER)
    return registered_kinds()


__all__ = ["ENROLL_HANDLER", "REEMBED_HANDLER", "register_all"]
