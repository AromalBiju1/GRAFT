"""Tests for ``baseline.run_baseline`` — the flat "retrieve everything" path.
GRAFT is benchmarked against this baseline (docs/scope.md §5). If it ever
gains complexity-based gating or skips modules, the comparison silently
becomes meaningless, so these tests pin its contract.
 
Two layers:
 
* **Unit tests** inject a fake query embedding and a fake retriever
  (``baseline.retrieve`` is monkeypatched), so they make no network calls and
  never load the sentence-transformers model.
* **Integration tests** (marked ``integration``) use the real embedder and
  skip cleanly when the model is unavailable.
"""
 
from __future__ import annotations
 
from pathlib import Path
from typing import Any
 
import pytest
from fastapi.testclient import TestClient
 
import baseline
import generation
from api import main as api_main
from config import settings
from embeddings import _load_model, active_provider, reset_model_cache  # noqa: F401
from modules.base import ModuleResult
from tests.modules_helpers import passage

import time
from pathlib import Path
 
REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_MD = REPO_ROOT / "data" / "sample_docs" / "sample_docs.md"
 
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
    """Records calls and returns canned §5-shaped passages."""
 
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
    fake = FakeRetriever()
    monkeypatch.setattr(baseline, "retrieve", fake)
    return fake
 
 
class TestBaselineUnit:
    @pytest.mark.parametrize("query", [SIMPLE_QUERY, COMPLEX_QUERY])
    def test_fires_all_modules_regardless_of_complexity(
        self, retriever: FakeRetriever, query: str
    ) -> None:
        """No complexity gating: simple and complex queries fire the same 4 modules."""
        out = baseline.run_baseline("req_1", query, fake_embed(query))
 
        assert len(out["module_results"]) == 4
        assert {m["module"] for m in out["module_results"]} == EXPECTED_MODULES
 
    def test_simple_query_still_fires_all_modules(self, retriever: FakeRetriever) -> None:
        out = baseline.run_baseline("req_simple", "Hi", fake_embed("Hi"))
 
        assert {m["module"] for m in out["module_results"]} == EXPECTED_MODULES
 
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
        """Baseline and GRAFT must share one synthesize, or answers aren't comparable."""
        assert baseline.synthesize is generation.synthesize
        assert api_main.synthesize is generation.synthesize
 
        seen: list[tuple[Any, ...]] = []
 
        def spy(request_id: str, query: str, context: list, module_results: list) -> dict:
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
 
    def test_accepts_injected_embedding_and_retriever_without_network(
        self, retriever: FakeRetriever
    ) -> None:
        """The embedding is caller-supplied and retrieval is swappable: nothing hits the network."""
        embedding = fake_embed(SIMPLE_QUERY)
        baseline.run_baseline(
            "req_1", SIMPLE_QUERY, embedding, n_results=3, filters={"document_id": "doc_001"}
        )
 
        assert len(retriever.calls) == 1
        call = retriever.calls[0]
        assert call["query_embedding"] == embedding
        assert call["n_results"] == 3
        assert call["filters"] == {"document_id": "doc_001"}
 
    def test_retrieval_is_not_depth_gated(self, retriever: FakeRetriever) -> None:
        """Flat baseline never passes a retrieval_depth: it scans without router gating."""
        baseline.run_baseline("req_1", SIMPLE_QUERY, fake_embed(SIMPLE_QUERY))
 
        assert "retrieval_depth" not in retriever.calls[0]
 
    def test_empty_retrieval_still_fires_all_modules(
        self, retriever: FakeRetriever
    ) -> None:
        retriever.passages = []
        out = baseline.run_baseline("req_1", SIMPLE_QUERY, fake_embed(SIMPLE_QUERY))
 
        assert {m["module"] for m in out["module_results"]} == EXPECTED_MODULES
        assert out["answer"]


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(settings, "chroma_path", tmp_path / "chroma")
    reset_model_cache()
    if active_provider() != "sentence-transformers":  # pragma: no cover - offline
        pytest.skip("sentence-transformers model unavailable; skipping integration test")
    return TestClient(api_main.app)


@pytest.fixture
def indexed(client: TestClient) -> TestClient:
    text = SAMPLE_MD.read_text(encoding="utf-8")
    response = client.post(
        "/index", json={"text": text, "document_id": "sample_docs", "source": "sample_docs.md"}
    )
    assert response.status_code == 200, response.text
    return client


