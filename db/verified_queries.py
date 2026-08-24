"""Verified question -> SQL pairs: the human-approved flywheel, with a lifecycle.

A pair is a reviewer saying "this SQL correctly answers this question **for this schema**". The
qualifier is the part that used to be missing: a pair saved in June against a schema that changed in
August was still being handed to the model in September as a verified example.

So a pair now records the snapshot it was approved against and a literal-independent fingerprint of
its SQL, and it has a status:

``draft`` -> ``approved`` -> (``stale`` | ``superseded`` | ``needs_review`` | ``rejected``)

Only ``approved`` pairs are offered to the model. Schema drift moves the affected ones to ``stale``
with a reason, which is visible rather than silent. Pairs are scoped to a data source and an owner,
and retrieval never crosses either boundary.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from app.core import sql_validator
from app.core.retriever import default_collection_name, get_vector_store
from db.database_manager import get_project_db_connection_string, get_session
from db.model import VerifiedQuery

logger = logging.getLogger(__name__)


class VerifiedStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    REJECTED = "rejected"
    STALE = "stale"
    SUPERSEDED = "superseded"
    NEEDS_REVIEW = "needs_review"

    @property
    def usable(self) -> bool:
        """Only an approved pair may be shown to the model as a verified example."""
        return self is VerifiedStatus.APPROVED


def _to_dict(row: VerifiedQuery) -> dict[str, Any]:
    return {
        "id": row.id,
        "db_flag": row.db_flag,
        "question": row.question,
        "sql": row.sql,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "status": row.status or VerifiedStatus.APPROVED.value,
        "snapshot_id": row.snapshot_id,
        "sql_fingerprint": row.sql_fingerprint,
        "tables": row.tables or [],
        "dialect": row.dialect,
        "reviewer": row.reviewer,
        "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
        "usage_count": row.usage_count or 0,
        "staleness_reason": row.staleness_reason,
        "prompt_version": row.prompt_version,
        "model_profile": row.model_profile,
    }


def _embed_pair(db_flag: str, question: str, sql: str) -> str | None:
    """Embed the question so the agent can retrieve this pair as a verified example."""
    try:
        from langchain_core.documents import Document

        store = get_vector_store(default_collection_name(db_flag))
        doc_id = hashlib.sha256(f"{db_flag}:{question}:{sql}".encode()).hexdigest()[:32]
        store.add_documents(
            [
                Document(
                    page_content=question,
                    metadata={
                        "section": "verified_qsql",
                        "db_flag": db_flag,
                        "question": question,
                        "sql": sql,
                    },
                )
            ],
            ids=[doc_id],
        )
        return doc_id
    except Exception as exc:  # embedding is best-effort; the row is the source of truth
        logger.warning("Failed to embed verified pair: %s", exc)
        return None


def save_verified_query(
    db_flag: str,
    question: str,
    sql: str,
    owner_id: int | None,
    *,
    reviewer: str | None = None,
    snapshot_id: str | None = None,
    dialect: str | None = None,
    prompt_version: str | None = None,
    model_profile: str | None = None,
    status: VerifiedStatus | str = VerifiedStatus.APPROVED,
) -> dict[str, Any]:
    """Validate, fingerprint and store a pair. Raises ``ValueError`` if the SQL is not read-only."""
    question = (question or "").strip()
    sql = (sql or "").strip()
    if not question or not sql:
        raise ValueError("Both a question and SQL are required.")

    decision = sql_validator.evaluate_sql(sql, db_flag, dialect=dialect)
    if not decision.valid:
        raise ValueError(f"Refusing to save SQL that is not read-only: {decision.reason}")

    embedding_id = _embed_pair(db_flag, question, sql) if VerifiedStatus(status).usable else None

    session = get_session(get_project_db_connection_string())
    try:
        row = VerifiedQuery(
            db_flag=db_flag,
            question=question,
            sql=sql,
            embedding_id=embedding_id,
            owner_id=owner_id,
            status=str(VerifiedStatus(status).value),
            snapshot_id=snapshot_id,
            sql_fingerprint=decision.fingerprint,
            tables=[str(t) for t in decision.tables],
            dialect=decision.dialect.value,
            reviewer=reviewer,
            reviewed_at=datetime.now(UTC),
            usage_count=0,
            last_validated_at=datetime.now(UTC),
            prompt_version=prompt_version,
            model_profile=model_profile,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return _to_dict(row)
    finally:
        session.close()


def list_verified_queries(
    db_flag: str | None,
    owner_id: int | None,
    *,
    statuses: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Pairs visible to this owner. Never crosses an owner or data-source boundary."""
    session = get_session(get_project_db_connection_string())
    try:
        query = session.query(VerifiedQuery).filter(VerifiedQuery.owner_id == owner_id)
        if db_flag:
            query = query.filter(VerifiedQuery.db_flag == db_flag)
        if statuses:
            query = query.filter(VerifiedQuery.status.in_(statuses))
        rows = query.order_by(VerifiedQuery.created_at.desc()).all()
        return [_to_dict(r) for r in rows]
    finally:
        session.close()


def usable_examples(db_flag: str, owner_id: int | None, limit: int = 5) -> list[dict[str, Any]]:
    """Approved pairs only - the set the model is allowed to see."""
    return list_verified_queries(db_flag, owner_id, statuses=[VerifiedStatus.APPROVED.value])[
        :limit
    ]


