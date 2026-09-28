"""End-to-end indexing pipeline: text -> chunks -> tree -> vector store.

This is the "head" of GRAFT: the single place documents are turned into a
queryable tree. It is runnable without any external LLM call so indexing can
be smoke-tested in CI, and it shares the real embedder from
:mod:`graft.embeddings` with the query side so stored vectors and query
vectors can never disagree on dimensionality.

Both the tree builder and the persistence layer are the tested implementations
in :mod:`indexing.builder` / :mod:`indexing.store`; this module is the
orchestration, not a second copy of them.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path
from typing import Any

from graft.config import settings
from graft.embeddings import collection_name as default_collection_name
from graft.embeddings import embed_text
from graft.indexing.chunking import chunk_text
from graft.indexing.tree import build_tree_from_chunks
from graft.tree_store import TreeNode

__all__ = ["build_tree", "index_document", "main"]


def build_tree(
    text: str,
    *,
    document_id: str = "doc_001",
    source: str | None = None,
    chunk_size: int = 400,
    chunk_overlap: int = 50,
    cluster_size: int = 3,
    embedding_fn: Callable[[str], Any] | None = None,
    llm_client: Any | None = None,
) -> list[TreeNode]:
    """Build a document tree from raw text, without touching the vector store."""
    chunks = chunk_text(
        text,
        chunk_size=chunk_size,
        overlap=chunk_overlap,
        document_id=document_id,
        source=source,
    )
    return build_tree_from_chunks(
        chunks,
        cluster_size=cluster_size,
        document_id=document_id,
        source=source,
        embedding_fn=embedding_fn or embed_text,
        llm_client=llm_client,
    )


def index_document(
    text: str,
    *,
    document_id: str,
    source: str | None = None,
    persist_path: Path | str | None = None,
    collection_name: str | None = None,
    embedding_fn: Callable[[str], Any] | None = None,
    llm_client: Any | None = None,
) -> list[TreeNode]:
    """Chunk, build the tree, embed nodes, and persist to Chroma.

    Uses the same embedder as the query side (:func:`graft.embeddings.embed_text`)
    so indexed and query vectors share a dimension.

    Args:
        text: raw document text.
        document_id: unique source document identifier.
        source: provenance recorded in node metadata.
        persist_path: Chroma location; defaults to ``settings.chroma_path``.
        collection_name: Chroma collection; defaults to ``settings.chroma_collection``.
        embedding_fn: override the embedder (tests, custom models).
        llm_client: override the summariser.

    Returns:
        The created tree nodes, leaves at level 0 and a single root on top.
    """
    nodes = build_tree(
        text,
        document_id=document_id,
        source=source,
        embedding_fn=embedding_fn,
        llm_client=llm_client,
    )
    persist_tree_nodes(
        nodes,
        persist_path=persist_path,
        collection_name=collection_name,
    )
    return nodes


def persist_tree_nodes(
    nodes: list[TreeNode],
    *,
    persist_path: Path | str | None = None,
    collection_name: str | None = None,
) -> None:
    """Upsert *nodes* into the configured Chroma collection.

    Raises:
        RuntimeError: if chromadb / the vector store is unavailable. Indexing
            that silently succeeds without persisting would produce a tree that
            retrieval can never see, so this fails loudly instead.
    """
    from indexing.store import persist_tree_nodes as _persist
    from vector_store import ChromaVectorStore

    store = ChromaVectorStore(
        persist_path=Path(persist_path) if persist_path else settings.chroma_path,
        collection_name=collection_name or default_collection_name(),
    )
    try:
        _persist(nodes, store)
    finally:
        try:
            store.close()
        except Exception as exc:  # pragma: no cover - close is best effort
            import logging

            logging.getLogger(__name__).warning("Chroma close() failed: %s", exc)


def main() -> None:  # pragma: no cover
    parser = argparse.ArgumentParser(description="GRAFT indexing pipeline")
    parser.add_argument("input", help="Path to a text/PDF file or '-' for stdin")
    parser.add_argument("--document-id", default="doc_001")
    parser.add_argument("--source", default=None)
    args = parser.parse_args()

    if args.input == "-":
        import sys

        text = sys.stdin.read()
    else:
        text = Path(args.input).read_text(encoding="utf-8", errors="ignore")

    nodes = index_document(text, document_id=args.document_id, source=args.source)
    roots = [n for n in nodes if n.parent_id is None and n.level > 0]
    print(f"Indexed {len(nodes)} nodes for document {args.document_id}")
    leaf_count = sum(1 for n in nodes if n.level == 0)
    print(f"  leaves: {leaf_count}  root: {roots[0].node_id if roots else 'none'}")
    for n in nodes[:5]:
        print(f"  L{n.level} {n.node_id}: {n.text[:80]!r}")
