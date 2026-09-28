"""Chunking for the indexing pipeline.

GRAFT has exactly one chunker: the token-based one in :mod:`indexing.chunker`,
which implements the contract in ``docs/interfaces.md`` section 2 (SHA-256
``chunk_id``, ``chunk_index``, ``token_count``; ``cl100k_base`` tokens with a
400/50 target).

This module previously held a *word*-based stub that emitted a different chunk
shape and contradicted that documented contract. The stub is gone; chunk sizes
are now token counts everywhere, so ``chunk_size`` means the same thing to the
JSON and multipart paths of ``POST /index``.
"""

from __future__ import annotations

from indexing.chunker import chunk_text, count_tokens

__all__ = ["chunk_text", "count_tokens"]
