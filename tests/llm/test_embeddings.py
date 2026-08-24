"""Embedding providers, version stamping, and the refusal to mix incompatible vectors."""

from __future__ import annotations

import pytest

from app.embeddings.base import (
    EmbeddingVersion,
    content_hash,
    cosine_similarity,
    normalize,
)
from app.embeddings.fake import FakeEmbeddingProvider
from app.embeddings.local import DEFAULT_MODEL, KNOWN_DIMENSIONS, LocalEmbeddingProvider
from app.embeddings.service import (
    IncompatibleEmbeddingVersion,
    build_embedding_provider,
    require_compatible,
)


class TestVersioning:
    def test_key_captures_everything_that_matters(self):
        version = EmbeddingVersion("fastembed", "bge-small", 384)
        assert version.key == "fastembed:bge-small:384:n:1"

    def test_incompatible_when_any_component_differs(self):
        base = EmbeddingVersion("fastembed", "bge-small", 384)
        assert base.compatible_with(EmbeddingVersion("fastembed", "bge-small", 384))
        assert not base.compatible_with(EmbeddingVersion("fastembed", "bge-base", 384))
        assert not base.compatible_with(EmbeddingVersion("google", "bge-small", 384))
        assert not base.compatible_with(EmbeddingVersion("fastembed", "bge-small", 768))
        assert not base.compatible_with(
            EmbeddingVersion("fastembed", "bge-small", 384, normalized=False)
        )

    def test_require_compatible_explains_the_fix(self):
        query = EmbeddingVersion("fastembed", "bge-small", 384)
        index = EmbeddingVersion("google", "gemini-embedding-001", 3072)
        with pytest.raises(IncompatibleEmbeddingVersion, match="Re-embed the index"):
            require_compatible(query, index)
        require_compatible(query, query)  # no raise


class TestMath:
    def test_normalize_gives_unit_length(self):
        vector = normalize([3.0, 4.0])
        assert pytest.approx(sum(c * c for c in vector), abs=1e-9) == 1.0

    def test_normalize_handles_the_zero_vector(self):
        assert normalize([0.0, 0.0]) == [0.0, 0.0]

    def test_cosine_similarity(self):
        assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
        assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
        assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)
        assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0

    def test_dimension_mismatch_is_an_error_not_a_wrong_answer(self):
        with pytest.raises(ValueError, match="different dimensions"):
            cosine_similarity([1.0], [1.0, 2.0])

    def test_content_hash_is_stable_and_content_specific(self):
        assert content_hash("orders table") == content_hash("orders table")
        assert content_hash("orders table") != content_hash("orders  table")
        assert len(content_hash("x")) == 32


class TestFakeProvider:
    def test_deterministic_across_calls(self):
        provider = FakeEmbeddingProvider()
        assert provider.embed_query("total revenue") == provider.embed_query("total revenue")

    def test_vectors_are_normalised_and_the_right_size(self):
        provider = FakeEmbeddingProvider(dimension=32)
        vector = provider.embed_query("customers by city")
        assert len(vector) == 32
        assert pytest.approx(sum(c * c for c in vector), abs=1e-9) == 1.0

    def test_shared_vocabulary_is_more_similar_than_none(self):
        provider = FakeEmbeddingProvider(dimension=256)
        orders = provider.embed_query("orders order_items revenue total")
        related = provider.embed_query("orders revenue")
        unrelated = provider.embed_query("zebra kangaroo umbrella")
        assert cosine_similarity(orders, related) > cosine_similarity(orders, unrelated)

    def test_embed_documents_stamps_versions_and_hashes(self):
        provider = FakeEmbeddingProvider()
        result = provider.embed_documents(["customers table", "orders table"])
        assert len(result) == 2
        assert result.version.provider == "fake"
        assert result.content_hashes == [
            content_hash("customers table"),
            content_hash("orders table"),
        ]

    def test_empty_text_is_the_zero_vector_not_a_crash(self):
        assert FakeEmbeddingProvider().embed_query("") == [0.0] * 64


class TestLocalProvider:
    def test_known_dimension_is_used_without_loading_the_model(self):
        provider = LocalEmbeddingProvider()
        assert provider.model_name == DEFAULT_MODEL
        assert provider.dimension == KNOWN_DIMENSIONS[DEFAULT_MODEL] == 384
        assert provider._model is None, "the dimension lookup must not download a model"

    def test_version_reflects_the_model(self):
        version = LocalEmbeddingProvider(model_name="BAAI/bge-base-en-v1.5").version
        assert version.dimension == 768 and version.provider == "fastembed"

    def test_empty_batch_short_circuits(self):
        result = LocalEmbeddingProvider().embed_documents([])
        assert len(result) == 0 and result.vectors == []


class TestSelection:
    def test_fake_profile(self):
        assert isinstance(build_embedding_provider("fake"), FakeEmbeddingProvider)

    def test_auto_and_local_are_local(self):
        for selection in ("auto", "", "local-bge-small"):
            assert isinstance(build_embedding_provider(selection, env={}), LocalEmbeddingProvider)

    def test_model_override_from_env(self):
        provider = build_embedding_provider(
            "auto", env={"EMBEDDING_MODEL_NAME": "BAAI/bge-base-en-v1.5"}
        )
        assert provider.model_name == "BAAI/bge-base-en-v1.5"

    def test_google_is_available_but_never_the_default(self):
        from app.embeddings.service import GoogleEmbeddingProvider

        assert isinstance(build_embedding_provider("google"), GoogleEmbeddingProvider)
        assert not isinstance(build_embedding_provider("auto", env={}), GoogleEmbeddingProvider)

    def test_unknown_profile_is_rejected(self):
        with pytest.raises(ValueError, match="Unknown EMBEDDING_PROFILE"):
            build_embedding_provider("openai-ada")
