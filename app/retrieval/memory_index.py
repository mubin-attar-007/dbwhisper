"""An in-process index: BM25 lexical scoring plus cosine vector search.

This exists so that retrieval is testable and so a SQLite-backed deployment is a real option. It is
not a toy: BM25 is the same ranking function Postgres full-text search approximates, so behaviour
here matches the Postgres index closely enough that the fusion logic above it can be developed and
evaluated without a database.

For a schema - hundreds to low thousands of documents - a linear scan is entirely adequate. The
Postgres index takes over when the corpus or the concurrency grows.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from app.embeddings.base import cosine_similarity
from app.retrieval.base import (
    IndexInfo,
    MatchType,
    RetrievalFilter,
    RetrievalHit,
    SearchDocument,
)

_TOKEN = re.compile(r"[a-z0-9]+")
#: BM25 parameters. k1 controls term-frequency saturation, b the length normalisation.
BM25_K1 = 1.2
BM25_B = 0.75

#: Words that carry no signal in a schema corpus - almost every table description contains them.
STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "have",
        "how",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "will",
        "with",
        "show",
        "me",
        "list",
        "get",
        "find",
        "all",
        "each",
        "every",
        "table",
        "column",
        "database",
        "record",
        "records",
        "data",
        "value",
        "values",
    ]
)


def tokenize(text: str, *, keep_stopwords: bool = False) -> list[str]:
    """Lower-case word tokens, with ``snake_case`` and ``camelCase`` split into parts.

    ``order_items`` has to match a question about "order items", and ``OrderItems`` has to match
    both - identifier casing is where naive tokenisation quietly loses recall.
    """
    expanded = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text or "")
    tokens = _TOKEN.findall(expanded.lower())
    if keep_stopwords:
        return tokens
    return [t for t in tokens if t not in STOPWORDS]


@dataclass
class InMemoryRetrievalIndex:
    """A complete :class:`~app.retrieval.base.RetrievalIndex` held in process memory."""

    documents: dict[str, SearchDocument] = field(default_factory=dict)
    _tokens: dict[str, list[str]] = field(default_factory=dict, repr=False)
    _document_frequency: dict[str, int] = field(
        default_factory=lambda: defaultdict(int), repr=False
    )

    # -- writing -------------------------------------------------------------------------------
    def index(self, documents: list[SearchDocument]) -> int:
        for document in documents:
            if document.id in self.documents:
                self._forget(document.id)
            self.documents[document.id] = document
            tokens = tokenize(document.searchable_text())
            self._tokens[document.id] = tokens
            for token in set(tokens):
                self._document_frequency[token] += 1
        return len(documents)

    def _forget(self, document_id: str) -> None:
        for token in set(self._tokens.get(document_id, [])):
            self._document_frequency[token] -= 1
            if self._document_frequency[token] <= 0:
                del self._document_frequency[token]
        self._tokens.pop(document_id, None)
        self.documents.pop(document_id, None)

    def delete_snapshot(self, source_id: str, snapshot_id: str) -> int:
        doomed = [
            d.id
            for d in self.documents.values()
            if d.source_id == source_id and d.snapshot_id == snapshot_id
        ]
        for document_id in doomed:
            self._forget(document_id)
        return len(doomed)

    def get(self, document_id: str) -> SearchDocument | None:
        return self.documents.get(document_id)

    # -- reading -------------------------------------------------------------------------------
    def _scope(self, filters: RetrievalFilter) -> list[SearchDocument]:
        return [d for d in self.documents.values() if filters.matches(d)]

    def _average_length(self, scope: list[SearchDocument]) -> float:
        if not scope:
            return 0.0
        return sum(len(self._tokens.get(d.id, [])) for d in scope) / len(scope)

    def lexical_search(self, query: str, filters: RetrievalFilter, k: int) -> list[RetrievalHit]:
        scope = self._scope(filters)
        if not scope:
            return []
        query_tokens = tokenize(query)
        if not query_tokens:
            return []
        total = len(scope)
        average_length = self._average_length(scope) or 1.0

        scored: list[tuple[float, SearchDocument]] = []
        for document in scope:
            tokens = self._tokens.get(document.id, [])
            if not tokens:
                continue
            counts: dict[str, int] = defaultdict(int)
            for token in tokens:
                counts[token] += 1
            length = len(tokens)
            score = 0.0
            for token in set(query_tokens):
                frequency = counts.get(token, 0)
                if not frequency:
                    continue
                # Scope-local document frequency: the corpus is the filtered set, not everything.
                containing = sum(1 for d in scope if token in set(self._tokens.get(d.id, [])))
                idf = math.log(1 + (total - containing + 0.5) / (containing + 0.5))
                denominator = frequency + BM25_K1 * (1 - BM25_B + BM25_B * length / average_length)
                score += idf * (frequency * (BM25_K1 + 1)) / denominator
            if score > 0:
                scored.append((score, document))

        scored.sort(key=lambda pair: (-pair[0], pair[1].id))
        return [
            RetrievalHit(document=document, score=score, match_type=MatchType.LEXICAL)
            for score, document in scored[:k]
        ]

    def vector_search(
        self, embedding: list[float], filters: RetrievalFilter, k: int
    ) -> list[RetrievalHit]:
        scope = [d for d in self._scope(filters) if d.embedding]
        if filters.embedding_version:
            scope = [d for d in scope if d.embedding_version == filters.embedding_version]
        scored = [
            (cosine_similarity(embedding, d.embedding), d)
            for d in scope
            if d.embedding and len(d.embedding) == len(embedding)
        ]
        scored.sort(key=lambda pair: (-pair[0], pair[1].id))
        return [
            RetrievalHit(document=document, score=score, match_type=MatchType.VECTOR)
            for score, document in scored[:k]
            if score > 0
        ]

    def exact_search(self, terms: list[str], filters: RetrievalFilter) -> list[RetrievalHit]:
        wanted = {t.strip().lower() for t in terms if t and t.strip()}
        if not wanted:
            return []
        hits: list[RetrievalHit] = []
        for document in self._scope(filters):
            names = {
                (document.table or "").lower(),
                (document.column or "").lower(),
                (document.qualified_table or "").lower(),
                document.title.lower(),
                *(s.lower() for s in document.synonyms),
            }
            names.discard("")
            # A normalised form so "order items" matches the table order_items.
            normalised = {n.replace("_", " ") for n in names} | names
            overlap = wanted & normalised
            if overlap:
                hits.append(
                    RetrievalHit(
                        document=document,
                        score=float(len(overlap)),
                        match_type=MatchType.EXACT,
                    )
                )
        hits.sort(key=lambda hit: (-hit.score, hit.document.id))
        return hits

    # -- diagnostics ---------------------------------------------------------------------------
    def info(self) -> IndexInfo:
        return IndexInfo(
            documents=len(self.documents),
            sources=sorted({d.source_id for d in self.documents.values()}),
            snapshots=sorted({d.snapshot_id for d in self.documents.values()}),
            embedding_versions=sorted(
                {d.embedding_version for d in self.documents.values() if d.embedding_version}
            ),
        )

    def stats(self) -> dict[str, Any]:
        info = self.info()
        by_kind: dict[str, int] = defaultdict(int)
        for document in self.documents.values():
            by_kind[document.kind.value] += 1
        return {
            "backend": "memory",
            "documents": info.documents,
            "sources": info.sources,
            "snapshots": info.snapshots,
            "embedding_versions": info.embedding_versions,
            "mixed_embeddings": info.has_mixed_embeddings,
            "by_kind": dict(by_kind),
            "vocabulary": len(self._document_frequency),
        }


__all__ = ["BM25_B", "BM25_K1", "STOPWORDS", "InMemoryRetrievalIndex", "tokenize"]
