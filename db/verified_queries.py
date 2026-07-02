"""Verified question -> SQL pairs: the human-approved training flywheel.

Source of truth is the ``verified_queries`` table. A copy of each question is embedded into the
same PGVector collection the schema RAG uses (tagged ``section=verified_qsql``) so the agent can
retrieve similar *approved* examples at generation time. Pairs are stored ONLY on explicit user
approval and only after the SQL passes the read-only validator.
"""

from __future__ import annotations

import hashlib
from typing import Any

from app.core import sql_validator
from app.core.retriever import default_collection_name, get_vector_store
from app.utils.logger import setup_logging
from db.database_manager import get_project_db_connection_string, get_session
from db.model import VerifiedQuery

logger = setup_logging(__name__)


def _to_dict(row: VerifiedQuery) -> dict[str, Any]:
    return {
        "id": row.id,
        "db_flag": row.db_flag,
        "question": row.question,
        "sql": row.sql,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _embed_pair(db_flag: str, question: str, sql: str) -> str | None:
    """Embed the question into the RAG collection so it can be retrieved as a verified example."""
    try:
        from langchain_core.documents import Document

        collection = default_collection_name(db_flag)
        store = get_vector_store(collection)
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
    except Exception as exc:  # pragma: no cover - embedding is best-effort
        logger.warning("Failed to embed verified pair: %s", exc)
        return None


def save_verified_query(
    db_flag: str, question: str, sql: str, owner_id: int | None
) -> dict[str, Any]:
    """Validate (read-only) + store a verified pair. Raises ValueError if the SQL isn't read-only."""
    question = (question or "").strip()
    sql = (sql or "").strip()
    if not question or not sql:
        raise ValueError("Both a question and SQL are required.")
    validation = sql_validator.validate_sql(sql, db_flag=db_flag)
    if not validation.get("valid"):
        raise ValueError(f"Refusing to save non-read-only SQL: {validation.get('reason')}")

    embedding_id = _embed_pair(db_flag, question, sql)

    session = get_session(get_project_db_connection_string())
    try:
        row = VerifiedQuery(
            db_flag=db_flag,
            question=question,
            sql=sql,
            embedding_id=embedding_id,
            owner_id=owner_id,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return _to_dict(row)
    finally:
        session.close()


def list_verified_queries(db_flag: str | None, owner_id: int | None) -> list[dict[str, Any]]:
    session = get_session(get_project_db_connection_string())
    try:
        q = session.query(VerifiedQuery)
        if db_flag:
            q = q.filter(VerifiedQuery.db_flag == db_flag)
        q = q.filter(VerifiedQuery.owner_id == owner_id)
        rows = q.order_by(VerifiedQuery.created_at.desc()).all()
        return [_to_dict(r) for r in rows]
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
            try:
                store = get_vector_store(default_collection_name(row.db_flag))
                store.delete([row.embedding_id])
            except Exception as exc:  # pragma: no cover - best-effort
                logger.warning("Failed to delete verified embedding: %s", exc)
        session.delete(row)
        session.commit()
        return True
    finally:
        session.close()
