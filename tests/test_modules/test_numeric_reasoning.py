"""Tests for the numeric/table reasoning specialist module (§7.3)."""

from __future__ import annotations

import pytest

from modules.numeric_reasoning import NumericReasoningModule
from tests.modules_helpers import assert_common_output, passage

REQUEST_ID = "req_001"
NUMERIC_CONTEXT = [passage("a", "Revenue was 1200 in 2023 and 1500 in 2024.", 0.9)]


class TestNumericReasoning:
    def test_extracts_numbers_from_context(self) -> None:
        result = NumericReasoningModule()(REQUEST_ID, "extract the figures", NUMERIC_CONTEXT)
        assert 1200.0 in result.result["numbers_found"]
        assert 1500.0 in result.result["numbers_found"]
        assert result.result["count"] >= 2

    def test_sum_query_returns_sum(self) -> None:
        result = NumericReasoningModule()(REQUEST_ID, "what is the total", NUMERIC_CONTEXT)
        assert result.metadata["operation"] == "sum"
        assert result.result["result"] == sum(result.result["numbers_found"])

    def test_average_query_returns_mean(self) -> None:
        result = NumericReasoningModule()(REQUEST_ID, "what is the average", NUMERIC_CONTEXT)
        assert result.metadata["operation"] == "average"
        numbers = result.result["numbers_found"]
        assert result.result["result"] == pytest.approx(sum(numbers) / len(numbers))

    def test_difference_query_returns_subtraction(self) -> None:
        context = [passage("a", "Revenue was 1200 and costs were 900.", 0.9)]
        result = NumericReasoningModule()(REQUEST_ID, "what is the difference", context)
        assert result.metadata["operation"] == "difference"
        assert result.result["result"] == 1200.0 - 900.0
        assert result.result["operands"] == [1200.0, 900.0]

    def test_no_numbers_yields_no_numbers_operation(self) -> None:
        result = NumericReasoningModule()(
            REQUEST_ID, "what is the total", [passage("a", "no digits here", 0.9)]
        )
        assert result.metadata["operation"] == "no_numbers"
        assert result.confidence <= 0.2
        assert result.result["result"] is None

    def test_result_payload_has_the_documented_keys(self) -> None:
        result = NumericReasoningModule()(REQUEST_ID, "total", NUMERIC_CONTEXT).result
        assert {"numbers_found", "count", "result"} <= set(result)

    def test_computed_operations_have_high_confidence(self) -> None:
        for query in ("sum", "total", "average"):
            result = NumericReasoningModule()(REQUEST_ID, query, NUMERIC_CONTEXT)
            assert result.confidence >= 0.8, query

    def test_numbers_with_thousands_separators_are_parsed(self) -> None:
        context = [passage("a", "The total was 1,200 and the other was 1,500.", 0.9)]
        result = NumericReasoningModule()(REQUEST_ID, "sum", context)
        assert 1200.0 in result.result["numbers_found"]
        assert 1500.0 in result.result["numbers_found"]

    def test_sum_excludes_years_from_operands(self) -> None:
        result = NumericReasoningModule()(REQUEST_ID, "what is the total revenue", NUMERIC_CONTEXT)

        assert result.result["result"] == 2700.0
        assert result.result["numbers_found"] == [1200.0, 1500.0]
        assert result.metadata["excluded_years"] == 2

    def test_difference_uses_entities_in_query_order(self) -> None:
        context = [passage("a", "Costs were 900 and revenue was 1200.", 0.9)]
        result = NumericReasoningModule()(
            REQUEST_ID,
            "what is the difference between revenue and costs",
            context,
        )

        assert result.metadata["operation"] == "difference"
        assert result.result["result"] == 300.0
        assert result.result["operands"] == [1200.0, 900.0]

    def test_currency_and_magnitude_are_normalized(self) -> None:
        context = [
            passage("a", "Revenue was $1.2 million in 2023 and $300,000 in 2024.", 0.9)
        ]
        result = NumericReasoningModule()(REQUEST_ID, "what is the total revenue", context)

        assert result.result["result"] == 1_500_000.0
        assert result.result["unit"] == "$"
        assert result.result["formatted"] == "$1,500,000"

    def test_table_column_is_summed(self) -> None:
        table = "| Year | Revenue | Costs |\n| 2023 | 100 | 40 |\n| 2024 | 200 | 60 |"
        result = NumericReasoningModule()(
            REQUEST_ID,
            "what is the total revenue",
            [passage("table", table, 0.9)],
        )

        assert result.result["result"] == 300.0
        assert result.result["numbers_found"] == [100.0, 200.0]
        assert result.metadata["table_used"] is True

    def test_percentage_change_uses_two_revenue_values(self) -> None:
        context = [passage("a", "Revenue grew from 100 in 2023 to 125 in 2024.", 0.9)]
        result = NumericReasoningModule()(
            REQUEST_ID,
            "what is the percentage increase in revenue",
            context,
        )

        assert result.metadata["operation"] == "percent_change"
        assert result.result["result"] == pytest.approx(25.0)
        assert result.result["unit"] == "%"

    def test_evidence_only_contains_contributing_chunks(self) -> None:
        context = [
            passage("revenue", "Revenue was 1200.", 0.9),
            passage("filler", "The archive contains 999 unrelated records.", 0.8),
        ]
        result = NumericReasoningModule()(REQUEST_ID, "what is the total revenue", context)

        assert [item["chunk_id"] for item in result.evidence] == ["revenue"]

    def test_reference_numbers_are_not_aggregated(self) -> None:
        context = [passage("a", "Module 2 has 13 questions. Revenue was 1200.", 0.9)]
        result = NumericReasoningModule()(REQUEST_ID, "what is the total revenue", context)

        assert result.result["result"] == 1200.0
        assert result.result["numbers_found"] == [1200.0]

    def test_to_dict_matches_contract(self) -> None:
        result = NumericReasoningModule()(REQUEST_ID, "total", NUMERIC_CONTEXT)
        assert_common_output(result, REQUEST_ID, "numeric_reasoning")

    def test_rejects_invalid_query(self) -> None:
        with pytest.raises(ValueError):
            NumericReasoningModule()(REQUEST_ID, "", NUMERIC_CONTEXT)