class TestRealEmbedder:
    def test_real_model_is_used(self, client: TestClient) -> None:
        health = client.get("/health").json()
        assert health["embedding_provider"] == "sentence-transformers"
        assert health["embedding_dim"] == 384

    def test_model_loads_without_error(self) -> None:
        assert _load_model() is not None


class TestIndexThenQuery:
    def test_simple_factual_query_returns_grounded_evidence(self, indexed: TestClient) -> None:
        payload = indexed.post(
            "/query", json={"query": "What documents are in the collection?"}
        ).json()

        assert payload["routing"]["complexity"] == "simple"
        assert payload["routing"]["retrieval_depth"] == 0
        assert payload["evidence"], "expected grounded evidence"
        assert payload["latency_ms"] > 0

        # The answer is the top-ranked passage from the indexed document, so it
        # must actually come from that document rather than being invented.
        joined = " ".join(e.get("text") or "" for e in payload["evidence"])
        assert "1705.pdf" in joined or "1706.pdf" in joined

    def test_answer_is_grounded_in_retrieved_context(self, indexed: TestClient) -> None:
        payload = indexed.post("/query", json={"query": "Which documents are listed?"}).json()
        top = payload["evidence"][0].get("text") or ""
        assert payload["answer"][:60] in top or top[:60] in payload["answer"]

    def test_complex_comparison_query_activates_multiple_modules(self, indexed: TestClient) -> None:
        query = "Compare the documents and explain every difference in detail"
        payload = indexed.post("/query", json={"query": query}).json()
        assert payload["routing"]["complexity"] == "complex"
        assert len(payload["routing"]["activated_modules"]) >= 2
        assert payload["evidence"]

    def test_semantic_retrieval_ranks_the_right_passage(self, indexed: TestClient) -> None:
        """The check the hash stub could never pass.

        Asking about a specific document must retrieve that document's passage
        above unrelated ones.
        """
        payload = indexed.post(
            "/query", json={"query": "Which document is the original Transformer paper?"}
        ).json()
        top_text = payload["evidence"][0].get("text") or ""
        assert "1706" in top_text or "Transformer" in top_text

    def test_retrieval_depth_2_still_returns_evidence(self, indexed: TestClient) -> None:
        payload = indexed.post(
            "/query", json={"query": "What documents are in the collection?", "retrieval_depth": 2}
        ).json()
        assert payload["evidence"], "a deep query must fall back to shallower levels"

    def test_full_pipeline_completes_quickly(self, indexed: TestClient) -> None:
        start = time.perf_counter()
        indexed.post("/query", json={"query": "What is in the collection?"})
        assert time.perf_counter() - start < 30.0


class TestBaselineComparison:
    def test_baseline_runs_all_modules_regardless_of_complexity(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The flat baseline must not gate, or the comparison is meaningless."""
        from baseline import run_baseline
        from embeddings import embed_text

        text = SAMPLE_MD.read_text(encoding="utf-8")
        client.post("/index", json={"text": text, "document_id": "sd", "source": "sample_docs.md"})

        out = run_baseline("req_base", "What documents are in the collection?", embed_text("query"))
        assert out["mode"] == "baseline_flat"
        assert len(out["module_results"]) == 4
        assert {m["module"] for m in out["module_results"]} == {
            "fact_lookup",
            "multi_hop",
            "numeric_reasoning",
            "contradiction_detection",
        }

    def test_baseline_answer_shape_matches_graft(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Both systems must speak the same response shape to be comparable."""
        from baseline import run_baseline
        from embeddings import embed_text

        text = SAMPLE_MD.read_text(encoding="utf-8")
        client.post("/index", json={"text": text, "document_id": "sd2", "source": "sample_docs.md"})

        graft_payload = client.post("/query", json={"query": "What documents are listed?"}).json()
        baseline = run_baseline(
            "req_base", "What documents are listed?", embed_text("What documents are listed?")
        )

        assert {"request_id", "answer", "evidence"} <= set(graft_payload)
        assert {"request_id", "answer", "evidence"} <= set(baseline)
        assert graft_payload["answer"] and baseline["answer"]
