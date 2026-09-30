"""Numeric/table reasoning — stub extractor + arithmetic.

Replace with a table-aware LLM pass when available; interface stays the same.
"""

from __future__ import annotations

import re
from typing import Any

from modules.base import BaseModule, ModuleResult

_NUMBER_RE = re.compile(r"-?\d[\d,]*\.?\d*")


def _extract_numbers(text: str) -> list[float]:
    vals: list[float] = []
    for m in _NUMBER_RE.findall(text):
        try:
            vals.append(float(m.replace(",", "")))
        except ValueError:
            continue
    return vals


class NumericReasoningModule(BaseModule):
    name = "numeric_reasoning"

    def run(self, request_id: str, query: str, context: list[dict[str, Any]]) -> ModuleResult:
        all_text = "\n".join(str(c.get("text", "")) for c in context)
        numbers = _extract_numbers(all_text)
        ql = query.lower()
        operation: str | None = None
        result_payload: dict[str, Any] = {"numbers_found": numbers[:20], "count": len(numbers)}

        if any(k in ql for k in ("sum", "total", "add")) and numbers:
            operation = "sum"
            result_payload["result"] = sum(numbers)
        elif any(k in ql for k in ("average", "mean")) and numbers:
            operation = "average"
            result_payload["result"] = sum(numbers) / len(numbers)
        elif any(k in ql for k in ("difference", "subtract", "compare")) and len(numbers) >= 2:
            operation = "difference"
            result_payload["result"] = numbers[0] - numbers[1]
            result_payload["operands"] = numbers[:2]
        elif numbers:
            operation = "extract"
            result_payload["result"] = numbers[:5]
        else:
            result_payload["result"] = None
            operation = "no_numbers"

        if operation in ("sum", "average", "difference"):
            confidence = 0.85
        else:
            confidence = 0.5 if numbers else 0.2
        return ModuleResult(
            request_id=request_id,
            module=self.name,
            result=result_payload,  # type: ignore[arg-type]
            evidence=context[:3],
            confidence=confidence,
            metadata={"operation": operation},
        )


__all__ = ["NumericReasoningModule"]
