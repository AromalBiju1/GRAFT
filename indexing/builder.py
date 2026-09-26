"""RAPTOR tree construction: ingest -> chunk -> embed -> cluster -> summarize.

Pipeline entry point for the indexing module. Wires together:

    indexing.ingest.parse_document   extract text from PDF/DOCX
    indexing.chunker.chunk_text      token-based chunking
    indexing.summarizer              RecursiveSummarizer (GMM/k-means + LLM)
    indexing.store.persist_tree_nodes upsert into ChromaVectorStore

Tree recursion is delegated to :class:`indexing.summarizer.RecursiveSummarizer`
rather than reimplemented here. Pass ``llm_client`` to use a real model; the
default is an offline deterministic stub so CI runs without network access.

Chunk input accepts both chunk schemas in circulation:

    # current indexing.chunker (flat keys, PR #27)
    {"chunk_id", "document_id", "text", "chunk_index", "token_count"}
    # legacy/vector-store shaped (nested metadata)
    {"chunk_id", "document_id", "text", "metadata": {...}}
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from indexing.chunker import chunk_text
from indexing.config import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_CLUSTER_METHOD,
    DEFAULT_MAX_SUMMARY_TOKENS,
    DEFAULT_MIN_CLUSTER_SIZE,
    DEFAULT_RANDOM_STATE,
    STUB_EMBEDDING_DIM,
    STUB_SUMMARY_MAX_CHARS,
)
from indexing.store import persist_tree_nodes
from indexing.summarizer import RecursiveSummarizer
from indexing.tree_node import TreeNode
from indexing.vector_store import ChromaVectorStore
from indexing.vector_store.chroma_store import DEFAULT_COLLECTION_NAME, DEFAULT_PERSIST_PATH

_LEAF_METADATA_KEYS = ("chunk_index", "token_count", "word_count", "source", "page")


def _stub_embedding(text: str, dim: int = STUB_EMBEDDING_DIM) -> list[float]:
    """Deterministic hash-based embedding stub (unit-test friendly)."""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    vals: list[float] = []
    for i in range(dim):
        byte = digest[i % len(digest)]
        vals.append((byte / 127.5) - 1.0)
    norm = sum(v * v for v in vals) ** 0.5 or 1.0
    return [v / norm for v in vals]


def _summarise_stub(texts: Sequence[str], max_chars: int = STUB_SUMMARY_MAX_CHARS) -> str:
    """Deterministic offline summariser: join and truncate.

    Used as the default ``llm_client`` so the pipeline is runnable in CI.
    """
    joined = "\n\n".join(texts)
    if len(joined) <= max_chars:
        return joined
    return joined[: max_chars - 3] + "..."


def _stub_llm_client(prompt: str) -> str:
    """Offline ``llm_client``: summarise the passages inside a prompt.

    Extracts the ``Passages:`` block rendered by
    ``indexing.prompts.SUMMARIZATION_PROMPT`` and truncates it, so the stub
    exercises the same prompt path as a real LLM without network access.
    """
    passages = prompt.split("Passages:\n", 1)[-1].split("\n\nCohesive Summary:", 1)[0]
    return _summarise_stub([passages])


def _chunk_metadata(chunk: Mapping[str, Any], document_id: str, source: str | None) -> dict[str, Any]:
    """Normalise either chunk schema into leaf ``TreeNode.metadata``.

    Keeps ``source`` / ``chunk_index`` / ``token_count`` (and legacy
    ``word_count`` / ``page``) so leaf provenance survives chunking — these
    back the ``metadata.source`` / ``metadata.page`` fields in
    ``docs/interfaces.md`` sections 3 and 5.
    """
    metadata: dict[str, Any] = {}
    nested = chunk.get("metadata")
    if isinstance(nested, Mapping):
        metadata.update(nested)
    for key in _LEAF_METADATA_KEYS:
        value = chunk.get(key)
        if value is not None and key not in metadata:
            metadata[key] = value
    if source and not metadata.get("source"):
        metadata["source"] = source
    metadata["document_id"] = str(chunk.get("document_id") or metadata.get("document_id") or document_id)
    return metadata


def build_tree_from_chunks(
    chunks: Iterable[Mapping[str, Any]],
    *,
    cluster_size: int = DEFAULT_MIN_CLUSTER_SIZE,
    document_id: str = "doc_001",
    source: str | None = None,
    embedding_fn: Callable[[str], Sequence[float]] | None = None,
    llm_client: Any | None = None,
    cluster_method: str = DEFAULT_CLUSTER_METHOD,
    random_state: int = DEFAULT_RANDOM_STATE,
    max_summary_tokens: int = DEFAULT_MAX_SUMMARY_TOKENS,
) -> list[TreeNode]:
    """Build a RAPTOR tree from already-chunked documents.

    Args:
        chunks: dicts from :func:`indexing.chunker.chunk_text` (flat keys) or
            legacy nested-``metadata`` dicts.
        cluster_size: minimum group size, forwarded as the summarizer's
            ``min_cluster_size`` (cluster count = len(layer) // cluster_size).
        document_id: fallback document ID when a chunk omits one.
        source: provenance recorded on leaves when the chunk lacks it.
        embedding_fn: ``text -> vector``; defaults to the offline stub.
        llm_client: summarization backend; defaults to the offline stub.
        cluster_method: ``gmm`` | ``kmeans`` | ``umap_gmm``.
        random_state: clustering seed for reproducible trees.
        max_summary_tokens: per-summary truncation budget.

    Returns:
        Flat list of every node across layers 0..root. Each leaf keeps its
        ``embedding``; summary nodes inherit the mean child embedding (or the
        stub embedding when child vectors are unusable). The last node is the
        single root with ``parent_id is None``.
    """
    chunks = list(chunks)
    if not chunks:
        return []
    if cluster_size < 2:
        raise ValueError("cluster_size must be >=2")

    embed: Callable[[str], Sequence[float]] = embedding_fn or _stub_embedding

    leaves: list[TreeNode] = []
    for position, chunk in enumerate(chunks):
        text = str(chunk.get("text") or "")
        node_id = str(
            chunk.get("node_id") or chunk.get("chunk_id") or f"{document_id}_chunk_{position:04d}"
        )
        doc_id = str(chunk.get("document_id") or document_id)
        leaves.append(
            TreeNode(
                node_id=node_id,
                text=text,
                level=0,
                embedding=list(embed(text)),
                parent_id=None,
                child_ids=[],
                metadata=_chunk_metadata(chunk, doc_id, source),
            )
        )

    summarizer = RecursiveSummarizer(
        llm_client or _stub_llm_client,
        max_summary_tokens=max_summary_tokens,
        min_cluster_size=cluster_size,
        cluster_method=cluster_method,
        random_state=random_state,
        # Namespace summary IDs per document: build_tree_from_chunks is called
        # once per document, so a shared counter would collide across documents
        # and silently break parent links.
        node_id_prefix=f"{document_id}_summary",
    )
    nodes = summarizer.build_tree_layers(leaves)

    # A single-chunk document has no layer to summarise, so the tree would
    # have no root and retrieval depth >= 1 would return nothing. Summarise
    # the lone leaf so every document yields a root node.
    if len(leaves) == 1:
        nodes = [leaves[0], summarizer.summarize_cluster(leaves, level=1)]

    # persist_tree_nodes rejects nodes without an embedding, so fill any gap
    # the summarizer could not mean-pool.
    for node in nodes:
        if node.embedding is None:
            node.embedding = list(embed(node.text))

    return _dedupe(nodes)


def _dedupe(nodes: Sequence[TreeNode]) -> list[TreeNode]:
    """Drop duplicate node IDs, preserving first-seen order."""
    seen: set[str] = set()
    unique: list[TreeNode] = []
    for node in nodes:
        if node.node_id not in seen:
            seen.add(node.node_id)
            unique.append(node)
    return unique


def build_tree(
    text: str,
    *,
    document_id: str = "doc_001",
    source: str | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    cluster_size: int = DEFAULT_MIN_CLUSTER_SIZE,
    embedding_fn: Callable[[str], Sequence[float]] | None = None,
    llm_client: Any | None = None,
    cluster_method: str = DEFAULT_CLUSTER_METHOD,
    random_state: int = DEFAULT_RANDOM_STATE,
) -> list[TreeNode]:
    """Chunk *text* then build its RAPTOR tree, without touching the store.

    ``chunk_size`` / ``chunk_overlap`` are ``cl100k_base`` tokens, matching
    :func:`indexing.chunker.chunk_text`.
    """
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
        embedding_fn=embedding_fn,
        llm_client=llm_client,
        cluster_method=cluster_method,
        random_state=random_state,
    )


def build_documents(
    texts: Sequence[tuple[str, str, str | None]],
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    cluster_size: int = DEFAULT_MIN_CLUSTER_SIZE,
    embedding_fn: Callable[[str], Sequence[float]] | None = None,
    llm_client: Any | None = None,
) -> list[TreeNode]:
    """Build RAPTOR trees for several documents without persisting them.

    Args:
        texts: ``(document_id, text, source)`` triples.

    Returns:
        Combined flat node list across all documents.
    """
    nodes: list[TreeNode] = []
    for document_id, text, source in texts:
        nodes.extend(
            build_tree(
                text,
                document_id=document_id,
                source=source,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                cluster_size=cluster_size,
                embedding_fn=embedding_fn,
                llm_client=llm_client,
            )
        )
    return nodes


def index_documents(
    texts: Sequence[tuple[str, str, str | None]],
    *,
    persist_path: Path | str | None = None,
    collection_name: str | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    cluster_size: int = DEFAULT_MIN_CLUSTER_SIZE,
    embedding_fn: Callable[[str], Sequence[float]] | None = None,
    llm_client: Any | None = None,
) -> list[TreeNode]:
    """Build trees for several documents and upsert them into ChromaDB.

    Args:
        texts: ``(document_id, text, source)`` triples.
        persist_path: defaults to ``ChromaVectorStore``'s
            ``DEFAULT_PERSIST_PATH`` (``.graft/chroma``).
        collection_name: defaults to ``DEFAULT_COLLECTION_NAME``.

    Returns:
        The combined flat node list (leaves + summaries + roots).
    """
    nodes = build_documents(
        texts,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        cluster_size=cluster_size,
        embedding_fn=embedding_fn,
        llm_client=llm_client,
    )
    if not nodes:
        return []

    store = ChromaVectorStore(
        persist_path=Path(persist_path) if persist_path else DEFAULT_PERSIST_PATH,
        collection_name=collection_name or DEFAULT_COLLECTION_NAME,
    )
    try:
        persist_tree_nodes(nodes, store)
    finally:
        store.close()
    return nodes


__all__ = [
    "build_tree",
    "build_tree_from_chunks",
    "build_documents",
    "index_documents",
    "_stub_embedding",
    "_stub_llm_client",
    "_summarise_stub",
]
