"""Indexing — chunking, clustering, summarisation, and tree construction.

Public entrypoint is :func:`build_tree` in :mod:`graft.indexing.pipeline`.
Lower-level helpers live in :mod:`graft.indexing.chunking` and
:mod:`graft.indexing.tree`.

Both of those re-export the tested implementations from :mod:`indexing.*`
rather than reimplementing them, so ``graft`` is a single public surface over
one set of indexing primitives.
"""

from graft.indexing.chunking import chunk_text, count_tokens
from graft.indexing.pipeline import build_tree, index_document
from graft.indexing.tree import build_tree_from_chunks

__all__ = [
    "build_tree",
    "build_tree_from_chunks",
    "chunk_text",
    "count_tokens",
    "index_document",
]
