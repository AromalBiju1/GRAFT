"""Shared query execution pipeline for GRAFT."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from fastapi import HTTPException

from config import settings
from db.logger import init_db, log_query
from embeddings import embed_text
from generation import synthesize
from modules.contradiction_detection import ContradictionDetectionModule
from modules.fact_lookup import FactLookupModule
from modules.multi_hop import MultiHopModule
from modules.numeric_reasoning import NumericReasoningModule
from retrieval import retrieve
from router import route

logger = logging.getLogger(__name__)

MODULE_REGISTRY: dict[str, Any] = {
    "fact_lookup": FactLookupModule(),
    "multi_hop": MultiHopModule(),
    "numeric_reasoning": NumericReasoningModule(),
    "contradiction_detection": ContradictionDetectionModule(),
}


def _embed_query(query: str) -> list[float]:
    return embed_text(query)


def to_evidence_dicts(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for ev in evidence:
        meta = ev.get("metadata") or {}
        items.append({
            "source": meta.get("source") or ev.get("source"),
            "page": meta.get("page") or ev.get("page"),
            "chunk_id": ev.get("chunk_id") or meta.get("chunk_id"),
            "node_id": meta.get("node_id") or ev.get("node_id"),
            "level": meta.get("level") if isinstance(meta.get("level"), int) else ev.get("level"),
            "score": ev.get("score"),
            "text": (ev.get("text") or "")[:600] if ev.get("text") else None,
        })
    return items


def execute_query(
    query_text: str,
    request_id: str | None = None,
    retrieval_depth_override: int | None = None,
    filters: dict[str, Any] | None = None,
    llm_client: Any | None = None,
) -> dict[str, Any]:
    """Execute the full GRAFT pipeline: route -> retrieve -> modules -> synthesize -> log."""
    if not query_text or not query_text.strip():
        raise HTTPException(status_code=422, detail="query must not be empty")

    req_id = request_id or f"req_{uuid.uuid4().hex[:10]}"
    t0 = time.perf_counter()

    # 1. Route
    decision = route(query_text, retrieval_depth_override=retrieval_depth_override)

    # 2. Retrieve
    q_emb = _embed_query(query_text)
    try:
        retrieved = retrieve(
            q_emb,
            n_results=5,
            retrieval_depth=decision.retrieval_depth,
            filters=filters,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"retrieval failed: {exc}") from exc

    # 3. Specialist modules
    module_results = []
    for name in decision.activated_modules:
        mod = MODULE_REGISTRY.get(name)
        if mod is None:
            continue
        try:
            module_results.append(mod(req_id, query_text, retrieved))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"module {name} failed: {exc}") from exc

    # 4. Synthesize
    try:
        active_llm = llm_client
        if active_llm is None and getattr(settings, "llm_enabled", False):
            import generation
            try:
                active_llm = generation.create_llm_client()
            except Exception as exc:
                logger.warning("LLM client creation failed (%s: %s); using fallback.", type(exc).__name__, exc)
                active_llm = None
        final = synthesize(req_id, query_text, retrieved, module_results, llm_client=active_llm)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"generation failed: {exc}") from exc

    latency_ms = (time.perf_counter() - t0) * 1000.0

    # 5. Log
    try:
        init_db()
        log_query(
            query_text=query_text,
            system="graft",
            latency_ms=latency_ms,
            modules_fired=decision.activated_modules,
            retrieval_depth=str(decision.retrieval_depth),
        )
    except Exception as exc:
        logger.warning(
            "Query logging failed for request %s: %s: %s", req_id, type(exc).__name__, exc
        )

    evidence_raw = final.get("evidence") or retrieved
    return {
        "request_id": req_id,
        "answer": str(final.get("answer", "")),
        "evidence": to_evidence_dicts(evidence_raw),
        "routing": decision.to_dict(),
        "latency_ms": round(latency_ms, 2),
    }
