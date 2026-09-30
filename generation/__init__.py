"""Generation — synthesizes the final answer from query + retrieved context + module outputs.

Contract: docs/interfaces.md sections 8–9.
Stub concatenates module results deterministically so the pipeline is
testable without an LLM. Swap in a real LLM call behind the same function.
"""

from __future__ import annotations

from typing import Any

from modules.base import ModuleResult


def synthesize(
    request_id: str,
    query: str,
    context: list[dict[str, Any]],
    module_results: list[ModuleResult | dict[str, Any]],
) -> dict[str, Any]:
    """Build a final response dict compatible with docs/interfaces.md section 9.

    Returns {"request_id": ..., "answer": ..., "evidence": [...]}.
    """
    if not request_id or not str(request_id).strip():
        raise ValueError("request_id must be a non-empty string")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")

    # Normalise module_results to dicts
    normalised: list[dict[str, Any]] = []
    for r in module_results:
        if isinstance(r, ModuleResult):
            normalised.append(r.to_dict())
        elif isinstance(r, dict):
            normalised.append(dict(r))
        else:
            raise TypeError("module_results must contain ModuleResult or dict")

    # Evidence is union of retrieval context + module evidence, deduped by chunk_id
    evidence_map: dict[str, dict[str, Any]] = {}
    for c in context:
        cid = str(c.get("chunk_id") or c.get("node_id") or f"ctx_{len(evidence_map)}")
        evidence_map.setdefault(cid, c)
    for mr in normalised:
        for ev in mr.get("evidence") or []:
            if isinstance(ev, dict):
                cid = str(ev.get("chunk_id") or ev.get("node_id") or f"ev_{len(evidence_map)}")
                evidence_map.setdefault(cid, ev)

    evidence = list(evidence_map.values())

    # Stub answer: prioritise contradiction summary if present, else fact_lookup result,
    # else first context passage.
    answer: str
    for mr in normalised:
        if mr.get("module") == "contradiction_detection":
            result = mr.get("result")
            conflicts = result.get("conflicts") if isinstance(result, dict) else None
            if conflicts:
                answer = str((mr.get("result") or {}).get("summary") or mr.get("result"))
                break
    else:
        answer = ""

    if not answer:
        for mr in normalised:
            if mr.get("module") == "fact_lookup":
                answer = str(mr.get("result") or "")
                break

    if not answer:
        if normalised and isinstance(normalised[0].get("result"), str):
            answer = str(normalised[0]["result"])
        elif context:
            answer = str(context[0].get("text", ""))[:1200]
        else:
            answer = "No answer could be generated from the available context."

    return {"request_id": request_id, "answer": answer, "evidence": evidence}


__all__ = ["synthesize"]
