"""Chunking helper for the indexing pipeline.

Thin wrapper around :mod:`graft.indexing.chunking` so the import path
``indexing/chunker.py`` exists as described in the issue. If graft chunking
is unavailable, falls back to an equivalent local implementation.

Contract (docs/interfaces.md section 2):
    Input: document_id, text, optional source
    Output: list of dicts with chunk_id, document_id, text, metadata
"""

from __future__ import annotations

try:
    from graft.indexing.chunking import chunk_text as _graft_chunk_text  # type: ignore
except Exception:  # pragma: no cover
    _graft_chunk_text = None  # type: ignore


def chunk_text(
    text: str,
    *,
    chunk_size: int = 800,
    overlap: int = 100,
    document_id: str = "doc_001",
    source: str | None = None,
) -> list[dict]:
    """Split *text* into overlapping chunks.

    Returns a list of dicts compatible with the Vector Store contract
    (chunk_id, document_id, text, metadata) without embeddings.
    Delegates to graft.indexing.chunking.chunk_text when available.
    """
    if _graft_chunk_text is not None:
        return _graft_chunk_text(
            text, chunk_size=chunk_size, overlap=overlap, document_id=document_id, source=source
        )

    # Fallback local implementation (mirrors graft.indexing.chunking)
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text must be a non-empty string")
    if chunk_size < 1 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("chunk_size must be >=1 and 0 <= overlap < chunk_size")
    if not document_id or not str(document_id).strip():
        raise ValueError("document_id must be a non-empty string")

    words = text.split()
    if not words:
        return []

    chunks: list[dict] = []
    step = chunk_size - overlap
    idx = 0
    chunk_index = 0
    while idx < len(words):
        window = words[idx : idx + chunk_size]
        chunk_text_value = " ".join(window)
        chunk_id = f"{document_id}_chunk_{chunk_index:04d}"
        metadata: dict = {"source": source} if source else {}
        metadata.update({"chunk_index": chunk_index, "word_count": len(window)})
        chunks.append(
            {
                "chunk_id": chunk_id,
                "document_id": document_id,
                "text": chunk_text_value,
                "metadata": metadata,
            }
        )
        chunk_index += 1
        idx += step
        if idx == 0:
            break
    return chunks


__all__ = ["chunk_text"]
