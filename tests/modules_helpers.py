"""Shared fixtures and builders for specialist-module tests."""

from __future__ import annotations

from typing import Any

import pytest

from graft.modules.base import ModuleResult

#: Retrieval results are dicts per docs/interfaces.md section 5.
PASSAGE_TEMPLATE: dict[str, Any] = {
    "chunk_id": "doc_001_chunk_0000",
    "text": "placeholder",
    "distance": 0.1,
    "score": 0.9,
    "metadata": {"source": "sample.md", "level": 0, "node_id": "doc_001_chunk_0000"},
}


def passage(chunk_id: str, text: str, score: float = 0.9, level: int = 0) -> dict[str, Any]:
    """Build one retrieval result dict in the §5 shape."""
    return {
        "chunk_id": chunk_id,
        "text": text,
        "distance": round(1.0 - score, 4),
        "score": score,
        "metadata": {"source": "sample.md", "page": 1, "level": level, "node_id": chunk_id},
    }


@pytest.fixture
def context() -> list[dict[str, Any]]:
    return [passage("c0", "Paris is the capital of France.", 0.95)]


@pytest.fixture
def result_fields() -> tuple[str, ...]:
    """The §7 common output fields every module must return."""
    return ("request_id", "module", "result", "evidence", "confidence", "metadata")


def assert_common_output(result: ModuleResult, request_id: str, module: str) -> None:
    """Assert a module honours the §7 common output contract."""
    payload = result.to_dict()
    assert set(payload) == {"request_id", "module", "result", "evidence", "confidence", "metadata"}
    assert payload["request_id"] == request_id
    assert payload["module"] == module
    assert 0.0 <= payload["confidence"] <= 1.0
    assert isinstance(payload["evidence"], list)
    assert isinstance(payload["metadata"], dict)
