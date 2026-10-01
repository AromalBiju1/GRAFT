"""Tests for ``baseline.run_baseline`` — the flat "retrieve everything" path.

GRAFT is benchmarked against this baseline (``docs/scope.md`` §5). If the
baseline ever gains complexity-based gating or starts skipping modules, the
comparison silently becomes meaningless, so these tests pin its contract.

Everything here is a **unit** test: ``baseline.retrieve`` is monkeypatched with
a fake retriever and the query embedding is caller-supplied, so no test loads
the sentence-transformers model or touches the network. The baseline's
behaviour against the *real* embedder is already covered by
``tests/test_integration.py::TestBaselineComparison``.
"""

from __future__ import annotations

from typing import Any

import pytest

import baseline
import generation
from modules.base import ModuleResult
from tests.modules_helpers import passage

EXPECTED_MODULES = {
    "fact_lookup",
    "multi_hop",
    "numeric_reasoning",
    "contradiction_detection",
}

SIMPLE_QUERY = "What is the capital of France?"
COMPLEX_QUERY = (
    "Compare the reported revenue growth across both reports, compute the "
    "difference in percentage points, and flag any contradictions between them."
)


def fake_embed(text: str) -> list[float]:
    """Deterministic stand-in embedder: no model, no network."""
    return [float(len(text) % 7), 0.5, -0.25, 1.0]


class FakeRetriever:
    """Records its arguments and returns canned §5-shaped passages."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.passages = [
            passage("doc_001_chunk_0000", "Paris is the capital of France.", 0.95),
            passage("doc_001_chunk_0001", "Revenue grew 12% in 2023 to $4.2 million.", 0.80),
            passage("doc_002_chunk_0000", "Revenue grew 9% in 2023 to $4.0 million.", 0.70),
        ]

    def __call__(self, query_embedding: list[float], **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append({"query_embedding": query_embedding, **kwargs})
        return [dict(p) for p in self.passages]


@pytest.fixture
def retriever(monkeypatch: pytest.MonkeyPatch) -> FakeRetriever:
    """Replace baseline retrieval with a fake that records calls for assertions."""
    fake = FakeRetriever()
    monkeypatch.setattr(baseline, "retrieve", fake)
    return fake


class TestNoGating:
    @pytest.mark.parametrize("query", [SIMPLE_QUERY, COMPLEX_QUERY, "Hi"])
    def test_fires_all_modules_regardless_of_complexity(
        self, retriever: FakeRetriever, query: str
    ) -> None:
        """No complexity gating: simple and complex queries fire the same 4 modules."""
        out = baseline.run_baseline("req_1", query, fake_embed(query))

        assert len(out["module_results"]) == 4
        assert {m["module"] for m in out["module_results"]} == EXPECTED_MODULES

    def test_retrieval_is_not_depth_gated(self, retriever: FakeRetriever) -> None:
        """The flat baseline never passes a ``retrieval_depth``: it scans ungated."""
        baseline.run_baseline("req_1", SIMPLE_QUERY, fake_embed(SIMPLE_QUERY))

        assert "retrieval_depth" not in retriever.calls[0]

    def test_does_not_use_the_router(self, retriever: FakeRetriever) -> None:
        """``baseline`` must not import or consult the router at all."""
        assert not hasattr(baseline, "route")


class TestResponseShape:
    def test_mode_is_baseline_flat(self, retriever: FakeRetriever) -> None:
        out = baseline.run_baseline("req_1", SIMPLE_QUERY, fake_embed(SIMPLE_QUERY))

        assert out["mode"] == "baseline_flat"

    def test_output_includes_module_results_and_retrieval(self, retriever: FakeRetriever) -> None:
        out = baseline.run_baseline("req_1", SIMPLE_QUERY, fake_embed(SIMPLE_QUERY))

        assert isinstance(out["module_results"], list)
        assert len(out["module_results"]) == 4
        assert all(isinstance(m, dict) for m in out["module_results"])
        assert out["retrieval"] == retriever.passages

    def test_output_has_request_id_answer_and_evidence(self, retriever: FakeRetriever) -> None:
        out = baseline.run_baseline("req_42", SIMPLE_QUERY, fake_embed(SIMPLE_QUERY))

        assert out["request_id"] == "req_42"
        assert isinstance(out["answer"], str) and out["answer"]
        assert isinstance(out["evidence"], list) and out["evidence"]

    def test_uses_the_same_synthesize_as_graft(
        self, retriever: FakeRetriever, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Baseline and GRAFT must share one ``synthesize``, or answers aren't comparable."""
        assert baseline.synthesize is generation.synthesize

        seen: list[tuple[Any, ...]] = []

        def spy(
            request_id: str, query: str, context: list, module_results: list
        ) -> dict[str, Any]:
            seen.append((request_id, query, context, module_results))
            return generation.synthesize(request_id, query, context, module_results)

        monkeypatch.setattr(baseline, "synthesize", spy)
        out = baseline.run_baseline("req_1", COMPLEX_QUERY, fake_embed(COMPLEX_QUERY))

        assert len(seen) == 1
        request_id, query, context, module_results = seen[0]
        assert (request_id, query) == ("req_1", COMPLEX_QUERY)
        assert context == retriever.passages
        assert len(module_results) == 4
        assert all(isinstance(r, ModuleResult) for r in module_results)

        # answer/evidence are exactly what synthesize produced for those inputs
        expected = generation.synthesize("req_1", COMPLEX_QUERY, context, module_results)
        assert out["answer"] == expected["answer"]
        assert out["evidence"] == expected["evidence"]


class TestInjection:
    def test_accepts_injected_embedding_and_retriever_without_network(
        self, retriever: FakeRetriever
    ) -> None:
        """Embedding is caller-supplied and retrieval swappable: nothing hits the network."""
        embedding = fake_embed(SIMPLE_QUERY)
        baseline.run_baseline(
            "req_1",
            SIMPLE_QUERY,
            embedding,
            n_results=3,
            filters={"document_id": "doc_001"},
        )

        assert len(retriever.calls) == 1
        call = retriever.calls[0]
        assert call["query_embedding"] == embedding
        assert call["n_results"] == 3
        assert call["filters"] == {"document_id": "doc_001"}


class TestDegenerateInput:
    def test_empty_retrieval_still_fires_all_modules(self, retriever: FakeRetriever) -> None:
        """Empty retrieval must not short-circuit the module set."""
        retriever.passages = []
        out = baseline.run_baseline("req_1", SIMPLE_QUERY, fake_embed(SIMPLE_QUERY))

        assert {m["module"] for m in out["module_results"]} == EXPECTED_MODULES
        assert out["answer"]
