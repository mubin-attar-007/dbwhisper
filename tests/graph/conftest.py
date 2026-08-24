"""A complete, offline DBWhisper: fake model, fake embeddings, real SQLite, real policy engine.

Everything except the model is genuine here - the retrieval index, the SQLGlot policy engine, the
read-only execution service and a database that actually runs the SQL. That is what makes these
tests worth having: they exercise the pipeline a user hits, and the only thing standing in for
production is the part that would otherwise need a GPU or an API key.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver

from app.embeddings.fake import FakeEmbeddingProvider
from app.graph.deps import DataSourceTarget, GraphDeps
from app.graph.query_graph import compile_query_graph
from app.llm.providers.fake import FakeProvider
from app.llm.registry import FAKE_PROFILE, EnabledProfiles
from app.llm.router import ModelRouter
from app.platform.modes import EgressPolicy, NetworkPolicyLevel
from app.retrieval.documents import (
    column_document,
    relationship_document,
    table_document,
    verified_query_document,
)
from app.retrieval.memory_index import InMemoryRetrievalIndex
from app.sqlpolicy.types import Dialect, PolicyLevel

SOURCE = "demo"

DDL = """
CREATE TABLE customers (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT, city TEXT, signup_date TEXT
);
CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT, category TEXT, price REAL);
CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER, order_date TEXT, status TEXT);
CREATE TABLE order_items (
    id INTEGER PRIMARY KEY, order_id INTEGER, product_id INTEGER, quantity INTEGER, unit_price REAL
);
INSERT INTO customers VALUES
    (1, 'Ava Sharma', 'ava@example.com', 'Mumbai', '2025-01-12'),
    (2, 'Liam Patel', 'liam@example.com', 'Delhi', '2025-02-03'),
    (3, 'Noah Khan', 'noah@example.com', 'Mumbai', '2025-02-20'),
    (4, 'Mia Reddy', 'mia@example.com', 'Pune', '2025-03-11');
INSERT INTO products VALUES
    (1, 'Wireless Mouse', 'Electronics', 19.99),
    (2, 'Keyboard', 'Electronics', 79.50),
    (3, 'Notebook', 'Stationery', 4.50);
INSERT INTO orders VALUES
    (1, 1, '2025-05-01', 'completed'),
    (2, 1, '2025-06-15', 'completed'),
    (3, 2, '2025-05-20', 'shipped'),
    (4, 3, '2025-06-02', 'cancelled');
INSERT INTO order_items VALUES
    (1, 1, 1, 2, 19.99), (2, 1, 3, 5, 4.50), (3, 2, 2, 1, 79.50),
    (4, 3, 1, 1, 19.99), (5, 4, 3, 3, 4.50);
