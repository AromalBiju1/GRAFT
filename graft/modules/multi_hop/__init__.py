"""Multi-hop reasoning — combines evidence across passages."""

from __future__ import annotations

from typing import Any

from graft.modules.base import BaseModule, ModuleResult


class MultiHopModule(BaseModule):
    name = "multi_hop"

    def run(self, request_id: str, query: str, context: list[dict[str, Any]]) -> ModuleResult:
        if len(context) < 2:
            return ModuleResult(
                request_id=request_id,
                module=self.name,
                result="Not enough context for multi-hop reasoning (need >=2 passages).",
                evidence=list(context),
                confidence=0.25,
                metadata={"reason": "insufficient_context", "passage_count": len(context)},
            )
        # Stub: concatenate top-3 passages as a joint chain.
        ranked = sorted(context, key=lambda c: c.get("score", 0.0), reverse=True)[:3]
        combined = "\n\n---\n\n".join(
            str(c.get("text", "")).strip() for c in ranked if c.get("text")
        )
        avg_score = sum(float(c.get("score", 0.5)) for c in ranked) / max(len(ranked), 1)
        return ModuleResult(
            request_id=request_id,
            module=self.name,
            result=combined[:2000] or "No answer stitched from multi-hop context.",
            evidence=ranked,
            confidence=max(0.0, min(1.0, avg_score * 0.9)),
            metadata={"hops_used": len(ranked)},
        )


__all__ = ["MultiHopModule"]
