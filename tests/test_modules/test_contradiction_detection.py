"""Tests for the contradiction-detection specialist module (§7.4).

Tests use fake clients and pipelines; no models or network are required.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import modules.contradiction_detection as detection
from modules.contradiction_detection import (
    ContradictionDetectionModule,
    NLIUnavailableError,
    TransformersNLIClient,
)
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


@pytest.fixture(autouse=True)
def offline_default(monkeypatch):
    client = Mock()
    client.predict.side_effect = NLIUnavailableError("offline test")
    monkeypatch.setattr(detection, "_default_nli_client", client)


@pytest.mark.parametrize("label,score,expected", [
    ("contradiction", 0.9, 1),
    (" CONTRADICTION ", 0.8, 1),
    ("contradiction", 0.7, 0),
    ("contradiction", 0.69, 0),
    ("entailment", 0.99, 0),
    ("neutral", 0.99, 0),
])
def test_nli_threshold_and_labels(label, score, expected):
    client = Mock()
    client.predict.return_value = {"label": label, "score": score}
    context = [passage("a", AGREEING_A, 0.9), passage("b", CONTRADICTING_B, 0.9)]
    result = ContradictionDetectionModule(client)(REQUEST_ID, "conflicts?", context)
    client.predict.assert_called_once_with(AGREEING_A, CONTRADICTING_B)
    assert set(result.result) == {"conflicts", "summary"}
    assert len(result.result["conflicts"]) == expected
    assert result.metadata["nli_pairs"] == 1
    assert "heuristic" not in result.metadata
    if expected:
        conflict = result.result["conflicts"][0]
        assert conflict["nli_label"] == "contradiction"
        assert conflict["nli_score"] == score
        assert conflict["passage_a"]["chunk_id"] == "a"
        assert conflict["signal"] == "nli_contradiction"


def test_nli_evaluates_matching_polarity_in_deterministic_order():
    client = Mock()
    client.predict.return_value = {"label": "contradiction", "score": 0.9}
    texts = ["The maximum segment size is 1460 bytes.",
             "The maximum segment size is 1500 bytes.",
             "The maximum segment size is 9000 bytes."]
    context = [passage(str(i), text, 0.9) for i, text in enumerate(texts)]
    result = ContradictionDetectionModule(client)(REQUEST_ID, "conflicts?", context)
    assert len(result.result["conflicts"]) == 3
    assert [call.args for call in client.predict.call_args_list] == [
        (texts[0], texts[1]), (texts[0], texts[2]), (texts[1], texts[2])]


def test_no_candidates_does_not_call_nli():
    client = Mock()
    context = [passage("a", "TCP congestion control uses slow start.", 0.9),
               passage("b", "The garden needs watering every morning.", 0.9)]
    ContradictionDetectionModule(client)(REQUEST_ID, "conflicts?", context)
    client.predict.assert_not_called()


@pytest.mark.parametrize("error", [NLIUnavailableError("offline"), ImportError("missing"),
                                  OSError("unavailable")])
def test_unavailable_client_preserves_fallback(error):
    client = Mock()
    client.predict.side_effect = error
    context = [passage("a", AGREEING_A, 0.9), passage("b", CONTRADICTING_B, 0.9)]
    result = ContradictionDetectionModule(client)(REQUEST_ID, "conflicts?", context)
    conflict = result.result["conflicts"][0]
    assert conflict["signal"] == "negation_mismatch"
    assert "nli_label" not in conflict
    assert result.metadata["fallback_pairs"] == 1


def test_programming_errors_are_not_hidden():
    client = Mock()
    client.predict.side_effect = TypeError("bug")
    with pytest.raises(TypeError, match="bug"):
        ContradictionDetectionModule(client)(REQUEST_ID, "conflicts?", [
            passage("a", AGREEING_A, 0.9), passage("b", CONTRADICTING_B, 0.9)])


@pytest.mark.parametrize("label,id2label,expected", [
    ("CONTRADICTION", {}, "contradiction"),
    ("Entailment", {}, "entailment"),
    ("NEUTRAL", {}, "neutral"),
    ("LABEL_2", {2: "CONTRADICTION"}, "contradiction"),
    ("label_1", {"1": "ENTAILMENT"}, "entailment"),
])
def test_pipeline_is_lazy_cached_and_uses_text_pairs(monkeypatch, label, id2label, expected):
    classifier = Mock(return_value=[{"label": label, "score": 0.92}])
    classifier.model.config.id2label = id2label
    factory = Mock(return_value=classifier)
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(pipeline=factory))
    client = TransformersNLIClient()
    factory.assert_not_called()
    assert client.predict("premise", "hypothesis") == {"label": expected, "score": 0.92}
    client.predict("other", "text")
    factory.assert_called_once_with("text-classification", model="microsoft/deberta-v3-mnli")
    assert classifier.call_args_list[0].args == ({"text": "premise", "text_pair": "hypothesis"},)
    assert classifier.call_args_list[0].kwargs == {"truncation": True}


@pytest.mark.parametrize("error", [ImportError("missing"), OSError("offline"),
                                  RuntimeError("backend"), ValueError("model unavailable")])
def test_initialization_failure_is_cached(monkeypatch, error):
    factory = Mock(side_effect=error)
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(pipeline=factory))
    client = TransformersNLIClient()
    for _ in range(2):
        with pytest.raises(NLIUnavailableError):
            client.predict("a", "b")
    factory.assert_called_once()


def test_transformers_import_failure(monkeypatch):
    monkeypatch.setitem(sys.modules, "transformers", None)
    with pytest.raises(NLIUnavailableError):
        TransformersNLIClient().predict("a", "b")


def test_inference_failure_uses_heuristic(monkeypatch):
    classifier = Mock(side_effect=RuntimeError("inference unavailable"))
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(
        pipeline=Mock(return_value=classifier)))
    result = ContradictionDetectionModule(TransformersNLIClient())(REQUEST_ID, "conflicts?", [
        passage("a", AGREEING_A, 0.9), passage("b", CONTRADICTING_B, 0.9)])
    assert result.result["conflicts"][0]["signal"] == "negation_mismatch"


def test_unknown_numeric_label_is_not_guessed():
    with pytest.raises(NLIUnavailableError, match="Unrecognized"):
        detection._normalize_label("LABEL_0", {0: "LABEL_0"})


@pytest.mark.parametrize("score", [float("nan"), float("inf"), -0.1, 1.1])
def test_invalid_scores_use_fallback(score):
    client = Mock()
    client.predict.return_value = {"label": "contradiction", "score": score}
    result = ContradictionDetectionModule(client)(REQUEST_ID, "conflicts?", [
        passage("a", AGREEING_A, 0.9), passage("b", CONTRADICTING_B, 0.9)])
    assert result.result["conflicts"][0]["signal"] == "negation_mismatch"



def test_full_text_is_used_but_evidence_stays_truncated():
    text = AGREEING_A * 10
    client = Mock()
    client.predict.return_value = {"label": "contradiction", "score": 0.9}
    result = ContradictionDetectionModule(client)(REQUEST_ID, "conflicts?", [
        passage("a", text, 0.9), passage("b", CONTRADICTING_B, 0.9)])
    client.predict.assert_called_once_with(text, CONTRADICTING_B)
    assert result.result["conflicts"][0]["passage_a"]["text"] == text[:400]


def test_mixed_nli_and_fallback_results():
    client = Mock()
    client.predict.side_effect = [
        {"label": "neutral", "score": 0.9},
        NLIUnavailableError("offline"),
        {"label": "contradiction", "score": 0.95},
    ]
    result = ContradictionDetectionModule(client)(REQUEST_ID, "conflicts?", [
        passage("a", AGREEING_A, 0.9),
        passage("b", AGREEING_A, 0.9),
        passage("c", CONTRADICTING_B, 0.9),
    ])
    assert [c["signal"] for c in result.result["conflicts"]] == [
        "negation_mismatch", "nli_contradiction"]
    assert result.metadata["nli_pairs"] == 2
    assert result.metadata["fallback_pairs"] == 1
    assert "NLI with heuristic fallback" in result.result["summary"]
