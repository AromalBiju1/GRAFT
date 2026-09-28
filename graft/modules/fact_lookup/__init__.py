"""Fact lookup — direct factual QA over retrieved evidence."""

from __future__ import annotations

from typing import Any

from graft.modules.base import BaseModule, ModuleResult


class FactLookupModule(BaseModule):
    name = "fact_lookup"

    def run(self, request_id: str, query: str, context: list[dict[str, Any]]) -> ModuleResult:
        if not context:
            return ModuleResult(
                request_id=request_id,
                module=self.name,
                result="No context provided.",
                evidence=[],
                confidence=0.0,
                metadata={"reason": "empty_context"},
            )
        # Stub: pick highest-scoring chunk as answer with its text as evidence.
        # Replace with LLM synthesis when generation is wired.
        ranked = sorted(context, key=lambda c: c.get("score", 0.0), reverse=True)
        top = ranked[0]
        result_text = str(top.get("text", "")).strip()[:800]
        return ModuleResult(
            request_id=request_id,
            module=self.name,
            result=result_text or "No answer found in context.",
            evidence=[top],
            confidence=float(top.get("score", 0.5)),
            metadata={"retrieved_count": len(context)},
        )


__all__ = ["FactLookupModule"]
