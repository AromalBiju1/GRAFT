"""Token-based, paragraph-aware document chunking."""

from __future__ import annotations

import hashlib
import re
from functools import lru_cache

import tiktoken

from indexing.parser import DocumentParsingError


@lru_cache(maxsize=1)
def _encoding() -> tiktoken.Encoding:
    return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    """Count ordinary text, including literal special-token strings."""
    return len(_encoding().encode(text, disallowed_special=()))


def _boundaries(text: str) -> tuple[list[int], set[int]]:
    """Return sentence ends and preferred paragraph ends as source offsets."""
    paragraphs = {match.end() for match in re.finditer(r"\n\s*\n\s*", text)}
    sentences = {
        match.end() for match in re.finditer(r'''[.!?]["'\u201d\u2019)]*\s+''', text)
    }
    return sorted(sentences | paragraphs | {len(text)}), paragraphs | {len(text)}


def chunk_text(
    text: str,
    *,
    chunk_size: int = 400,
    overlap: int = 50,
    document_id: str = "doc_001",
    source: str | None = None,
) -> list[dict]:
    """Return standard chunks, preserving source text and sentence boundaries.

    Sizes are cl100k_base tokens. Whole-sentence overlap approximates the
    requested size; it may shrink to allow new content. A sentence larger than
    chunk_size is emitted intact (the target is soft), ensuring progress without
    corrupting Unicode or losing text. ``source`` remains accepted for existing
    callers but is not part of the ingestion chunk schema.
    """
    if not isinstance(text, str) or not text.strip():
        raise DocumentParsingError("text must be a non-empty string")
    if chunk_size < 1 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("chunk_size must be >=1 and 0 <= overlap < chunk_size")
    if not isinstance(document_id, str) or not document_id.strip():
        raise ValueError("document_id must be a non-empty string")

    text = text.strip()
    ends, paragraph_ends = _boundaries(text)
    chunks: list[dict] = []
    start = 0
    next_unit = 0
    while next_unit < len(ends):
        fitting: list[int] = []
        for unit in range(next_unit, len(ends)):
            if count_tokens(text[start:ends[unit]].strip()) > chunk_size:
                break
            fitting.append(unit)
        if fitting:
            preferred = [unit for unit in fitting if ends[unit] in paragraph_ends]
            last = preferred[-1] if preferred else fitting[-1]
        else:
            # Drop overlap before emitting an oversized sentence intact.
            start = ends[next_unit - 1] if next_unit else 0
            last = next_unit
        end = ends[last]
        value = text[start:end].strip()
        index = len(chunks)
        chunks.append({
            "chunk_id": hashlib.sha256(f"{document_id}:{index}".encode()).hexdigest(),
            "document_id": document_id,
            "text": value,
            "chunk_index": index,
            "token_count": count_tokens(value),
        })
        next_unit = last + 1
        if next_unit == len(ends):
            break

        # Reuse only a suffix, always consuming at least one new sentence next.
        candidates = [end]
        if overlap:
            for unit in range(last - 1, -1, -1):
                boundary = ends[unit]
                if boundary <= start:
                    break
                suffix_size = count_tokens(text[boundary:end].strip())
                if count_tokens(text[boundary:ends[next_unit]].strip()) <= chunk_size:
                    candidates.append(boundary)
                if suffix_size >= overlap:
                    break
        start = min(
            candidates,
            key=lambda pos: abs(count_tokens(text[pos:end].strip()) - overlap),
        )
    return chunks


__all__ = ["chunk_text", "count_tokens"]
