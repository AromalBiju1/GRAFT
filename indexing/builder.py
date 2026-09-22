"""RAPTOR tree construction with embeddings (indexing/builder.py).

Implements the Ingest → Chunk → Embed → Cluster → Summarize → Persist pipeline
described in issue #17. Uses deterministic stub embedding / summarisation so the
pipeline is runnable in CI without external LLM calls.

Embedding stub mirrors graft.indexing.pipeline._stub_embedding (hash-based,
L2-normalised).

Tree construction mirrors graft.indexing.tree.build_tree_from_chunks but
targets indexing.tree_node.TreeNode (which stores embeddings and uses
child_ids / parent_id).

Public helpers:
    - build_tree(text, document_id, source, chunk_size, chunk_overlap, cluster_size)
    - build_tree_from_chunks(chunks, cluster_size, document_id, embedding_fn)
    - index_documents(texts_with_ids, persist_path, collection_name)

Persist uses indexing.store.persist_tree_nodes via ChromaVectorStore.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from indexing.chunker import chunk_text
from indexing.store import persist_tree_nodes
from indexing.tree_node import TreeNode
from indexing.vector_store import ChromaVectorStore


def _stub_embedding(text: str, dim: int = 16) -> list[float]:
    """Deterministic hash-based embedding stub (unit-test friendly)."""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    vals: list[float] = []
    for i in range(dim):
        byte = digest[i % len(digest)]
        vals.append((byte / 127.5) - 1.0)
    norm = sum(v * v for v in vals) ** 0.5 or 1.0
    return [v / norm for v in vals]


def _summarise_stub(texts: list[str], max_chars: int = 600) -> str:
    """Deterministic stub summariser: join and truncate."""
    joined = "\n\n".join(texts)
    if len(joined) <= max_chars:
        return joined
    return joined[: max_chars - 3] + "..."


def build_tree_from_chunks(
    chunks: list[dict[str, Any]],
    *,
    cluster_size: int = 4,
    document_id: str = "doc_001",
    embedding_fn: Any | None = None,
) -> list[TreeNode]:
    """Build a hierarchy from already-chunked docs with embeddings.

    Args:
        chunks: list of dicts with at least chunk_id, text, metadata.
        cluster_size: how many children per parent in stub clustering.
        document_id: fallback document_id if chunk lacks it.
        embedding_fn: optional embedding callable; defaults to _stub_embedding.

    Returns:
        Flat list of TreeNodes where level 0 are leaves and highest level
        is root. Parent/children links populated and every node has embedding.
    """
    if not chunks:
        return []
    if cluster_size < 2:
        raise ValueError("cluster_size must be >=2")

    embed = embedding_fn or _stub_embedding

    # Level 0: leaves with embeddings
    level_nodes: list[TreeNode] = []
    for ch in chunks:
        node_id = ch.get("node_id") or ch.get("chunk_id") or f"node_{len(level_nodes):04d}"
        doc_id = ch.get("document_id") or document_id
        text = ch.get("text") or ""
        metadata = dict(ch.get("metadata") or {})
        # Ensure document_id in metadata as required by persist_tree_nodes
        metadata.setdefault("document_id", str(doc_id))
        if source := ch.get("metadata", {}).get("source"):
            metadata.setdefault("source", source)
        # Preserve chunk_index etc for traceability
        node = TreeNode(
            node_id=str(node_id),
            text=str(text),
            level=0,
            embedding=embed(str(text)),
            parent_id=None,
            child_ids=[],
            metadata=metadata,
        )
        level_nodes.append(node)

    all_nodes: list[TreeNode] = list(level_nodes)
    current_level = 0
    current_nodes = level_nodes

    while len(current_nodes) > 1:
        next_level: list[TreeNode] = []
        for i in range(0, len(current_nodes), cluster_size):
            group = current_nodes[i : i + cluster_size]
            parent_id = f"doc_{document_id}_L{current_level+1}_{len(next_level):04d}" if len(chunks) > 0 else f"node_L{current_level+1}_{len(next_level):04d}"
            # Use first child's document_id for parent (or fallback)
            doc_id = group[0].metadata.get("document_id", document_id)
            summary = _summarise_stub([n.text for n in group])
            parent = TreeNode(
                node_id=parent_id,
                text=summary,
                level=current_level + 1,
                embedding=embed(summary),
                parent_id=None,
                child_ids=[n.node_id for n in group],
                metadata={"document_id": str(doc_id), "child_count": len(group)},
            )
            for child in group:
                child.parent_id = parent_id
            next_level.append(parent)
            all_nodes.append(parent)
        current_nodes = next_level
        current_level += 1
        if current_level > 20:
            break

    # Deduplicate by node_id while preserving first-seen order
    seen: set[str] = set()
    deduped: list[TreeNode] = []
    for n in all_nodes:
        if n.node_id not in seen:
            seen.add(n.node_id)
            deduped.append(n)
    return deduped


def build_tree(
    text: str,
    *,
    document_id: str = "doc_001",
    source: str | None = None,
    chunk_size: int = 800,
    chunk_overlap: int = 100,
    cluster_size: int = 4,
    embedding_fn: Any | None = None,
) -> list[TreeNode]:
    """Build a document tree from raw text with embeddings, without persistence."""
    chunks = chunk_text(
        text, chunk_size=chunk_size, overlap=chunk_overlap, document_id=document_id, source=source
    )
    return build_tree_from_chunks(
        chunks, cluster_size=cluster_size, document_id=document_id, embedding_fn=embedding_fn
    )


def index_documents(
    texts: list[tuple[str, str, str | None]],
    *,
    persist_path: Path | str | None = None,
    collection_name: str | None = None,
    chunk_size: int = 200,
    chunk_overlap: int = 20,
    cluster_size: int = 4,
    embedding_fn: Any | None = None,
) -> list[TreeNode]:
    """Build trees for multiple documents and persist to Chroma.

    Args:
        texts: list of (document_id, text, source) tuples.
        persist_path / collection_name: override defaults.
        chunk_size, chunk_overlap, cluster_size: pipeline tunables.
        embedding_fn: optional embedding callable.

    Returns:
        Combined flat list of all TreeNodes across documents.
    """
    all_nodes: list[TreeNode] = []
    for document_id, text, source in texts:
        nodes = build_tree(
            text,
            document_id=document_id,
            source=source,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            cluster_size=cluster_size,
            embedding_fn=embedding_fn,
        )
        all_nodes.extend(nodes)

    if not all_nodes:
        return []

    # Persist
    store = ChromaVectorStore(
        persist_path=Path(persist_path) if persist_path else Path(".graft/chroma"),
        collection_name=collection_name or "graft_tree_nodes",
    )
    try:
        persist_tree_nodes(all_nodes, store)
    finally:
        try:
            store.close()
        except Exception:
            pass
    return all_nodes


__all__ = ["build_tree", "build_tree_from_chunks", "index_documents", "_stub_embedding", "_summarise_stub"]