"""

SCHEMA = {
    "customers": ["id", "name", "email", "city", "signup_date"],
    "products": ["id", "name", "category", "price"],
    "orders": ["id", "customer_id", "order_date", "status"],
    "order_items": ["id", "order_id", "product_id", "quantity", "unit_price"],
}
DESCRIPTIONS = {
    "customers": "People who buy things; one row per customer, with the city they are in.",
    "products": "Catalogue of items for sale with a category and list price.",
    "orders": "Purchases placed by a customer on a date with a fulfilment status.",
    "order_items": "Line items of an order: product, quantity and the unit price charged.",
}
RELATIONSHIPS = [
    ("orders", ["customer_id"], "customers", ["id"]),
    ("order_items", ["order_id"], "orders", ["id"]),
    ("order_items", ["product_id"], "products", ["id"]),
]


@pytest.fixture
def target(tmp_path) -> DataSourceTarget:
    path = tmp_path / "demo.db"
    connection = sqlite3.connect(path)
    try:
        connection.executescript(DDL)
        connection.commit()
    finally:
        connection.close()
    return DataSourceTarget(
        source_id=SOURCE,
        connection_string=f"sqlite:///{path.as_posix()}",
        dialect=Dialect.SQLITE,
        max_rows=100,
        timeout_seconds=10,
        snapshot_id="snap-1",
        description="Bundled retail demo",
    )


@pytest.fixture
def index() -> InMemoryRetrievalIndex:
    documents = []
    for table, columns in SCHEMA.items():
        documents.append(
            table_document(
                source_id=SOURCE,
                snapshot_id="snap-1",
                schema="main",
                table=table,
                description=DESCRIPTIONS[table],
                columns=columns,
                primary_key=["id"],
            )
        )
        documents += [
            column_document(
                source_id=SOURCE,
                snapshot_id="snap-1",
                schema="main",
                table=table,
                column=column,
            )
            for column in columns
        ]
    documents += [
        relationship_document(
            source_id=SOURCE,
            snapshot_id="snap-1",
            from_table=a,
            from_columns=ac,
            to_table=b,
            to_columns=bc,
        )
        for a, ac, b, bc in RELATIONSHIPS
    ]
    documents.append(
        verified_query_document(
            source_id=SOURCE,
            snapshot_id="snap-1",
            question="revenue by product category",
            sql=(
                "SELECT p.category, SUM(oi.quantity * oi.unit_price) AS revenue "
                "FROM order_items oi JOIN products p ON p.id = oi.product_id GROUP BY p.category"
            ),
            pair_id=1,
        )
    )

    embedder = FakeEmbeddingProvider(dimension=128)
    result = embedder.embed_documents([d.searchable_text() for d in documents])
    for document, vector in zip(documents, result.vectors, strict=True):
        document.embedding = vector
        document.embedding_version = result.version.key

    store = InMemoryRetrievalIndex()
    store.index(documents)
    return store


@pytest.fixture
def model() -> FakeProvider:
    """A fake model that answers the questions these tests ask, and nothing else."""
    provider = FakeProvider()

    def understanding(intent_type: str = "lookup", **extra) -> str:
        return json.dumps({"intent_type": intent_type, "requires_clarification": False, **extra})

    provider.add_rule(
        lambda r: r.prompt_name == "understand", understanding(), label="default understanding"
    )
    provider.add_rule(
        lambda r: r.prompt_name == "summarize",
        json.dumps(
            {
                "answer": "Mumbai has the most customers.",
                "observations": ["Two of the four customers are in Mumbai."],
                "caveats": [],
                "follow_ups": ["How has that changed over time?"],
            }
        ),
        label="default summary",
    )
    provider.add_rule(
        lambda r: r.prompt_name in {"generate_sql", "repair_sql"},
        json.dumps(
            {
                "sql": "SELECT city, COUNT(*) AS n FROM customers GROUP BY city",
                "rationale": "count per city",
            }
        ),
        label="default sql",
    )
    return provider


@pytest.fixture
def make_graph(index, model, target) -> Callable[..., tuple]:
    """Returns ``build(**dep_overrides) -> (compiled_graph, deps, saver_context)``."""

    def build(**overrides):
        deps = GraphDeps(
            router=ModelRouter(
                provider_factory=lambda _p: model,
                available=EnabledProfiles((FAKE_PROFILE,), {}),
            ),
            index=index,
            embedder=FakeEmbeddingProvider(dimension=128),
            resolve_source=lambda source_id: target if source_id == SOURCE else _unknown(source_id),
            egress_policy=EgressPolicy.LOCAL_ONLY,
            policy_level=PolicyLevel.STANDARD,
            network_level=NetworkPolicyLevel.PRIVATE_ALLOWED,
            **overrides,
        )
        return deps

    return build


def _unknown(source_id: str):
    raise KeyError(source_id)


@pytest.fixture
def run_graph(make_graph):
    """Run a question to completion (or to an interrupt) against a durable SQLite checkpointer."""

    def runner(question: str, *, thread: str = "t1", resume=None, deps=None, **state_kwargs):
        from app.graph.state import initial_state

        deps = deps or make_graph()
        with SqliteSaver.from_conn_string(":memory:") as saver:
            graph = compile_query_graph(deps, checkpointer=saver)
            config = {"configurable": {"thread_id": thread}}
            state = initial_state(question=question, source_id=SOURCE, **state_kwargs)
            result = graph.invoke(state, config)
            if resume is not None and result.get("__interrupt__"):
                from langgraph.types import Command

                result = graph.invoke(Command(resume=resume), config)
            return result, graph, config

    return runner
