"""Contradiction detection — compares claims across retrieved sources.

Stub uses surface heuristics (negation + keyword overlap). Replace with an
NLI / entailment model (e.g. deberta-v3-mnli) behind the same interface.
Output matches docs/interfaces.md section 7.4 plus structured conflict list.
"""

from __future__ import annotations

import re
from typing import Any

from graft.modules.base import BaseModule, ModuleResult

_NEGATION_RE = re.compile(
    r"\b(no[nt]?|never|not|without|banned|prohibited|deprecated|obsolete|must not|cannot)\b", re.I
)


def _has_negation(text: str) -> bool:
    return bool(_NEGATION_RE.search(text))


def _keyword_overlap(a: str, b: str) -> float:
    ta = set(re.findall(r"[a-z0-9]{3,}", a.lower()))
    tb = set(re.findall(r"[a-z0-9]{3,}", b.lower()))
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0


class ContradictionDetectionModule(BaseModule):
    name = "contradiction_detection"

    def run(self, request_id: str, query: str, context: list[dict[str, Any]]) -> ModuleResult:
        if len(context) < 2:
            return ModuleResult(
                request_id=request_id,
                module=self.name,
                result={
                    "conflicts": [],
                    "summary": "Need at least 2 passages to detect contradictions.",
                },
                evidence=list(context),
                confidence=0.3,
                metadata={"reason": "insufficient_context"},
            )

        conflicts: list[dict[str, Any]] = []
        # Pairwise scan for high overlap + mismatched negation (cheap proxy for NLI contradiction)
        for i in range(len(context)):
            for j in range(i + 1, len(context)):
                a = str(context[i].get("text", ""))
                b = str(context[j].get("text", ""))
                overlap = _keyword_overlap(a, b)
                if overlap < 0.25:
                    continue
                neg_a, neg_b = _has_negation(a), _has_negation(b)
                if neg_a != neg_b:
                    conflicts.append(
                        {
                            "passage_a": {"chunk_id": context[i].get("chunk_id"), "text": a[:400]},
                            "passage_b": {"chunk_id": context[j].get("chunk_id"), "text": b[:400]},
                            "overlap": round(overlap, 3),
                            "signal": "negation_mismatch",
                            "message": (
                                "Potential contradiction: similar topic but opposite "
                                "polarity (stub heuristic)."
                            ),
                        }
                    )

        summary = (
            f"Flagged {len(conflicts)} potential conflict(s) (heuristic, needs NLI verification)."
            if conflicts
            else "No contradictions flagged by stub heuristic."
        )
        confidence = 0.75 if conflicts else 0.55
        return ModuleResult(
            request_id=request_id,
            module=self.name,
            result={"conflicts": conflicts, "summary": summary},  # type: ignore[arg-type]
            evidence=list(context),
            confidence=confidence,
            metadata={
                "conflicts_found": len(conflicts),
                "heuristic": "negation_mismatch + keyword_overlap",
            },
        )


__all__ = ["ContradictionDetectionModule"]
