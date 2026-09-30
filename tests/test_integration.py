"""End-to-end integration test: index -> query -> verify.

This is the "does it all work together" test. It runs the *real* pipeline with
the *real* sentence-transformers embedder (the stub cannot be used: it makes
retrieval semantically meaningless, so a test built on it would pass while the
system retrieved nonsense). It is marked ``integration`` and skips cleanly when
the model is unavailable, so offline CI is not blocked.

Chroma is redirected to a temp directory and the sample documents are parsed
from ``data/sample_docs``.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api import main as api_main
from config import settings
from embeddings import _load_model, active_provider, reset_model_cache

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DOCS = REPO_ROOT / "data" / "sample_docs"
SAMPLE_MD = SAMPLE_DOCS / "sample_docs.md"

pytestmark = pytest.mark.integration


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