def set_status(
    pair_id: int,
    owner_id: int | None,
    status: VerifiedStatus | str,
    *,
    reason: str | None = None,
    reviewer: str | None = None,
) -> dict[str, Any] | None:
    """Move a pair through its lifecycle, recording who and why."""
    session = get_session(get_project_db_connection_string())
    try:
        row = (
            session.query(VerifiedQuery)
            .filter(VerifiedQuery.id == pair_id, VerifiedQuery.owner_id == owner_id)
            .one_or_none()
        )
        if row is None:
            return None
        new_status = VerifiedStatus(status)
        row.status = new_status.value
        row.staleness_reason = reason
        row.reviewer = reviewer or row.reviewer
        row.reviewed_at = datetime.now(UTC)

        # Retrieval has to follow the status, in both directions. A pair that is no longer approved
        # must stop being *reachable* rather than merely be labelled; and one that a reviewer puts
        # back into circulation has to be re-embedded, or it would be approved but never retrieved.
        if not new_status.usable and row.embedding_id:
            _delete_embedding(row.db_flag, row.embedding_id)
            row.embedding_id = None
        elif new_status.usable and not row.embedding_id:
            row.embedding_id = _embed_pair(row.db_flag, row.question, row.sql)
        session.commit()
        session.refresh(row)
        return _to_dict(row)
    finally:
        session.close()


def mark_stale_for_tables(db_flag: str, changed_tables: list[str], reason: str) -> int:
    """Called by drift detection: any approved pair touching a changed table needs review again.

    Conservative on purpose. A pair whose tables were not recorded (saved before this existed) is
    also marked, because we cannot show it is unaffected.
    """
    changed = {t.lower() for t in changed_tables}
    if not changed:
        return 0

    session = get_session(get_project_db_connection_string())
    try:
        rows = (
            session.query(VerifiedQuery)
            .filter(
                VerifiedQuery.db_flag == db_flag,
                VerifiedQuery.status == VerifiedStatus.APPROVED.value,
            )
            .all()
        )
        affected = 0
        for row in rows:
            tables = {str(t).lower() for t in (row.tables or [])}
            bare = {t.split(".")[-1] for t in tables}
            unknown = not tables
            if unknown or (tables | bare) & (changed | {c.split(".")[-1] for c in changed}):
                row.status = VerifiedStatus.STALE.value
                row.staleness_reason = reason
                if row.embedding_id:
                    _delete_embedding(db_flag, row.embedding_id)
                    row.embedding_id = None
                affected += 1
        session.commit()
        if affected:
            logger.info("Marked %d verified pair(s) stale for %s: %s", affected, db_flag, reason)
        return affected
    finally:
        session.close()


def record_use(pair_id: int) -> None:
    """Count a pair being used, so the least-useful ones can be found and retired."""
    session = get_session(get_project_db_connection_string())
    try:
        row = session.query(VerifiedQuery).filter(VerifiedQuery.id == pair_id).one_or_none()
        if row is not None:
            row.usage_count = (row.usage_count or 0) + 1
            session.commit()
    finally:
        session.close()


def delete_verified_query(pair_id: int, owner_id: int | None) -> bool:
    session = get_session(get_project_db_connection_string())
    try:
        row = (
            session.query(VerifiedQuery)
            .filter(VerifiedQuery.id == pair_id, VerifiedQuery.owner_id == owner_id)
            .one_or_none()
        )
        if row is None:
            return False
        if row.embedding_id:
            _delete_embedding(row.db_flag, row.embedding_id)
        session.delete(row)
        session.commit()
        return True
    finally:
        session.close()


def _delete_embedding(db_flag: str, embedding_id: str) -> None:
    try:
        get_vector_store(default_collection_name(db_flag)).delete([embedding_id])
    except Exception as exc:  # pragma: no cover - best effort
        logger.warning("Failed to delete verified embedding: %s", exc)


def export_as_eval_cases(db_flag: str, owner_id: int | None) -> list[dict[str, Any]]:
    """Approved pairs as evaluation cases - the flywheel's other end.

    A reviewer approving a pair is exactly the judgement an evaluation dataset needs, so this turns
    accumulated review effort into regression coverage rather than leaving it in a side table.
    """
    return [
        {
            "id": f"verified-{pair['id']}",
            "dataset": f"verified:{db_flag}",
            "database": db_flag,
            "dialect": pair["dialect"],
            "question": pair["question"],
            "gold_sql": pair["sql"],
            "expected_behavior": "answerable",
            "expected_tables": pair["tables"],
            "notes": (
                f"Approved by {pair['reviewer'] or 'a reviewer'} on {pair['reviewed_at']}; "
                f"snapshot {pair['snapshot_id'] or 'unrecorded'}."
            ),
        }
        for pair in list_verified_queries(
            db_flag, owner_id, statuses=[VerifiedStatus.APPROVED.value]
        )
    ]


__all__ = [
    "VerifiedStatus",
    "delete_verified_query",
    "export_as_eval_cases",
    "list_verified_queries",
    "mark_stale_for_tables",
    "record_use",
    "save_verified_query",
    "set_status",
    "usable_examples",
]
