"""Hybrid retrieval: understand the question, search three ways, fuse, expand, explain.

The single change that matters most here is **Reciprocal Rank Fusion**. Lexical scores (BM25) and
vector scores (cosine) live on incompatible scales, so combining them by weighted sum requires
tuning constants that stop being right the moment the corpus changes. RRF ignores the scores and
combines *ranks* - ``1 / (k + rank)`` summed across lists - which is scale-free, needs no tuning,
and reliably beats either list alone.

The stages:

1. normalise the question and pull out identifiers, quoted phrases and time expressions;
2. run exact, lexical and vector search inside the tenant/snapshot scope;
3. fuse with RRF, giving exact identifier matches a deliberate boost - if the user typed a table
   name, that table belongs in the context regardless of what the embedding thinks;
4. expand along the join graph, so a table needed only to connect two others is still included;
5. return hits with the evidence of how each was found.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.retrieval.base import (
    DocumentKind,
    MatchType,
    RetrievalFilter,
    RetrievalHit,
    RetrievalIndex,
    SearchDocument,
)

#: The RRF constant. 60 is the value from the original paper and is not sensitive.
RRF_K = 60
#: Exact identifier hits are worth more than a good vector rank; this is how much more.
EXACT_MATCH_BOOST = 2.0

_QUOTED = re.compile(r"['\"]([^'\"]{2,60})['\"]")
_IDENTIFIER = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b", re.IGNORECASE)
_CAMEL = re.compile(r"\b[a-z]+(?:[A-Z][a-z0-9]+)+\b")
_TIME_WORDS = frozenset(
    [
        "today",
        "yesterday",
        "week",
        "weeks",
        "month",
        "months",
        "quarter",
        "quarters",
        "year",
        "years",
        "day",
        "days",
        "daily",
        "weekly",
        "monthly",
        "quarterly",
        "yearly",
        "annual",
        "ytd",
        "mtd",
        "qtd",
        "last",
        "previous",
        "prior",
        "recent",
        "since",
        "between",
        "during",
        "before",
        "after",
        "trend",
        "over",
        "time",
    ]
)
_METRIC_WORDS = frozenset(
    [
        "revenue",
        "sales",
        "total",
        "sum",
        "count",
        "average",
        "avg",
        "median",
        "min",
        "max",
        "growth",
        "churn",
        "rate",
        "ratio",
        "percentage",
        "percent",
        "share",
        "margin",
        "profit",
        "cost",
        "spend",
        "volume",
        "quantity",
        "amount",
        "value",
        "number",
        "top",
        "bottom",
        "rank",
        "ranking",
        "best",
        "worst",
        "highest",
        "lowest",
    ]
)


@dataclass(slots=True)
class QueryAnalysis:
    """What we could work out about the question without asking a model."""

    text: str
    normalized: str
    identifiers: list[str] = field(default_factory=list)
    quoted: list[str] = field(default_factory=list)
    time_terms: list[str] = field(default_factory=list)
    metric_terms: list[str] = field(default_factory=list)

    @property
    def exact_terms(self) -> list[str]:
        """Terms worth an exact-match lookup: things the user appears to have named."""
        seen: list[str] = []
        for term in [*self.quoted, *self.identifiers]:
            lowered = term.lower()
            if lowered not in seen:
                seen.append(lowered)
        return seen

    @property
    def has_time_dimension(self) -> bool:
        return bool(self.time_terms)


def analyze_query(text: str) -> QueryAnalysis:
    """Deterministic query understanding. No model call, so it always runs and never fails."""
    raw = (text or "").strip()
    normalized = re.sub(r"\s+", " ", raw).strip()
    quoted = [m.group(1).strip() for m in _QUOTED.finditer(raw)]
    identifiers = [m.group(0) for m in _IDENTIFIER.finditer(raw)]
    identifiers += [m.group(0) for m in _CAMEL.finditer(raw)]
    words = re.findall(r"[a-z]+", raw.lower())
    return QueryAnalysis(
        text=raw,
        normalized=normalized,
        identifiers=list(dict.fromkeys(identifiers)),
        quoted=quoted,
        time_terms=[w for w in dict.fromkeys(words) if w in _TIME_WORDS],
        metric_terms=[w for w in dict.fromkeys(words) if w in _METRIC_WORDS],
    )


def reciprocal_rank_fusion(
    ranked_lists: dict[str, list[RetrievalHit]],
    *,
    k: int = RRF_K,
    weights: dict[str, float] | None = None,
) -> list[RetrievalHit]:
    """Combine ranked lists by rank rather than by score.

    Returns one hit per document, with ``contributions`` recording its rank in each source list -
    which is what lets the UI say "chosen because it ranked 1st on name match and 3rd on meaning".
    """
    weights = weights or {}
    scores: dict[str, float] = {}
    contributions: dict[str, dict[str, int]] = {}
    documents: dict[str, SearchDocument] = {}
    origin: dict[str, MatchType] = {}

    for source, hits in ranked_lists.items():
        weight = weights.get(source, 1.0)
        for rank, hit in enumerate(hits, start=1):
            document_id = hit.document.id
            scores[document_id] = scores.get(document_id, 0.0) + weight / (k + rank)
            contributions.setdefault(document_id, {})[source] = rank
            documents[document_id] = hit.document
            # Remember the strongest provenance: an exact match outranks the rest.
            if document_id not in origin or hit.match_type is MatchType.EXACT:
                origin[document_id] = hit.match_type

    fused = [
        RetrievalHit(
            document=documents[document_id],
            score=score,
            match_type=(
                MatchType.EXACT
                if origin.get(document_id) is MatchType.EXACT
                else (
                    MatchType.FUSED if len(contributions[document_id]) > 1 else origin[document_id]
                )
            ),
            contributions=contributions[document_id],
        )
        for document_id, score in scores.items()
    ]
    fused.sort(key=lambda hit: (-hit.score, hit.document.id))
    return fused


@dataclass(slots=True)
class RetrievalRequest:
    question: str
    source_id: str
    snapshot_id: str | None = None
    k: int = 8
    per_source_k: int = 20
    embedding: list[float] | None = None
    embedding_version: str | None = None
    expand_neighbours: bool = True
    kinds: tuple[DocumentKind, ...] | None = None


@dataclass(slots=True)
class RetrievalEvidence:
    """Recorded on the run so the answer can show how its context was assembled."""

    analysis: QueryAnalysis
    lexical_ids: list[str] = field(default_factory=list)
    vector_ids: list[str] = field(default_factory=list)
    exact_ids: list[str] = field(default_factory=list)
    neighbour_ids: list[str] = field(default_factory=list)
    selected_ids: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)
    latency_ms: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "identifiers": self.analysis.identifiers,
            "time_terms": self.analysis.time_terms,
            "metric_terms": self.analysis.metric_terms,
            "lexical": self.lexical_ids,
            "vector": self.vector_ids,
            "exact": self.exact_ids,
            "neighbours": self.neighbour_ids,
            "selected": self.selected_ids,
            "tables": self.tables,
            "latency_ms": round(self.latency_ms, 2),
        }


@dataclass(slots=True)
class RetrievalResult:
    hits: list[RetrievalHit]
    evidence: RetrievalEvidence

    @property
    def tables(self) -> list[str]:
        seen: list[str] = []
        for hit in self.hits:
            qualified = hit.document.qualified_table
            if qualified and qualified not in seen:
                seen.append(qualified)
        return seen


def _neighbour_tables(
    index: RetrievalIndex, filters: RetrievalFilter, tables: set[str]
) -> set[str]:
    """Tables reachable in one hop through indexed relationship documents."""
    relationship_filter = RetrievalFilter(
        source_id=filters.source_id,
        snapshot_id=filters.snapshot_id,
        kinds=(DocumentKind.RELATIONSHIP,),
    )
    neighbours: set[str] = set()
    for hit in index.exact_search(sorted(tables), relationship_filter):
        metadata = hit.document.metadata
        for key in ("from_table", "to_table", "referenced_table"):
            value = metadata.get(key)
            if value and value.lower() not in tables:
                neighbours.add(str(value))
    return neighbours


def _with_parent_tables(
    index: RetrievalIndex, filters: RetrievalFilter, hits: list[RetrievalHit]
) -> list[RetrievalHit]:
    """Add the TABLE document for every table a hit belongs to, if it is not already present."""
    present_tables = {
        (h.document.qualified_table or "").lower()
        for h in hits
        if h.document.kind is DocumentKind.TABLE
    }
    wanted = (
        {(h.document.qualified_table or "").lower() for h in hits if h.document.qualified_table}
        - present_tables
        - {""}
    )
    if not wanted:
        return hits

    table_filter = RetrievalFilter(
        source_id=filters.source_id,
        snapshot_id=filters.snapshot_id,
        kinds=(DocumentKind.TABLE,),
        tables=tuple(wanted),
    )
    existing = {h.document.id for h in hits}
    # Rank parent tables just below the hit that pulled them in, so ordering stays meaningful.
    lowest = min((h.score for h in hits), default=0.0)
    added = [
        RetrievalHit(hit.document, score=lowest, match_type=MatchType.FUSED)
        for hit in index.exact_search(sorted(wanted), table_filter)
        if hit.document.id not in existing
    ]
    return [*hits, *added]


def retrieve(index: RetrievalIndex, request: RetrievalRequest) -> RetrievalResult:
    """Run the full hybrid pipeline and return ranked hits plus the evidence behind them."""
    import time

    started = time.perf_counter()
    analysis = analyze_query(request.question)
    filters = RetrievalFilter(
        source_id=request.source_id,
        snapshot_id=request.snapshot_id,
        kinds=request.kinds,
        embedding_version=request.embedding_version,
    )

    lexical = index.lexical_search(analysis.normalized, filters, request.per_source_k)
    exact = index.exact_search(analysis.exact_terms, filters) if analysis.exact_terms else []
    vector: list[RetrievalHit] = []
    if request.embedding:
        vector = index.vector_search(request.embedding, filters, request.per_source_k)

    fused = reciprocal_rank_fusion(
        {"lexical": lexical, "vector": vector, "exact": exact},
        weights={"exact": EXACT_MATCH_BOOST},
    )
    selected = fused[: request.k]
    # Level two: a column can be the best match while its table never ranks. Pull in the table
    # document for everything selected, or the context pack would describe a column with no table.
    selected = _with_parent_tables(index, filters, selected)

    neighbour_hits: list[RetrievalHit] = []
    if request.expand_neighbours and selected:
        chosen_tables = {(h.document.table or "").lower() for h in selected if h.document.table} - {
            ""
        }
        neighbours = _neighbour_tables(index, filters, chosen_tables)
        if neighbours:
            table_filter = RetrievalFilter(
                source_id=request.source_id,
                snapshot_id=request.snapshot_id,
                kinds=(DocumentKind.TABLE,),
                tables=tuple(neighbours),
            )
            already = {h.document.id for h in selected}
            neighbour_hits = [
                RetrievalHit(hit.document, score=0.0, match_type=MatchType.NEIGHBOUR)
                for hit in index.exact_search(sorted(neighbours), table_filter)
                if hit.document.id not in already
            ]

    hits = [*selected, *neighbour_hits]
    evidence = RetrievalEvidence(
        analysis=analysis,
        lexical_ids=[h.document.id for h in lexical[: request.k]],
        vector_ids=[h.document.id for h in vector[: request.k]],
        exact_ids=[h.document.id for h in exact],
        neighbour_ids=[h.document.id for h in neighbour_hits],
        selected_ids=[h.document.id for h in hits],
        latency_ms=(time.perf_counter() - started) * 1000,
    )
    result = RetrievalResult(hits=hits, evidence=evidence)
    evidence.tables = result.tables
    return result


__all__ = [
    "EXACT_MATCH_BOOST",
    "RRF_K",
    "QueryAnalysis",
    "RetrievalEvidence",
    "RetrievalRequest",
    "RetrievalResult",
    "analyze_query",
    "reciprocal_rank_fusion",
    "retrieve",
]
