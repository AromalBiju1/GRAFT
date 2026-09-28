"""Tests for answer synthesis (docs/interfaces.md sections 8-9).

Generation is deterministic and must not invent facts, so these tests pin the
priority rules, evidence dedup, and input validation.
"""

from __future__ import annotations

import pytest

from graft.generation import synthesize
from graft.modules.base import ModuleResult
from tests.modules_helpers import passage

REQUEST_ID = "req_001"

CONFLICT_SUMMARY = "Flagged 1 potential conflict."
FACT_TEXT = "Paris is the capital of France."


def _fact_module() -> ModuleResult:
    return ModuleResult(
        request_id=REQUEST_ID,
        module="fact_lookup",
        result=FACT_TEXT,
        evidence=[passage("shared", "shared passage", 0.9)],
        confidence=0.9,
    )


def _contradiction_module(*, conflicts: bool = True) -> ModuleResult:
    return ModuleResult(
        request_id=REQUEST_ID,
        module="contradiction_detection",
        result={
            "conflicts": [{"overlap": 0.5}] if conflicts else [],
            "summary": CONFLICT_SUMMARY,
        },
        evidence=[passage("x", "passage x", 0.8), passage("y", "passage y", 0.8)],
        confidence=0.8,
    )


class TestEmptyInputs:
    def test_no_context_and_no_modules_reports_no_answer(self) -> None:
        out = synthesize(REQUEST_ID, "anything?", [], [])
        assert out["answer"] == "No answer could be generated from the available context."
        assert out["evidence"] == []

    def test_returns_the_documented_response_keys(self) -> None:
        out = synthesize(REQUEST_ID, "q", [passage("a", "text")], [])
        assert set(out) == {"request_id", "answer", "evidence"}
        assert out["request_id"] == REQUEST_ID


class TestAnswerPriority:
    def test_contradiction_summary_wins_over_fact_lookup(self) -> None:
        out = synthesize(
            REQUEST_ID,
            "conflicts?",
            [passage("x", "x")],
            [_fact_module(), _contradiction_module()],
        )
        assert out["answer"] == CONFLICT_SUMMARY

    def test_contradiction_without_conflicts_does_not_take_priority(self) -> None:
        out = synthesize(
            REQUEST_ID,
            "q",
            [passage("x", "x")],
            [_fact_module(), _contradiction_module(conflicts=False)],
        )
        assert out["answer"] == FACT_TEXT

    def test_fact_lookup_wins_when_no_contradiction(self) -> None:
        out = synthesize(REQUEST_ID, "q", [passage("a", "text")], [_fact_module()])
        assert out["answer"] == FACT_TEXT

    def test_falls_back_to_first_context_passage(self) -> None:
        out = synthesize(REQUEST_ID, "q", [passage("a", "context text")], [])
        assert out["answer"] == "context text"


class TestEvidence:
    def test_evidence_is_deduped_by_chunk_id(self) -> None:
        # "shared" appears in both the retrieval context and module evidence.
        out = synthesize(REQUEST_ID, "q", [passage("shared", "shared passage")], [_fact_module()])
        ids = [e["chunk_id"] for e in out["evidence"]]
        assert ids.count("shared") == 1

    def test_context_items_come_before_module_evidence(self) -> None:
        out = synthesize(REQUEST_ID, "q", [passage("from_context", "c")], [_fact_module()])
        ids = [e["chunk_id"] for e in out["evidence"]]
        assert ids[0] == "from_context"
        assert "shared" in ids[1:]

    def test_all_evidence_is_retained_when_distinct(self) -> None:
        out = synthesize(REQUEST_ID, "q", [passage("x", "x")], [_contradiction_module()])
        ids = {e["chunk_id"] for e in out["evidence"]}
        assert {"x", "y"} <= ids


class TestModuleResultHandling:
    def test_accepts_raw_dicts(self) -> None:
        out = synthesize(REQUEST_ID, "q", [], [{"module": "fact_lookup", "result": "from a dict"}])
        assert out["answer"] == "from a dict"

    def test_accepts_mixed_module_objects_and_dicts(self) -> None:
        out = synthesize(
            REQUEST_ID, "q", [], [_fact_module(), {"module": "other", "result": "dict result"}]
        )
        assert out["answer"] == FACT_TEXT

    def test_rejects_unsupported_module_result_type(self) -> None:
        with pytest.raises(TypeError):
            synthesize(REQUEST_ID, "q", [], ["a bare string"])  # type: ignore[list-item]


class TestValidation:
    @pytest.mark.parametrize("request_id", ["", "   ", None])
    def test_invalid_request_id_raises(self, request_id: str | None) -> None:
        with pytest.raises(ValueError):
            synthesize(request_id, "q", [], [])  # type: ignore[arg-type]

    @pytest.mark.parametrize("query", ["", "   ", None])
    def test_invalid_query_raises(self, query: str | None) -> None:
        with pytest.raises(ValueError):
            synthesize(REQUEST_ID, query, [], [])  # type: ignore[arg-type]
