"""Tests for the multi-hop reasoning specialist module (§7.2)."""

from __future__ import annotations

import pytest

from graft.modules.multi_hop import MultiHopModule
from tests.modules_helpers import assert_common_output, passage

REQUEST_ID = "req_001"


class TestMultiHop:
    def test_single_passage_is_low_confidence(self) -> None:
        result = MultiHopModule()(REQUEST_ID, "compare", [passage("a", "only one", 0.95)])
        assert result.confidence <= 0.3
        assert result.metadata["reason"] == "insufficient_context"
        assert result.metadata["passage_count"] == 1

    def test_zero_passages_is_insufficient_context(self) -> None:
        result = MultiHopModule()(REQUEST_ID, "compare", [])
        assert result.metadata["reason"] == "insufficient_context"
        assert result.confidence <= 0.3

    def test_chains_top_three_passages_with_separator(self) -> None:
        context = [
            passage("a", "first passage", 0.9),
            passage("b", "second passage", 0.8),
            passage("c", "third passage", 0.7),
            passage("d", "fourth passage", 0.1),
        ]
        result = MultiHopModule()(REQUEST_ID, "compare", context)
        assert result.result.count("---") == 2
        assert "first passage" in result.result
        assert "second passage" in result.result
        assert "third passage" in result.result
        # The lowest-scoring passage is not part of the chain.
        assert "fourth passage" not in result.result

    def test_confidence_is_average_score_times_point_nine(self) -> None:
        context = [passage("a", "x", 1.0), passage("b", "y", 0.5)]
        result = MultiHopModule()(REQUEST_ID, "compare", context)
        assert result.confidence == pytest.approx((1.0 + 0.5) / 2 * 0.9)

    def test_hops_used_matches_passage_count(self) -> None:
        two = MultiHopModule()(REQUEST_ID, "c", [passage("a", "x", 0.9), passage("b", "y", 0.8)])
        four = MultiHopModule()(
            REQUEST_ID,
            "c",
            [
                passage("a", "x", 0.9),
                passage("b", "y", 0.8),
                passage("c", "z", 0.7),
                passage("d", "w", 0.6),
            ],
        )
        assert two.metadata["hops_used"] == 2
        # Capped at the top-3 chain.
        assert four.metadata["hops_used"] == 3

    def test_to_dict_matches_contract(self) -> None:
        result = MultiHopModule()(REQUEST_ID, "c", [passage("a", "x", 0.9), passage("b", "y", 0.8)])
        assert_common_output(result, REQUEST_ID, "multi_hop")

    def test_rejects_invalid_request_id(self) -> None:
        with pytest.raises(ValueError):
            MultiHopModule()("  ", "c", [passage("a", "x", 0.9), passage("b", "y", 0.8)])
