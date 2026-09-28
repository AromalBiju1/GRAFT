"""Tree construction (RAPTOR-style).

Leaf nodes are the chunks from :mod:`graft.indexing.chunking`. Upper levels
are produced by :mod:`indexing.summarizer`'s ``RecursiveSummarizer`` (GMM
clustering + LLM summarisation, seeded and deterministic).

This module previously carried its own naive sequential-grouping tree builder
with a truncated-concatenation summariser. That created a split brain: the
``/index`` JSON branch and the ``/index`` multipart branch built structurally
different trees into the same Chroma collection. Both now delegate to the one
tested implementation in :mod:`indexing.builder`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from graft.tree_store import TreeNode

__all__ = ["build_tree_from_chunks", "synthesise_stub"]


def synthesise_stub(texts: list[str], max_chars: int = 600) -> str:
    """Deterministic offline summariser: join and truncate.

    Used as the default ``llm_client`` target so indexing runs without a
    network or an API key. Pass a real client to ``build_tree_from_chunks``
    via ``indexing.builder.build_tree_from_chunks(llm_client=...)``.
    """
    joined = "\n\n".join(texts)
    if len(joined) <= max_chars:
        return joined
    return joined[: max_chars - 3] + "..."


def _default_llm_client(prompt: str) -> str:
    """Offline LLM stand-in: echoes the passages it was handed."""
    passages = prompt.split("Passages:", 1)[-1]
    return synthesise_stub([passages.strip()])


def build_tree_from_chunks(
    chunks: Iterable[Mapping[str, Any]],
    *,
    cluster_size: int = 3,
    document_id: str = "doc_001",
    source: str | None = None,
    embedding_fn: Callable[[str], Any] | None = None,
    llm_client: Any | None = None,
) -> list[TreeNode]:
    """Build a RAPTOR tree from already-chunked documents.

    Delegates to :func:`indexing.builder.build_tree_from_chunks`, which owns
    clustering, summarisation, parent/child linking and embedding fallbacks.

    Args:
        chunks: chunk dicts from :func:`graft.indexing.chunking.chunk_text`.
        cluster_size: minimum group size; cluster count is ``n // cluster_size``.
        document_id: fallback document ID when a chunk omits one.
        source: provenance recorded on leaves that lack it.
        embedding_fn: ``text -> vector``; defaults to the offline hash stub.
        llm_client: summarisation backend; defaults to the offline stub.

    Returns:
        Flat list of every node across levels 0..root. The root is the single
        node with ``parent_id is None`` at the highest level.
    """
    from indexing.builder import build_tree_from_chunks as _build

    return _build(
        chunks,
        cluster_size=cluster_size,
        document_id=document_id,
        source=source,
        embedding_fn=embedding_fn,
        llm_client=llm_client or _default_llm_client,
    )
