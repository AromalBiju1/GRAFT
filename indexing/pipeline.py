"""End-to-end indexing pipeline: text -> chunks -> tree -> vector store.

This is the "head" of GRAFT: the single place documents are turned into a
queryable tree. It is runnable without any external LLM call so indexing can
be smoke-tested in CI, and it shares the real embedder from :mod:`embeddings`
with the query side so stored vectors and query vectors can never disagree on
dimensionality.

The tree construction and persistence are the tested implementations in
:mod:`indexing.builder` and :mod:`indexing.store`; this module is the
orchestration that binds them to the configured embedder and collection, not a
second copy of them.
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from config import settings
from embeddings import collection_name as default_collection_name
from embeddings import embed_text
from indexing.builder import build_tree
from indexing.config import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_MIN_CLUSTER_SIZE,
)
from indexing.tree_node import TreeNode

__all__ = ["index_document", "main", "persist_tree_nodes"]

logger = logging.getLogger(__name__)


def index_document(
    text: str,
    *,
    document_id: str,
    source: str | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    cluster_size: int = DEFAULT_MIN_CLUSTER_SIZE,
    persist_path: Path | str | None = None,
    collection_name: str | None = None,
    embedding_fn: Callable[[str], Any] | None = None,
    llm_client: Any | None = None,
) -> list[TreeNode]:
    """Chunk, build the tree, embed nodes, and persist to Chroma.

    Uses the same embedder as the query side (:func:`embeddings.embed_text`)
    so indexed and query vectors share a dimension.

    Args:
        text: raw document text.
        document_id: unique source document identifier.
        source: provenance recorded in node metadata.
        chunk_size / chunk_overlap: ``cl100k_base`` **tokens**, not words.
        cluster_size: minimum group size for tree clustering.
        persist_path: Chroma location; defaults to ``settings.chroma_path``.
        collection_name: Chroma collection; defaults to the embedder-qualified
            name from :func:`embeddings.collection_name`.
        embedding_fn: override the embedder (tests, custom models).
        llm_client: override the summariser.

    Returns:
        The created tree nodes, leaves at level 0 and a single root on top.
    """
    nodes = build_tree(
        text,
        document_id=document_id,
        source=source,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        cluster_size=cluster_size,
        embedding_fn=embedding_fn or embed_text,
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
            logger.warning("Chroma close() failed: %s: %s", type(exc).__name__, exc)


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
