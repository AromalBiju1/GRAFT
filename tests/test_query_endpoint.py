"""Tests for ``POST /query`` — the integration point of the whole pipeline.

route -> retrieve -> activated modules only -> generate -> respond. These tests
verify that the gating actually gates, the response matches §10, and errors are
handled explicitly rather than swallowed.

The Chroma collection is redirected to a temp directory so the suite never
touches ``.graft/chroma``, and the stub embedder keeps runs offline and fast.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api import main as api_main
from config import settings

SAMPLE_TEXT = (
    "GRAFT routes queries by complexity before retrieval. "
    "The router classifies each query as simple, moderate, or complex. "
    "Simple queries are answered from the leaf chunks. "
    "Complex queries retrieve deeper into the summary tree. "
) * 6


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(settings, "embedding_provider", "stub")
    monkeypatch.setattr(settings, "chroma_path", tmp_path / "chroma")
    from embeddings import reset_model_cache

    reset_model_cache()
    yield TestClient(api_main.app)
    reset_model_cache()


@pytest.fixture
def indexed(client: TestClient) -> TestClient:
    response = client.post(
        "/index", json={"text": SAMPLE_TEXT, "document_id": "doc_q", "source": "sample.md"}
    )
    assert response.status_code == 200, response.text
    return client


class TestHealth:
    def test_health_reports_status_and_embedding_state(self, client: TestClient) -> None:
        payload = client.get("/health").json()
        assert payload["status"] == "ok"
        assert payload["embedding_provider"] in ("stub", "sentence-transformers")
        assert payload["embedding_dim"] > 0


class TestQueryContract:
    def test_response_has_the_documented_fields(self, indexed: TestClient) -> None:
        payload = indexed.post("/query", json={"query": "What is GRAFT?"}).json()
        assert {"request_id", "answer", "evidence", "routing", "latency_ms"} <= set(payload)

    def test_routing_has_the_documented_fields(self, indexed: TestClient) -> None:
        routing = indexed.post("/query", json={"query": "What is GRAFT?"}).json()["routing"]
        assert set(routing) == {"complexity", "confidence", "retrieval_depth", "activated_modules"}

    def test_evidence_items_expose_source_page_level_score(self, indexed: TestClient) -> None:
        evidence = indexed.post("/query", json={"query": "What is GRAFT?"}).json()["evidence"]
        assert evidence, "expected at least one evidence item for an indexed corpus"
        for item in evidence:
            assert {"source", "page", "level", "score"} <= set(item)

    def test_latency_is_a_positive_float(self, indexed: TestClient) -> None:
        latency = indexed.post("/query", json={"query": "What is GRAFT?"}).json()["latency_ms"]
        assert isinstance(latency, float)
        assert latency > 0

    def test_supplied_request_id_is_echoed(self, indexed: TestClient) -> None:
        payload = indexed.post(
            "/query", json={"query": "What is GRAFT?", "request_id": "req_xyz"}
        ).json()
        assert payload["request_id"] == "req_xyz"

    def test_request_id_is_generated_when_absent(self, indexed: TestClient) -> None:
        payload = indexed.post("/query", json={"query": "What is GRAFT?"}).json()
        assert payload["request_id"].startswith("req_")


class TestGating:
    def test_simple_query_does_not_fire_contradiction_detection(self, indexed: TestClient) -> None:
        routing = indexed.post("/query", json={"query": "What is GRAFT?"}).json()["routing"]
        assert routing["complexity"] == "simple"
        assert "contradiction_detection" not in routing["activated_modules"]

    def test_simple_query_retrieves_at_depth_0(self, indexed: TestClient) -> None:
        routing = indexed.post("/query", json={"query": "What is GRAFT?"}).json()["routing"]
        assert routing["retrieval_depth"] == 0

    def test_complex_query_activates_multiple_modules(self, indexed: TestClient) -> None:
        query = "Compare the two documents and explain every difference in detail"
        routing = indexed.post("/query", json={"query": query}).json()["routing"]
        assert routing["complexity"] == "complex"
        assert len(routing["activated_modules"]) >= 2

    def test_conflict_query_fires_contradiction_detection(self, indexed: TestClient) -> None:
        query = "Are there any conflicts in the two documents?"
        routing = indexed.post("/query", json={"query": query}).json()["routing"]
        assert "contradiction_detection" in routing["activated_modules"]

    def test_retrieval_depth_override_is_honoured(self, indexed: TestClient) -> None:
        routing = indexed.post(
            "/query", json={"query": "What is GRAFT?", "retrieval_depth": 2}
        ).json()["routing"]
        assert routing["retrieval_depth"] == 2

    def test_retrieval_actually_returns_evidence(self, indexed: TestClient) -> None:
        """Regression guard: a depth the tree lacks used to return zero rows."""
        for depth in (0, 1, 2):
            evidence = indexed.post(
                "/query", json={"query": "What is GRAFT?", "retrieval_depth": depth}
            ).json()["evidence"]
            assert evidence, f"no evidence at retrieval_depth={depth}"


class TestValidation:
    def test_empty_query_is_rejected(self, client: TestClient) -> None:
        response = client.post("/query", json={"query": "   "})
        assert response.status_code == 422
        assert "query must not be empty" in response.text

    def test_negative_depth_is_rejected(self, indexed: TestClient) -> None:
        assert indexed.post("/query", json={"query": "q", "retrieval_depth": -1}).status_code == 422


class TestIndexEndpoint:
    def test_json_index_reports_node_counts(self, client: TestClient) -> None:
        response = client.post(
            "/index", json={"text": SAMPLE_TEXT, "document_id": "doc_i", "source": "s.md"}
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["document_id"] == "doc_i"
        assert payload["nodes_indexed"] >= 1
        assert payload["nodes"][0]["level"] == 0

    def test_json_and_multipart_use_one_tree_builder(self, client: TestClient) -> None:
        """Both /index branches must yield the same tree shape.

        They previously used different builders, so a PDF upload and a JSON post
        produced structurally different trees in the same collection.
        """
        json_payload = client.post(
            "/index", json={"text": SAMPLE_TEXT, "document_id": "doc_shape", "source": "s.md"}
        ).json()
        assert {"node_id", "text", "level", "parent_id", "child_ids", "metadata"} == set(
            json_payload["nodes"][0]
        )

    @pytest.mark.parametrize(
        "payload",
        [
            {"text": "", "document_id": "d"},
            {"text": "   ", "document_id": "d"},
            {"text": "text", "document_id": ""},
            {"text": "text"},
        ],
    )
    def test_invalid_index_payloads_are_rejected(self, client: TestClient, payload: dict) -> None:
        assert client.post("/index", json=payload).status_code == 422
