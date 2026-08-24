"""The LangChain-facing embedding client: profile resolution and the legacy variable.

The v1 tests here checked private builder functions that no longer exist. What matters now is the
observable contract: which provider a given configuration selects, that vectors come back as plain
lists for the vector store, and that the old ``EMBEDDING_PROVIDER`` variable still works.
"""

from __future__ import annotations

import logging

import pytest

from app.core.embeddings import EmbeddingClient, build_embedding_client, resolve_profile
from app.embeddings.fake import FakeEmbeddingProvider
from app.embeddings.local import LocalEmbeddingProvider
from app.embeddings.service import GoogleEmbeddingProvider


class TestProfileResolution:
    def test_defaults_to_local(self):
        assert resolve_profile(env={}) == "auto"

    def test_explicit_profile_wins(self):
        assert resolve_profile(env={"EMBEDDING_PROFILE": "fake"}) == "fake"
        assert (
            resolve_profile(env={"EMBEDDING_PROFILE": "fake", "EMBEDDING_PROVIDER": "google"})
            == "fake"
        )

    @pytest.mark.parametrize(
        ("legacy", "expected"),
        [
            ("google", "google"),
            ("gemini", "google"),
            ("huggingface", "auto"),
            ("local", "auto"),
            ("something-unknown", "auto"),
        ],
    )
    def test_legacy_variable_is_mapped(self, legacy: str, expected: str, caplog):
        # The project's loggers set propagate=False, so caplog needs to be attached directly.
        app_logger = logging.getLogger("app.core.embeddings")
        app_logger.addHandler(caplog.handler)
        try:
            with caplog.at_level(logging.WARNING):
                assert resolve_profile(env={"EMBEDDING_PROVIDER": legacy}) == expected
        finally:
            app_logger.removeHandler(caplog.handler)
        assert "deprecated" in caplog.text.lower()


class TestClient:
    def test_returns_plain_vectors_for_the_vector_store(self):
        client = build_embedding_client("fake")
        vectors = client.embed_documents(["customers table", "orders table"])
        assert isinstance(vectors, list) and len(vectors) == 2
        assert all(isinstance(v, list) and isinstance(v[0], float) for v in vectors)
        assert isinstance(client.embed_query("how many customers"), list)

    def test_exposes_the_embedding_version(self):
        client = build_embedding_client("fake")
        assert client.version.provider == "fake"
        assert client.version.dimension == len(client.embed_query("x"))
        assert client.provider_name == "fake"

    def test_selects_the_right_provider(self):
        assert isinstance(build_embedding_client("fake")._provider, FakeEmbeddingProvider)
        assert isinstance(build_embedding_client("auto")._provider, LocalEmbeddingProvider)
        assert isinstance(build_embedding_client("google")._provider, GoogleEmbeddingProvider)

    def test_is_the_documented_adapter_type(self):
        assert isinstance(build_embedding_client("fake"), EmbeddingClient)

    def test_empty_batch(self):
        assert build_embedding_client("fake").embed_documents([]) == []
