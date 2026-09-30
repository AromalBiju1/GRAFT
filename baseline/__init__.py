"""Baseline — flat 'retrieve everything' RAG for honest comparison.

Contrasts with the gated GRAFT pipeline. Retrieval always scans at the
requested depth (no gating), all modules fire, and latency/modules-fired
are reported so the benchmark harness can quantify the trade-off.
"""

from __future__ import annotations

from typing import Any

from generation import synthesize
from modules.base import ModuleResult
from modules.contradiction_detection import ContradictionDetectionModule
from modules.fact_lookup import FactLookupModule
from modules.multi_hop import MultiHopModule
from modules.numeric_reasoning import NumericReasoningModule
from retrieval import retrieve


def run_baseline(
    request_id: str,
    query: str,
    query_embedding: list[float],
    *,
    n_results: int = 8,
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute the flat baseline end-to-end.

    Returns dict with keys: request_id, answer, evidence, module_results,
    retrieval (raw), latency metadata placeholder.
    """
    retrieved = retrieve(query_embedding, n_results=n_results, filters=filters)

    modules = [
        FactLookupModule(),
        MultiHopModule(),
        NumericReasoningModule(),
        ContradictionDetectionModule(),
    ]
    module_results: list[ModuleResult] = [m(request_id, query, retrieved) for m in modules]
    final = synthesize(request_id, query, retrieved, module_results)
    final["module_results"] = [r.to_dict() for r in module_results]
    final["retrieval"] = retrieved
    final["mode"] = "baseline_flat"
    return final


__all__ = ["run_baseline"]
