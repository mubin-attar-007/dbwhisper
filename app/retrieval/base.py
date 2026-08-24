"""What retrieval indexes store and return.

The v1 retriever asked pgvector for the four nearest table summaries and passed them to the model.
That fails in the two situations that matter most: an exact identifier the user typed verbatim
(vector search has no notion of "this string appears in the schema"), and a question whose relevant
table is fifth. So the v2 index stores several *kinds* of document, supports both lexical and vector
lookup, and returns hits with enough provenance to explain the answer.

Every document belongs to exactly one ``(source_id, snapshot_id)`` pair. Filtering on that in the
index - not after it - is what keeps one tenant's schema out of another's prompt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from app.embeddings.base import EmbeddingVersion


class DocumentKind(StrEnum):
    TABLE = "table"
    COLUMN = "column"
    RELATIONSHIP = "relationship"
    GLOSSARY = "glossary"
    METRIC = "metric"
    VERIFIED_QUERY = "verified_query"


class MatchType(StrEnum):
    """How a hit was found. Shown in the UI so a user can see *why* a table was chosen."""

    EXACT = "exact"
    LEXICAL = "lexical"
    VECTOR = "vector"
    NEIGHBOUR = "neighbour"
    FUSED = "fused"


@dataclass(slots=True)
class SearchDocument:
    """One indexable unit of schema or business knowledge."""

    id: str
    kind: DocumentKind
    source_id: str
    snapshot_id: str
    title: str
    text: str
    table: str | None = None
    schema_name: str | None = None
    column: str | None = None
    synonyms: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    embedding: list[float] | None = None
    embedding_version: str | None = None

    @property
    def qualified_table(self) -> str | None:
        if not self.table:
            return None
        return f"{self.schema_name}.{self.table}" if self.schema_name else self.table

    def searchable_text(self) -> str:
        parts = [self.title, self.text, *self.synonyms]
        if self.qualified_table:
            parts.append(self.qualified_table)
        if self.column:
            parts.append(self.column)
        return "\n".join(p for p in parts if p)


@dataclass(slots=True)
class RetrievalHit:
    document: SearchDocument
    score: float
    match_type: MatchType
    #: Rank in each source list that contributed, for explaining a fused score.
    contributions: dict[str, int] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return self.document.id


@dataclass(slots=True)
class RetrievalFilter:
    """Scope filters. Applied inside the index query, never as a post-filter."""

    source_id: str
    snapshot_id: str | None = None
    kinds: tuple[DocumentKind, ...] | None = None
    tables: tuple[str, ...] | None = None
    embedding_version: str | None = None

    def matches(self, document: SearchDocument) -> bool:
        if document.source_id != self.source_id:
            return False
        if self.snapshot_id is not None and document.snapshot_id != self.snapshot_id:
            return False
        if self.kinds is not None and document.kind not in self.kinds:
            return False
        if self.tables is not None:
            qualified = (document.qualified_table or "").lower()
            bare = (document.table or "").lower()
            wanted = {t.lower() for t in self.tables}
            if qualified not in wanted and bare not in wanted:
                return False
        return True


@runtime_checkable
class RetrievalIndex(Protocol):
    """Storage for search documents, supporting the three lookup styles fusion needs."""

    def index(self, documents: list[SearchDocument]) -> int:
        """Insert or replace documents by id. Returns how many were written."""
        ...

    def delete_snapshot(self, source_id: str, snapshot_id: str) -> int: ...

    def lexical_search(self, query: str, filters: RetrievalFilter, k: int) -> list[RetrievalHit]:
        """Keyword search. Finds documents that literally contain the user's words."""
        ...

    def vector_search(
        self, embedding: list[float], filters: RetrievalFilter, k: int
    ) -> list[RetrievalHit]:
        """Nearest neighbours by embedding. Finds documents that *mean* the same thing."""
        ...

    def exact_search(self, terms: list[str], filters: RetrievalFilter) -> list[RetrievalHit]:
        """Identifier and synonym matches. Finds the table the user named outright."""
        ...

    def get(self, document_id: str) -> SearchDocument | None: ...

    def stats(self) -> dict[str, Any]: ...


@dataclass(slots=True)
class IndexInfo:
    """What an index currently holds, used to detect a stale or mixed embedding version."""

    documents: int
    sources: list[str]
    snapshots: list[str]
    embedding_versions: list[str]

    @property
    def has_mixed_embeddings(self) -> bool:
        return len([v for v in self.embedding_versions if v]) > 1


def embedding_version_key(version: EmbeddingVersion | str | None) -> str | None:
    if version is None:
        return None
    return version if isinstance(version, str) else version.key


__all__ = [
    "DocumentKind",
    "IndexInfo",
    "MatchType",
    "RetrievalFilter",
    "RetrievalHit",
    "RetrievalIndex",
    "SearchDocument",
    "embedding_version_key",
]
