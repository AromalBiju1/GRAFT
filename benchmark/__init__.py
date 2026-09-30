"""Benchmark harness — evaluation metrics and ablation helpers.

Keeps metric definitions explicit so changes to accuracy/latency/modules-fired
calculations are reviewable per docs/interfaces and copilot-instructions.
"""

from __future__ import annotations

import time
from typing import Any


def measure_latency(fn, *args, **kwargs) -> tuple[Any, float]:
    """Run *fn* and return (result, latency_ms)."""
    start = time.perf_counter()
    result = fn(*args, **kwargs)
    latency_ms = (time.perf_counter() - start) * 1000.0
    return result, latency_ms


def modules_fired_count(module_results: list[dict[str, Any]] | list[Any]) -> int:
    return len(module_results or [])


def retrieval_precision_at_k(
    retrieved: list[dict[str, Any]], relevant_ids: set[str], k: int = 5
) -> float:
    """Precision@k for retrieved chunk_ids against a relevant set."""
    if k < 1:
        raise ValueError("k must be >=1")
    if not retrieved:
        return 0.0
    top_k = retrieved[:k]
    hits = sum(1 for r in top_k if str(r.get("chunk_id")) in relevant_ids)
    return hits / min(k, len(top_k))


__all__ = ["measure_latency", "modules_fired_count", "retrieval_precision_at_k"]
