"""Turning an enrolled schema into search documents.

Deterministic metadata comes first and AI-written descriptions are clearly labelled, because the
context pack is where a hallucinated "foreign key" would become an instruction the model trusts.
A generated summary is included as *draft* text; a human-approved one replaces it outright.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from app.retrieval.base import DocumentKind, SearchDocument


def _qualified(schema: str | None, table: str) -> str:
    return f"{schema}.{table}" if schema else table


def table_document(
    *,
    source_id: str,
    snapshot_id: str,
    schema: str | None,
    table: str,
    description: str = "",
    description_source: str = "db_comment",
    columns: list[str] | None = None,
    primary_key: list[str] | None = None,
    synonyms: list[str] | None = None,
) -> SearchDocument:
    qualified = _qualified(schema, table)
    body = description.strip()
    if body and description_source == "ai_draft":
        body = f"(AI-drafted, unreviewed) {body}"
    return SearchDocument(
        id=f"{source_id}:{snapshot_id}:table:{qualified}".lower(),
        kind=DocumentKind.TABLE,
        source_id=source_id,
        snapshot_id=snapshot_id,
        title=qualified,
        text=body or f"Table {qualified}.",
        table=table,
        schema_name=schema,
        synonyms=synonyms or [],
        metadata={
            "columns": columns or [],
            "primary_key": primary_key or [],
            "description_source": description_source,
        },
    )


def column_document(
    *,
    source_id: str,
    snapshot_id: str,
    schema: str | None,
    table: str,
    column: str,
    data_type: str = "",
    description: str = "",
    nullable: bool | None = None,
) -> SearchDocument:
    qualified = _qualified(schema, table)
    detail = f"Column {column} of {qualified}"
    if data_type:
        detail += f" ({data_type})"
    if nullable is not None:
        detail += ", nullable" if nullable else ", not null"
    return SearchDocument(
        id=f"{source_id}:{snapshot_id}:column:{qualified}.{column}".lower(),
        kind=DocumentKind.COLUMN,
        source_id=source_id,
        snapshot_id=snapshot_id,
        title=f"{qualified}.{column}",
        text=f"{detail}. {description}".strip(),
        table=table,
        schema_name=schema,
        column=column,
        metadata={"data_type": data_type, "nullable": nullable},
    )


def relationship_document(
    *,
    source_id: str,
    snapshot_id: str,
    from_table: str,
    from_columns: list[str],
    to_table: str,
    to_columns: list[str],
    confirmed: bool = True,
) -> SearchDocument:
    join = f"{from_table}.{', '.join(from_columns)} -> {to_table}.{', '.join(to_columns)}"
    label = "declared foreign key" if confirmed else "inferred relationship (needs review)"
    return SearchDocument(
        id=f"{source_id}:{snapshot_id}:rel:{from_table}:{to_table}:{'-'.join(from_columns)}".lower(),
        kind=DocumentKind.RELATIONSHIP,
        source_id=source_id,
        snapshot_id=snapshot_id,
        title=f"{from_table} -> {to_table}",
        text=f"{join} ({label})",
        table=from_table,
        synonyms=[from_table, to_table],
        metadata={
            "from_table": from_table,
            "to_table": to_table,
            "from_columns": from_columns,
            "to_columns": to_columns,
            "confirmed": confirmed,
        },
    )


def verified_query_document(
    *, source_id: str, snapshot_id: str, question: str, sql: str, pair_id: str | int
) -> SearchDocument:
    return SearchDocument(
        id=f"{source_id}:{snapshot_id}:verified:{pair_id}".lower(),
        kind=DocumentKind.VERIFIED_QUERY,
        source_id=source_id,
        snapshot_id=snapshot_id,
        title=question,
        text=question,
        metadata={"question": question, "sql": sql, "pair_id": pair_id},
    )


def documents_from_schema_index(
    index_path: Path | str, *, source_id: str, snapshot_id: str | None = None
) -> list[SearchDocument]:
    """Build documents from a ``schema_index.yaml`` produced by enrollment.

    This is the bridge from the v1 artefacts to v2 retrieval, so an already-enrolled database can be
    searched without re-running extraction.
    """
    path = Path(index_path)
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    snapshot = snapshot_id or str(data.get("extraction_date") or path.stat().st_mtime_ns)

    documents: list[SearchDocument] = []
    for entry in data.get("tables") or []:
        table = str(entry.get("table") or "").strip()
        if not table:
            continue
        schema = (str(entry.get("schema") or "").strip()) or None
        columns = [str(c) for c in (entry.get("column_names") or [])]
        documents.append(
            table_document(
                source_id=source_id,
                snapshot_id=snapshot,
                schema=schema,
                table=table,
                description=str(entry.get("short_description") or ""),
                columns=columns,
                primary_key=[str(c) for c in (entry.get("primary_key") or [])],
                synonyms=[str(k) for k in (entry.get("keywords") or [])],
            )
        )
        for column in columns:
            documents.append(
                column_document(
                    source_id=source_id,
                    snapshot_id=snapshot,
                    schema=schema,
                    table=table,
                    column=column,
                )
            )
    return documents


__all__ = [
    "column_document",
    "documents_from_schema_index",
    "relationship_document",
    "table_document",
    "verified_query_document",
]
