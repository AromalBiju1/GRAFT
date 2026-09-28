"""Tests for the contradiction-detection specialist module (§7.4).

These cover the offline heuristic (negation mismatch + keyword overlap). The
NLI model is issue #12; until then these tests pin the fallback's behaviour so
swapping in a model cannot silently regress it.
"""

from __future__ import annotations

import pytest

from graft.modules.contradiction_detection import ContradictionDetectionModule
from tests.modules_helpers import assert_common_output, passage

REQUEST_ID = "req_001"

AGREEING_A = "The system must encrypt all user data at rest using AES-256 encryption."
AGREEING_B = "All user data at rest is encrypted with AES-256 encryption in this system."
CONTRADICTING_B = "The system must not encrypt user data at rest; plaintext storage is permitted."


class TestContradictionDetection:
    def test_single_source_cannot_conflict(self) -> None:
        result = ContradictionDetectionModule()(
            REQUEST_ID, "is there a conflict?", [passage("a", "only source", 0.9)]
        )
        assert result.result["conflicts"] == []
        assert result.confidence <= 0.35
        assert result.metadata["reason"] == "insufficient_context"

    def test_matching_number_is_not_a_contradiction(self) -> None:
        # Same value, same polarity: agreement, not conflict.
        context = [
            passage("a", "The maximum segment size is 1460 bytes.", 0.9),
            passage("b", "This maximum segment size of 1460 bytes is the limit.", 0.9),
        ]
        result = ContradictionDetectionModule()(REQUEST_ID, "conflicts?", context)
        assert result.result["conflicts"] == []

    def test_negation_mismatch_is_flagged(self) -> None:
        context = [passage("a", AGREEING_A, 0.9), passage("b", CONTRADICTING_B, 0.9)]
        result = ContradictionDetectionModule()(REQUEST_ID, "conflicts?", context)
        assert len(result.result["conflicts"]) == 1
        assert result.confidence > 0.7

    def test_result_has_conflicts_list_and_summary(self) -> None:
        context = [passage("a", AGREEING_A, 0.9), passage("b", CONTRADICTING_B, 0.9)]
        result = ContradictionDetectionModule()(REQUEST_ID, "conflicts?", context).result
        assert isinstance(result["conflicts"], list)
        assert isinstance(result["summary"], str)
        assert result["summary"]

    def test_each_conflict_has_the_documented_fields(self) -> None:
        context = [passage("a", AGREEING_A, 0.9), passage("b", CONTRADICTING_B, 0.9)]
        conflict = ContradictionDetectionModule()(REQUEST_ID, "conflicts?", context).result[
            "conflicts"
        ][0]
        assert {"passage_a", "passage_b", "overlap", "signal"} <= set(conflict)
        assert conflict["passage_a"]["chunk_id"] == "a"
        assert conflict["passage_b"]["chunk_id"] == "b"
        assert 0.0 <= conflict["overlap"] <= 1.0
        assert conflict["signal"] == "negation_mismatch"

    def test_unrelated_passages_are_not_compared(self) -> None:
        context = [
            passage("a", "TCP congestion control uses slow start.", 0.9),
            passage("b", "The garden needs watering every morning.", 0.9),
        ]
        result = ContradictionDetectionModule()(REQUEST_ID, "conflicts?", context)
        assert result.result["conflicts"] == []

    def test_summary_reports_zero_conflicts(self) -> None:
        context = [passage("a", AGREEING_A, 0.9), passage("b", AGREEING_B, 0.9)]
        result = ContradictionDetectionModule()(REQUEST_ID, "conflicts?", context)
        assert result.result["conflicts"] == []
        assert "no contradictions" in result.result["summary"].lower()

    def test_metadata_names_the_heuristic(self) -> None:
        context = [passage("a", AGREEING_A, 0.9), passage("b", CONTRADICTING_B, 0.9)]
        result = ContradictionDetectionModule()(REQUEST_ID, "conflicts?", context)
        assert result.metadata["conflicts_found"] == 1
        assert "negation_mismatch" in result.metadata["heuristic"]

    def test_to_dict_matches_contract(self) -> None:
        context = [passage("a", AGREEING_A, 0.9), passage("b", CONTRADICTING_B, 0.9)]
        result = ContradictionDetectionModule()(REQUEST_ID, "conflicts?", context)
        assert_common_output(result, REQUEST_ID, "contradiction_detection")

    def test_rejects_invalid_request_id(self) -> None:
        with pytest.raises(ValueError):
            ContradictionDetectionModule()("", "q", [passage("a", "x", 0.9)])
