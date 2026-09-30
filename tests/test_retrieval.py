"""Tests for depth-aware retrieval against a real (temp) Chroma collection.

The depth semantics are load-bearing: ``retrieval_depth`` is the *deepest* level
to consider, not the only level. Restricting to a single exact level silently
returned nothing whenever a document's tree lacked that level, which made the
router's simple/moderate/complex split look like it changed nothing.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from config import settings
from embeddings import embed_text, reset_model_cache
from indexing.pipeline import index_document
from retrieval import MAX_RETRIEVAL_DEPTH, retrieve

DOC_TEXT = (
    "The transmission control protocol provides reliable ordered delivery. "
    "Congestion control uses slow start and a congestion window. "
    "The user datagram protocol is connectionless and offers no reliability. "
    "Ports are 16 bit unsigned integers identifying application endpoints. "
) * 8


@pytest.fixture
def collection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A temp Chroma collection seeded with a real multi-level tree."""
    monkeypatch.setattr(settings, "embedding_provider", "stub")
    monkeypatch.setattr(settings, "chroma_path", tmp_path / "chroma")
    reset_model_cache()
    index_document(DOC_TEXT, document_id="doc_r", source="doc.md")
    yield settings.chroma_path
    reset_model_cache()


def _levels_present() -> set[int]:
    """Levels actually stored for the seeded document."""
    from vector_store import ChromaVectorStore

    store = ChromaVectorStore(
        persist_path=settings.chroma_path, collection_name=settings.chroma_collection + "_stub_16"
    )
    try:
        found: set[int] = set()
        for level in range(MAX_RETRIEVAL_DEPTH + 1):
            found |= {
                int(h["metadata"]["level"])
                for h in store.query(embed_text("x"), 50, {"level": level})
            }
        return found
    finally:
        store.close()


class TestDepthSemantics:
    def test_depth_0_searches_only_leaves(self, collection: Path) -> None:
        hits = retrieve(embed_text("reliable delivery"), n_results=5, retrieval_depth=0)
        assert hits
        assert {h["metadata"]["level"] for h in hits} == {0}

    def test_depth_includes_shallower_levels(self, collection: Path) -> None:
        """Regression guard: an exact-level filter returned zero rows here."""
        for depth in range(MAX_RETRIEVAL_DEPTH + 1):
            hits = retrieve(embed_text("ports"), n_results=5, retrieval_depth=depth)
            assert hits, f"retrieval_depth={depth} returned nothing"
            assert {h["metadata"]["level"] for h in hits} <= set(range(depth + 1))

    def test_deeper_depth_never_narrows_the_result_set(self, collection: Path) -> None:
        previous: set[str] = set()
        for depth in range(MAX_RETRIEVAL_DEPTH + 1):
            ids = {
                h["chunk_id"]
                for h in retrieve(embed_text("ports"), n_results=20, retrieval_depth=depth)
            }
            assert previous <= ids, f"depth {depth} lost results from depth {depth - 1}"
            previous = ids

    def test_results_are_capped_at_n_results(self, collection: Path) -> None:
        assert len(retrieve(embed_text("ports"), n_results=3, retrieval_depth=2)) <= 3

    def test_results_are_sorted_by_descending_score(self, collection: Path) -> None:
        scores = [
            h["score"] for h in retrieve(embed_text("ports"), n_results=10, retrieval_depth=2)
        ]
        assert scores == sorted(scores, reverse=True)

    def test_chunk_ids_are_not_duplicated_across_levels(self, collection: Path) -> None:
        ids = [
            h["chunk_id"] for h in retrieve(embed_text("ports"), n_results=20, retrieval_depth=2)
        ]
        assert len(ids) == len(set(ids))


class TestClampingAndValidation:
    def test_depth_above_max_is_clamped(self, collection: Path) -> None:
        clamped = retrieve(embed_text("ports"), n_results=5, retrieval_depth=99)
        assert clamped == retrieve(
            embed_text("ports"), n_results=5, retrieval_depth=MAX_RETRIEVAL_DEPTH
        )

    def test_negative_depth_is_clamped_to_leaves(self, collection: Path) -> None:
        assert {
            h["metadata"]["level"]
            for h in retrieve(embed_text("ports"), n_results=5, retrieval_depth=-5)
        } == {0}

    def test_none_depth_searches_every_level(self, collection: Path) -> None:
        hits = retrieve(embed_text("ports"), n_results=30, retrieval_depth=None)
        assert hits
        assert {h["metadata"]["level"] for h in hits} <= set(range(MAX_RETRIEVAL_DEPTH + 1))

    def test_extra_filters_are_merged(self, collection: Path) -> None:
        hits = retrieve(
            embed_text("ports"), n_results=5, retrieval_depth=1, filters={"document_id": "doc_r"}
        )
        assert hits
        assert {h["metadata"]["document_id"] for h in hits} == {"doc_r"}

    def test_empty_embedding_is_rejected(self, collection: Path) -> None:
        with pytest.raises(ValueError):
            retrieve([], n_results=5)

    def test_non_positive_n_results_is_rejected(self, collection: Path) -> None:
        with pytest.raises(ValueError):
            retrieve(embed_text("x"), n_results=0)


class TestShape:
    def test_results_match_the_section_5_contract(self, collection: Path) -> None:
        for hit in retrieve(embed_text("ports"), n_results=3, retrieval_depth=1):
            assert {"chunk_id", "text", "distance", "score", "metadata"} <= set(hit)
            assert isinstance(hit["metadata"], dict)
