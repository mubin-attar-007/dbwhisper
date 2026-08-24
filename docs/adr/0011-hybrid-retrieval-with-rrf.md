# ADR 0011 — Hybrid retrieval fused by Reciprocal Rank Fusion, over an in-process index

**Status:** Accepted — 2026-08-21, Phase 5. Partially implemented: the PostgreSQL index in
`TARGET_ARCHITECTURE.md` §4 does not exist yet.

## Context

The model is only as good as the tables it is shown. v1 retrieved schema context with vector
similarity alone, through PGVector (`app/core/retriever.py`), which fails in a characteristic way:
a user who types an exact table name gets whatever the embedding thought was nearby. Lexical search
fixes that case and breaks the paraphrase case. Combining them is the obvious answer and the hard
part, because BM25 scores and cosine scores live on incompatible scales.

## Decision

**Fuse ranks, not scores.** `app/retrieval/hybrid.py` runs exact-identifier, lexical and vector
search, then combines them with Reciprocal Rank Fusion — `1 / (k + rank)` summed across lists, with
`RRF_K = 60`. RRF needs no score normalisation and no tuned weights, so the fusion does not stop
being correct when the corpus changes. Exact identifier matches get a deliberate boost: if the user
typed a table name, that table is in the context regardless of what the embedding thinks. Results
are then expanded along the join graph, so a table needed only to *connect* two others is included,
and every hit carries the evidence of how it was found.

**An in-process index is a first-class deployment, not a test double.**
`app/retrieval/memory_index.py` implements BM25 plus cosine over the same `RetrievalIndex` protocol
as any database-backed index. For a schema — hundreds to low thousands of documents — a linear scan
is entirely adequate, and BM25 is the ranking function PostgreSQL full-text search approximates, so
fusion logic developed against it transfers.

**Context is budgeted, not truncated at the end.** `app/retrieval/context_pack.py` assembles table
cards, join paths and verified examples under a token budget, labelling AI-drafted descriptions as
such so the model can weigh them differently from a human-written one.

## Consequences

**What it buys.** Retrieval runs for real in CI with no PostgreSQL and no embedding download
(`tests/retrieval`, 38 tests; ADR 0004 supplies the fake embedder). There are no fusion constants to
re-tune per deployment, and a table the user named by hand carries an `EXACT_MATCH_BOOST` through
fusion rather than competing on embedding distance alone.

**What it costs.**

* **RRF discards magnitude.** A document that wins its list by a mile ranks the same as one that
  wins by a whisker. That is what makes it robust, and it means a strongly-matching document cannot
  express how strongly it matched.
* **The in-memory index is per-process.** It is rebuilt on demand and cached on the schema file's
  mtime (`app/api/v2/deps.py::build_index`), so an N-worker deployment holds N copies and pays N
  rebuilds. Acceptable for a schema-sized corpus; not a general-purpose search tier.
* **`snapshot_id` is currently a file mtime**, not a catalog row: `deps.py` stamps
  `f"yaml:{path.stat().st_mtime_ns}"` because the Phase 3 catalog tables have not landed. Every
  consumer that treats a snapshot as an immutable, addressable object (ADR 0014) is working against
  a placeholder.
* **`app/retrieval/postgres_index.py` does not exist.** The v2 retrieval path has no PostgreSQL
  FTS/pgvector index today. The v1 path still uses PGVector through `langchain-postgres`, so the
  project currently carries **two** retrieval implementations (ADR 0015). This is the largest gap
  between `TARGET_ARCHITECTURE.md` §10 and the code, and it is not disguised anywhere.
* **Reranking is not implemented.** §10 lists an optional local reranker "off until `eval-retrieval`
  shows a gain"; there is no `rerank` module. Nothing is lost, but nothing is measured either.
