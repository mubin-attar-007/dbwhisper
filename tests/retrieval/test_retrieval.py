"""Hybrid retrieval: tokenisation, BM25, fusion, scoping, expansion and the context pack."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.embeddings import FakeEmbeddingProvider
from app.retrieval import (
    DocumentKind,
    InMemoryRetrievalIndex,
    MatchType,
    RetrievalFilter,
    RetrievalHit,
    RetrievalRequest,
    SearchDocument,
    analyze_query,
    build_context_pack,
    column_document,
    documents_from_schema_index,
    reciprocal_rank_fusion,
    relationship_document,
    retrieve,
    table_document,
    tokenize,
    verified_query_document,
)

SOURCE = "retail"
SNAPSHOT = "snap-1"

SCHEMA: dict[str, list[str]] = {
    "customers": ["id", "name", "email", "city", "signup_date"],
    "orders": ["id", "customer_id", "order_date", "status"],
    "order_items": ["id", "order_id", "product_id", "quantity", "unit_price"],
    "products": ["id", "name", "category", "price"],
    "support_tickets": ["id", "customer_id", "opened_at", "severity"],
}

DESCRIPTIONS = {
    "customers": "People who buy things. One row per customer account.",
    "orders": "A purchase placed by a customer on a date, with a fulfilment status.",
    "order_items": "The individual line items of an order: which product, how many, at what price.",
    "products": "The catalogue of things for sale, grouped into categories with a list price.",
    "support_tickets": "Customer support requests and their severity.",
}

RELATIONSHIPS = [
    ("orders", ["customer_id"], "customers", ["id"]),
    ("order_items", ["order_id"], "orders", ["id"]),
    ("order_items", ["product_id"], "products", ["id"]),
    ("support_tickets", ["customer_id"], "customers", ["id"]),
]


def build_documents(source_id: str = SOURCE, snapshot_id: str = SNAPSHOT) -> list[SearchDocument]:
    documents: list[SearchDocument] = []
    for table, columns in SCHEMA.items():
        documents.append(
            table_document(
                source_id=source_id,
                snapshot_id=snapshot_id,
                schema="public",
                table=table,
                description=DESCRIPTIONS[table],
                columns=columns,
                primary_key=["id"],
            )
        )
        documents += [
            column_document(
                source_id=source_id,
                snapshot_id=snapshot_id,
                schema="public",
                table=table,
                column=column,
                data_type="text",
            )
            for column in columns
        ]
    documents += [
        relationship_document(
            source_id=source_id,
            snapshot_id=snapshot_id,
            from_table=from_table,
            from_columns=from_columns,
            to_table=to_table,
            to_columns=to_columns,
        )
        for from_table, from_columns, to_table, to_columns in RELATIONSHIPS
    ]
    return documents


@pytest.fixture
def index() -> InMemoryRetrievalIndex:
    documents = build_documents()
    provider = FakeEmbeddingProvider(dimension=128)
    result = provider.embed_documents([d.searchable_text() for d in documents])
    for document, vector in zip(documents, result.vectors, strict=True):
        document.embedding = vector
        document.embedding_version = result.version.key
    store = InMemoryRetrievalIndex()
    store.index(documents)
    return store


@pytest.fixture
def embedder() -> FakeEmbeddingProvider:
    return FakeEmbeddingProvider(dimension=128)


def _search(index, embedder, question: str, **kwargs) -> list[str]:
    request = RetrievalRequest(
        question=question,
        source_id=SOURCE,
        snapshot_id=SNAPSHOT,
        embedding=embedder.embed_query(question),
        **kwargs,
    )
    return retrieve(index, request).tables


class TestTokenize:
    def test_splits_snake_case(self):
        assert tokenize("order_items") == ["order", "items"]

    def test_splits_camel_case(self):
        assert tokenize("OrderItems totalRevenue") == ["order", "items", "total", "revenue"]

    def test_removes_stopwords(self):
        assert tokenize("show me all the data in the table") == []

    def test_keeps_stopwords_when_asked(self):
        assert "the" in tokenize("the orders", keep_stopwords=True)


class TestQueryAnalysis:
    def test_finds_snake_case_identifiers(self):
        assert "order_items" in analyze_query("how many order_items per order").identifiers

    def test_finds_quoted_phrases(self):
        assert analyze_query('customers in "New York"').quoted == ["New York"]

    def test_finds_time_and_metric_words(self):
        analysis = analyze_query("monthly revenue trend for the last quarter")
        assert analysis.has_time_dimension
        assert "revenue" in analysis.metric_terms

    def test_no_false_time_dimension(self):
        assert not analyze_query("list every product category").has_time_dimension

    def test_exact_terms_are_deduplicated_and_lowercased(self):
        analysis = analyze_query('order_items and ORDER_ITEMS and "order_items"')
        assert analysis.exact_terms.count("order_items") == 1


class TestLexicalSearch:
    def test_finds_the_table_a_description_describes(self, index):
        filters = RetrievalFilter(source_id=SOURCE, snapshot_id=SNAPSHOT)
        hits = index.lexical_search("catalogue of things for sale", filters, k=5)
        assert hits and hits[0].document.table == "products"

    def test_a_word_in_every_document_ranks_nothing(self, index):
        filters = RetrievalFilter(source_id=SOURCE, snapshot_id=SNAPSHOT)
        assert index.lexical_search("the table data", filters, k=5) == []

    def test_kind_filter(self, index):
        filters = RetrievalFilter(
            source_id=SOURCE, snapshot_id=SNAPSHOT, kinds=(DocumentKind.TABLE,)
        )
        hits = index.lexical_search("customer support severity", filters, k=5)
        assert all(h.document.kind is DocumentKind.TABLE for h in hits)


class TestScoping:
    def test_another_source_is_invisible(self, index, embedder):
        other = build_documents(source_id="other-tenant", snapshot_id=SNAPSHOT)
        index.index(other)
        tables = _search(index, embedder, "customers by city")
        assert tables, "the tenant's own tables must still be found"
        filters = RetrievalFilter(source_id=SOURCE, snapshot_id=SNAPSHOT)
        assert all(
            index.get(hit.document.id).source_id == SOURCE
            for hit in index.lexical_search("customers", filters, k=10)
        )

    def test_another_snapshot_is_invisible(self, index):
        index.index(build_documents(snapshot_id="snap-2"))
        filters = RetrievalFilter(source_id=SOURCE, snapshot_id=SNAPSHOT)
        hits = index.lexical_search("catalogue of things for sale", filters, k=10)
        assert hits and all(h.document.snapshot_id == SNAPSHOT for h in hits)

    def test_delete_snapshot_removes_only_that_snapshot(self, index):
        index.index(build_documents(snapshot_id="snap-2"))
        before = index.stats()["documents"]
        removed = index.delete_snapshot(SOURCE, "snap-2")
        assert removed > 0
        assert index.stats()["documents"] == before - removed
        assert index.stats()["snapshots"] == [SNAPSHOT]

    def test_embedding_version_filter_excludes_foreign_vectors(self, index):
        filters = RetrievalFilter(
            source_id=SOURCE, snapshot_id=SNAPSHOT, embedding_version="someone-elses-model:1"
        )
        assert index.vector_search([0.1] * 128, filters, k=5) == []


class TestFusion:
    def _hit(self, doc_id: str, match: MatchType = MatchType.LEXICAL) -> RetrievalHit:
        document = SearchDocument(
            id=doc_id,
            kind=DocumentKind.TABLE,
            source_id=SOURCE,
            snapshot_id=SNAPSHOT,
            title=doc_id,
            text="",
            table=doc_id,
        )
        return RetrievalHit(document=document, score=1.0, match_type=match)

    def test_a_document_in_both_lists_outranks_one_in_either(self):
        fused = reciprocal_rank_fusion(
            {
                "lexical": [self._hit("a"), self._hit("b")],
                "vector": [self._hit("c", MatchType.VECTOR), self._hit("a", MatchType.VECTOR)],
            }
        )
        assert fused[0].document.id == "a"
        assert fused[0].match_type is MatchType.FUSED
        assert fused[0].contributions == {"lexical": 1, "vector": 2}

    def test_exact_matches_are_boosted_and_keep_their_provenance(self):
        fused = reciprocal_rank_fusion(
            {
                "lexical": [self._hit("b"), self._hit("c"), self._hit("d")],
                "exact": [self._hit("z", MatchType.EXACT)],
            },
            weights={"exact": 2.0},
        )
        assert fused[0].document.id == "z"
        assert fused[0].match_type is MatchType.EXACT

    def test_empty_lists_are_harmless(self):
        assert reciprocal_rank_fusion({"lexical": [], "vector": []}) == []


class TestEndToEndRetrieval:
    def test_an_exact_table_name_always_wins(self, index, embedder):
        assert "public.order_items" in _search(index, embedder, "order_items quantity", k=4)

    def test_a_revenue_question_finds_the_tables_it_needs(self, index, embedder):
        tables = _search(index, embedder, "revenue by product category", k=6)
        assert "public.products" in tables
        assert "public.order_items" in tables

    def test_neighbour_expansion_pulls_in_the_join_table(self, index, embedder):
        tables = _search(index, embedder, "which customers bought the most", k=4)
        assert "public.customers" in tables
        assert any(t in tables for t in ("public.orders", "public.order_items"))

    def test_expansion_can_be_disabled(self, index, embedder):
        with_expansion = _search(index, embedder, "customers by city", k=3)
        without = _search(index, embedder, "customers by city", k=3, expand_neighbours=False)
        assert len(without) <= len(with_expansion)

    def test_evidence_explains_the_selection(self, index, embedder):
        question = "order_items quantity by product"
        result = retrieve(
            index,
            RetrievalRequest(
                question=question,
                source_id=SOURCE,
                snapshot_id=SNAPSHOT,
                embedding=embedder.embed_query(question),
                k=5,
            ),
        )
        evidence = result.evidence.as_dict()
        assert "order_items" in evidence["identifiers"]
        assert evidence["exact"], "an identifier the user typed should produce exact hits"
        assert evidence["selected"] and evidence["tables"]
        assert evidence["latency_ms"] >= 0

    def test_works_without_an_embedding(self, index):
        result = retrieve(
            index,
            RetrievalRequest(question="catalogue of things for sale", source_id=SOURCE, k=3),
        )
        assert "public.products" in result.tables, "lexical search alone must still work"

    def test_unknown_source_returns_nothing(self, index, embedder):
        result = retrieve(
            index,
            RetrievalRequest(
                question="customers",
                source_id="does-not-exist",
                embedding=embedder.embed_query("customers"),
            ),
        )
        assert result.hits == []


class TestContextPack:
    def _hits(self, index, embedder, question: str, k: int = 6):
        return retrieve(
            index,
            RetrievalRequest(
                question=question,
                source_id=SOURCE,
                snapshot_id=SNAPSHOT,
                embedding=embedder.embed_query(question),
                k=k,
            ),
        ).hits

    def test_renders_tables_with_columns_and_keys(self, index, embedder):
        pack = build_context_pack(self._hits(index, embedder, "revenue by product category"))
        rendered = pack.render()
        assert "TABLE public.products" in rendered
        assert "primary key: id" in rendered
        assert "category" in rendered

    def test_a_column_hit_promotes_its_table(self, index, embedder):
        pack = build_context_pack(self._hits(index, embedder, "unit_price"))
        assert "public.order_items" in pack.table_names

    def test_budget_is_respected_and_drops_are_reported(self, index, embedder):
        hits = self._hits(index, embedder, "customers orders products order_items", k=10)
        pack = build_context_pack(hits, budget_tokens=40)
        assert pack.token_estimate <= 40 or len(pack.tables) == 1
        assert pack.dropped
        assert "omitted to fit the context budget" in pack.render()

    def test_verified_examples_are_labelled_as_approved(self, index, embedder):
        index.index(
            [
                verified_query_document(
                    source_id=SOURCE,
                    snapshot_id=SNAPSHOT,
                    question="revenue by category",
                    sql="SELECT p.category, SUM(oi.quantity * oi.unit_price) FROM order_items oi "
                    "JOIN products p ON p.id = oi.product_id GROUP BY p.category",
                    pair_id=1,
                )
            ]
        )
        pack = build_context_pack(self._hits(index, embedder, "revenue by category", k=8))
        assert pack.verified_examples
        assert "human-approved" in pack.render()

    def test_the_pack_names_which_pairs_it_actually_showed(self, index, embedder):
        """Usage is credited to pairs the model saw, so the count means something."""
        index.index(
            [
                verified_query_document(
                    source_id=SOURCE,
                    snapshot_id=SNAPSHOT,
                    question="revenue by category",
                    sql="SELECT p.category FROM products p",
                    pair_id=42,
                )
            ]
        )
        pack = build_context_pack(self._hits(index, embedder, "revenue by category", k=8))
        assert pack.example_pair_ids == ["42"]
        assert pack.as_dict()["verified_example_ids"] == ["42"]

    def test_an_example_dropped_for_budget_is_not_credited(self, index, embedder):
        index.index(
            [
                verified_query_document(
                    source_id=SOURCE,
                    snapshot_id=SNAPSHOT,
                    question="revenue by category over the last twelve months by region",
                    sql="SELECT p.category, r.name FROM products p JOIN regions r ON r.id = p.rid",
                    pair_id=42,
                )
            ]
        )
        hits = self._hits(index, embedder, "revenue by category", k=8)
        pack = build_context_pack(hits, budget_tokens=30)
        assert pack.example_pair_ids == [], "a pair the model never saw must not be counted"

    def test_ai_drafted_descriptions_are_labelled(self):
        document = table_document(
            source_id=SOURCE,
            snapshot_id=SNAPSHOT,
            schema="public",
            table="orders",
            description="Probably about orders.",
            description_source="ai_draft",
            columns=["id"],
        )
        pack = build_context_pack(
            [RetrievalHit(document=document, score=1.0, match_type=MatchType.LEXICAL)]
        )
        assert "AI-drafted, unreviewed" in pack.render()

    def test_empty_hits_produce_an_empty_pack(self):
        pack = build_context_pack([])
        assert pack.render() == "" and pack.table_names == []

    def test_as_dict_is_serialisable(self, index, embedder):
        import json

        pack = build_context_pack(self._hits(index, embedder, "customers"))
        json.dumps(pack.as_dict())


class TestSchemaIndexBridge:
    def test_builds_documents_from_the_committed_demo_schema(self):
        path = Path("database_schemas/demo/schema/schema_index.yaml")
        documents = documents_from_schema_index(path, source_id="demo", snapshot_id="s1")
        tables = {d.table for d in documents if d.kind is DocumentKind.TABLE}
        assert tables == {"customers", "orders", "order_items", "products"}
        columns = [d for d in documents if d.kind is DocumentKind.COLUMN]
        assert len(columns) == 18
        assert all(d.source_id == "demo" and d.snapshot_id == "s1" for d in documents)

    def test_ids_are_stable_across_calls(self):
        path = Path("database_schemas/demo/schema/schema_index.yaml")
        first = documents_from_schema_index(path, source_id="demo", snapshot_id="s1")
        second = documents_from_schema_index(path, source_id="demo", snapshot_id="s1")
        assert [d.id for d in first] == [d.id for d in second]

    def test_reindexing_replaces_rather_than_duplicates(self, index):
        before = index.stats()["documents"]
        index.index(build_documents())
        assert index.stats()["documents"] == before
