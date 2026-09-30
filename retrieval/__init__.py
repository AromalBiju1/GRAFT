"""Retrieval — walks the tree at the depth the router selects.

Contract: docs/interfaces.md sections 4-5 (Router <-> Retrieval).
Uses :class:`vector_store.ChromaVectorStore` for ANN search then filters by
level to honour the requested depth.

Depth semantics: ``retrieval_depth`` is the *deepest* level to consider, not
the only level. Retrieving at a single exact level silently returns nothing
whenever the requested level is absent from a given document's tree -- a
one-chunk document has only levels 0 and 1, so a depth-2 query found zero
results and answered "No context provided." This module therefore walks from
the leaves up to the requested depth and merges the hits, keeping the deeper
(summarised) nodes ahead of the shallower ones when scores tie.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from config import settings
from embeddings import collection_name as default_collection_name
from vector_store import ChromaVectorStore

logger = logging.getLogger(__name__)

#: Hard ceiling on tree depth, matching the 0..2 contract in the router.
MAX_RETRIEVAL_DEPTH = 2


def _where(filters: Mapping[str, Any] | None, level: int | None) -> dict[str, Any] | None:
    """Build a valid Chroma ``where`` clause for *filters* plus a level.

    Chroma accepts only a single top-level operator in ``where``, so merging
    ``{"document_id": "doc_001"}`` with ``{"level": 1}`` into one flat dict
    raises ``ValueError: Expected where to have exactly one operator``. Multiple
    equality conditions must be combined with ``$and``.
    """
    conditions: list[dict[str, Any]] = []
    for key, value in (filters or {}).items():
        conditions.append({key: value} if not isinstance(value, Mapping) else {key: dict(value)})
    if level is not None:
        conditions.append({"level": level})
    if not conditions:
        return None
    if len(conditions) == 1:
        return conditions[0]
    return {"$and": conditions}


def retrieve(
    query_embedding: list[float],
    *,
    n_results: int = 5,
    retrieval_depth: int | None = None,
    filters: dict[str, Any] | None = None,
    persist_path: Path | str | None = None,
    collection_name: str | None = None,
) -> list[dict[str, Any]]:
    """Retrieve nearest nodes at or above *retrieval_depth*.

    Args:
        query_embedding: embedding for the user query. Must match the dimension
            the index was built with; :mod:`embeddings` guarantees this.
        n_results: top-k.
        retrieval_depth: deepest tree level to include. ``None`` searches all
            levels. Values above :data:`MAX_RETRIEVAL_DEPTH` are clamped.
        filters: additional equality filters (e.g. ``{"document_id": ...}``),
            combined with the level filter using Chroma's ``$and``.
        persist_path / collection_name: override defaults from settings.

    Returns:
        Up to *n_results* dicts with keys ``chunk_id``, ``text``, ``distance``,
        ``score`` and ``metadata`` (docs/interfaces.md section 5), ordered by
        descending score.
    """
    if not query_embedding:
        raise ValueError("query_embedding must be a non-empty list of floats")
    if n_results < 1:
        raise ValueError("n_results must be >= 1")

    depth = (
        None if retrieval_depth is None else max(0, min(int(retrieval_depth), MAX_RETRIEVAL_DEPTH))
    )

    store = ChromaVectorStore(
        persist_path=Path(persist_path) if persist_path else settings.chroma_path,
        collection_name=collection_name or default_collection_name(),
    )
    try:
        if depth is None:
            return store.query(
                embedding=query_embedding, n_results=n_results, filters=_where(filters, None)
            )

        # Search the requested level first, then progressively shallower ones,
        # so a query still gets real leaf evidence when the document's tree has
        # no node at the requested depth. Level 0 is always the floor, which
        # keeps a simple query's retrieval genuinely shallow.
        seen: set[str] = set()
        merged: list[dict[str, Any]] = []
        for level in range(depth, -1, -1):
            hits = store.query(
                embedding=query_embedding, n_results=n_results, filters=_where(filters, level)
            )
            for hit in hits:
                cid = str(hit.get("chunk_id") or hit.get("node_id") or "")
                if cid in seen:
                    continue
                seen.add(cid)
                merged.append(hit)

        merged.sort(key=lambda h: float(h.get("score") or 0.0), reverse=True)
        return merged[:n_results]
    finally:
        try:
            store.close()
        except Exception as exc:  # pragma: no cover - close is best effort
            logger.warning("Chroma close() failed: %s: %s", type(exc).__name__, exc)


__all__ = ["MAX_RETRIEVAL_DEPTH", "retrieve"]
