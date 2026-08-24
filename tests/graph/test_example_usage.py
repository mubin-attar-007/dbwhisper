"""The graph reports which verified examples it showed; it never records them itself.

Crediting usage is a write to the application database. A graph node that opens its own session is
a node that can be handed a credential later, which is exactly what the checkpointed-state rule
exists to prevent — so the node calls an injected sink and the API layer does the writing.

An import-linter contract in ``pyproject.toml`` enforces the structural half (``app.graph`` cannot
import ``db.database_manager``). These cover the behaviour.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

from app.graph.nodes import _credit_verified_examples


class TestTheGraphStaysOutOfTheDatabase:
    def test_the_module_does_not_reach_for_a_database(self):
        import app.graph.nodes as nodes_module

        source = inspect.getsource(nodes_module)
        assert "db.database_manager" not in source
        assert "from db.verified_queries" not in source

    def test_deps_carries_the_sink_rather_than_the_node_importing_one(self):
        from app.graph.deps import GraphDeps

        assert "on_examples_used" in GraphDeps.__dataclass_fields__


class TestReporting:
    def test_the_sink_receives_the_rendered_pair_ids(self):
        seen: list[list[str]] = []
        _credit_verified_examples(SimpleNamespace(on_examples_used=seen.append), ["7", "9"])
        assert seen == [["7", "9"]]

    def test_a_missing_sink_is_simply_not_called(self):
        _credit_verified_examples(SimpleNamespace(on_examples_used=None), ["1", "2"])

    def test_no_ids_means_no_call(self):
        def boom(_ids):
            raise AssertionError("must not be called with an empty list")

        _credit_verified_examples(SimpleNamespace(on_examples_used=boom), [])

    def test_a_failing_sink_never_breaks_the_run(self):
        def explode(_ids):
            raise RuntimeError("the application database is down")

        _credit_verified_examples(SimpleNamespace(on_examples_used=explode), ["1"])

    def test_the_sink_gets_a_copy_it_cannot_use_to_mutate_the_pack(self):
        original = ["1", "2"]
        captured: list[list[str]] = []
        _credit_verified_examples(SimpleNamespace(on_examples_used=captured.append), original)
        captured[0].append("3")
        assert original == ["1", "2"]


class TestTheApiLayerDoesTheWriting:
    def test_the_v2_deps_wire_the_sink(self):
        import app.api.v2.deps as v2_deps

        source = inspect.getsource(v2_deps)
        assert "on_examples_used=" in source, "the graph would report into the void"
        assert "record_use" in source

    def test_a_non_numeric_id_is_skipped_rather_than_raising(self, monkeypatch):
        import app.api.v2.deps as v2_deps

        recorded: list[int] = []
        monkeypatch.setattr("db.verified_queries.record_use", recorded.append)
        v2_deps._record_example_usage(["12", "not-an-id", "34"])
        assert recorded == [12, 34]
