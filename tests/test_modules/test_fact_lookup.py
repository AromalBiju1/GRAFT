"""Tests for the fact-lookup specialist module (§7.1)."""

from __future__ import annotations

import pytest

from graft.modules.fact_lookup import FactLookupModule
from tests.modules_helpers import assert_common_output, passage

REQUEST_ID = "req_001"


class TestFactLookup:
    def test_empty_context_reports_no_context(self) -> None:
        result = FactLookupModule()(REQUEST_ID, "What is the capital?", [])
        assert result.result == "No context provided."
        assert result.confidence == 0.0
        assert result.metadata["reason"] == "empty_context"

    def test_picks_highest_scoring_chunk(self) -> None:
        context = [
            passage("low", "low score text", score=0.2),
            passage("high", "high score text", score=0.95),
            passage("mid", "mid score text", score=0.6),
        ]
        result = FactLookupModule()(REQUEST_ID, "Which one?", context)
        assert result.result == "high score text"
        assert result.evidence[0]["chunk_id"] == "high"

    def test_evidence_carries_chunk_id_text_and_score(self) -> None:
        result = FactLookupModule()(
            REQUEST_ID, "Which one?", [passage("c1", "some text", score=0.7)]
        )
        evidence = result.evidence[0]
        assert evidence["chunk_id"] == "c1"
        assert evidence["text"] == "some text"
        assert evidence["score"] == 0.7

    def test_confidence_tracks_top_score(self) -> None:
        result = FactLookupModule()(REQUEST_ID, "Which one?", [passage("c1", "text", score=0.42)])
        assert result.confidence == pytest.approx(0.42)

    def test_metadata_reports_retrieved_count(self) -> None:
        context = [passage("a", "x", 0.9), passage("b", "y", 0.5)]
        assert FactLookupModule()(REQUEST_ID, "q", context).metadata["retrieved_count"] == 2

    def test_to_dict_returns_all_seven_contract_fields(self) -> None:
        result = FactLookupModule()(REQUEST_ID, "q", [passage("a", "x", 0.9)])
        assert set(result.to_dict()) == {
            "request_id",
            "module",
            "result",
            "evidence",
            "confidence",
            "metadata",
        }
        assert_common_output(result, REQUEST_ID, "fact_lookup")

    def test_blank_chunk_text_falls_back(self) -> None:
        result = FactLookupModule()(REQUEST_ID, "q", [passage("a", "   ", 0.9)])
        assert result.result == "No answer found in context."

    def test_rejects_invalid_request_id(self) -> None:
        with pytest.raises(ValueError):
            FactLookupModule()("", "q", [passage("a", "x", 0.9)])

    def test_rejects_invalid_query(self) -> None:
        with pytest.raises(ValueError):
            FactLookupModule()(REQUEST_ID, "   ", [passage("a", "x", 0.9)])

    def test_rejects_non_list_context(self) -> None:
        with pytest.raises(TypeError):
            FactLookupModule()(REQUEST_ID, "q", {"not": "a list"})  # type: ignore[arg-type]
