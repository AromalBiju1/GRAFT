"""Tests for the embedding layer (real model with a deterministic stub fallback).

The critical property is that the *indexing* and *query* sides agree on vector
dimension: Chroma fixes a dimension per collection, so a mismatch makes every
query fail with
``InvalidArgumentError: Collection expecting embedding with dimension of 16, got 384``.
"""

from __future__ import annotations

import math

import pytest

from config import settings
from embeddings import (
    STUB_EMBEDDING_DIM,
    _load_model,
    active_provider,
    collection_name,
    embed_text,
    embed_texts,
    embedding_dim,
)

#: all-MiniLM-L6-v2 output width, per docs/interfaces.md §15.
MINILM_DIM = 384


def _model_available() -> bool:
    """Probe the real model once, retrying once to ride out a cold/blip cache.

    Resolved at import time and cached, so every test in this module makes the
    *same* skip decision. Probing per-test (as a bare ``skipif`` expression
    would) let a transient load failure silently drop three tests from a run
    that otherwise passed, which hides lost coverage.

    The probe reuses the session-cached model (see ``tests/conftest.py``) and
    only resets between attempts when the first load actually failed, so
    collection does not pay for — or evict — an already-loaded model.
    """
    if _load_model() is not None:
        return True
    import embeddings as _emb

    _emb.reset_model_cache()
    return _load_model() is not None


REAL_MODEL_AVAILABLE = _model_available()
REAL_MODEL_SKIP_REASON = "sentence-transformers model unavailable (offline CI)"
requires_real_model = pytest.mark.skipif(
    not REAL_MODEL_AVAILABLE, reason=REAL_MODEL_SKIP_REASON
)


# NOTE: ``stub_provider`` now lives in ``tests/conftest.py`` as a shared
# fixture. It forces the stub embedder without resetting the model cache, so
# stub tests no longer evict the session model and force a reload in the next
# real-model test (issue #69).


class TestProviderSelection:
    def test_stub_provider_is_reported_as_stub(self, stub_provider: None) -> None:
        assert active_provider() == "stub"

    def test_stub_provider_yields_stub_dimension(self, stub_provider: None) -> None:
        assert embedding_dim() == STUB_EMBEDDING_DIM

    def test_unknown_provider_falls_back_to_stub(
        self, monkeypatch: pytest.MonkeyPatch, stub_provider: None
    ) -> None:
        # ``stub_provider`` already forces the stub path (and preserves the
        # session model cache); an unknown provider must behave the same way.
        monkeypatch.setattr(settings, "embedding_provider", "not-a-real-provider")
        assert active_provider() == "stub"
        assert embedding_dim() == STUB_EMBEDDING_DIM

    @requires_real_model
    def test_real_model_yields_384_dimensions(self) -> None:
        assert active_provider() == "sentence-transformers"
        assert embedding_dim() == MINILM_DIM


class TestEmbedText:
    def test_returns_floats(self, stub_provider: None) -> None:
        vector = embed_text("hello world")
        assert len(vector) == STUB_EMBEDDING_DIM
        assert all(isinstance(v, float) for v in vector)

    def test_identical_text_gives_identical_embedding(self, stub_provider: None) -> None:
        assert embed_text("repeatable") == embed_text("repeatable")

    def test_different_text_gives_different_embedding(self, stub_provider: None) -> None:
        assert embed_text("alpha") != embed_text("beta")

    def test_stub_embedding_is_l2_normalised(self, stub_provider: None) -> None:
        vector = embed_text("normalised please")
        assert math.isclose(math.sqrt(sum(v * v for v in vector)), 1.0, rel_tol=1e-6)

    def test_rejects_non_string(self, stub_provider: None) -> None:
        with pytest.raises(TypeError):
            embed_text(123)  # type: ignore[arg-type]

    @requires_real_model
    def test_real_embedding_dimension_matches_embedding_dim(self) -> None:
        # The property that keeps Chroma inserts and queries compatible.
        assert len(embed_text("some document text")) == embedding_dim()

    @requires_real_model
    def test_real_embeddings_are_semantically_ordered(self) -> None:
        """Similar texts must embed closer than unrelated ones.

        This is the check the hash stub can never pass, and the reason retrieval
        quality depends on the real model.
        """
        anchor = embed_text("The Transmission Control Protocol governs reliable transport.")
        near = embed_text("TCP is the transport protocol that ensures reliable delivery.")
        far = embed_text("Bananas are a yellow tropical fruit grown in the tropics.")

        def cosine(a: list[float], b: list[float]) -> float:
            return sum(x * y for x, y in zip(a, b, strict=True))

        assert cosine(anchor, near) > cosine(anchor, far)


class TestEmbedTexts:
    def test_empty_batch(self, stub_provider: None) -> None:
        assert embed_texts([]) == []

    def test_batch_matches_individual_calls(self, stub_provider: None) -> None:
        texts = ["alpha", "beta", "gamma"]
        assert embed_texts(texts) == [embed_text(t) for t in texts]


class TestCollectionName:
    def test_stub_collection_is_dimension_qualified(self, stub_provider: None) -> None:
        name = collection_name("graft_tree_nodes")
        assert name.startswith("graft_tree_nodes")
        assert str(STUB_EMBEDDING_DIM) in name
        assert "stub" in name

    def test_collection_name_is_usable_by_chroma(self, stub_provider: None) -> None:
        # Chroma requires 3-512 chars, alphanumerics/dash/underscore, and
        # alphanumeric start and end.
        name = collection_name()
        assert 3 <= len(name) <= 512
        assert name[0].isalnum() and name[-1].isalnum()
        assert all(c.isalnum() or c in "-_" for c in name)

    def test_switching_embedder_changes_the_collection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression guard for the 16-vs-384 dimension crash.

        Both embedders must resolve to *different* collections, otherwise
        writing real vectors into a stub-built collection fails.

        Provider switching alone is sufficient — no cache reset is needed
        (the session model from ``tests/conftest.py`` is reused, avoiding a
        full reload per run; issue #69).
        """
        monkeypatch.setattr(settings, "embedding_provider", "stub")
        stub_name = collection_name()
        monkeypatch.setattr(settings, "embedding_provider", "sentence-transformers")
        real_name = collection_name()
        if active_provider() == "sentence-transformers":
            assert real_name != stub_name
