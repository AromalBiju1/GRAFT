"""Central tunables for the indexing pipeline.

All indexing thresholds live here so they are not buried as magic numbers
inside `builder.py` / `api/routes/indexing.py` (per .github/copilot-instructions
item 6: thresholds must be named constants and easy to tune).

Units: `chunk_size` / `chunk_overlap` / `max_summary_tokens` are
`cl100k_base` **tokens** (see `indexing.chunker.count_tokens`), not words.
"""

from __future__ import annotations

# --- Chunking (tokens) -------------------------------------------------
DEFAULT_CHUNK_SIZE = 400
DEFAULT_CHUNK_OVERLAP = 50

# --- Tree construction -------------------------------------------------
# Target minimum group size; cluster count is len(layer) // MIN_CLUSTER_SIZE.
DEFAULT_MIN_CLUSTER_SIZE = 3
# "gmm" | "kmeans" | "umap_gmm". Falls back to sequential groups of
# DEFAULT_MIN_CLUSTER_SIZE when embeddings are unusable or scikit-learn
# is not installed.
DEFAULT_CLUSTER_METHOD = "gmm"
DEFAULT_RANDOM_STATE = 42
# Guard against runaway recursion in RecursiveSummarizer.
MAX_TREE_DEPTH = 20
# A layer this small is collapsed into a single final root summary.
COLLAPSE_THRESHOLD = 3

# --- Summarization -----------------------------------------------------
DEFAULT_MAX_SUMMARY_TOKENS = 400
# Chars retained by the offline stub summariser (used when no LLM is wired).
STUB_SUMMARY_MAX_CHARS = 600

# --- Embeddings --------------------------------------------------------
# Dimension of the deterministic offline stub embedding.
STUB_EMBEDDING_DIM = 16

# --- Documents ---------------------------------------------------------
ALLOWED_DOCUMENT_SUFFIXES = frozenset({".pdf", ".docx"})
MAX_DOCUMENT_ID_LENGTH = 64

__all__ = [
    "DEFAULT_CHUNK_SIZE",
    "DEFAULT_CHUNK_OVERLAP",
    "DEFAULT_MIN_CLUSTER_SIZE",
    "DEFAULT_CLUSTER_METHOD",
    "DEFAULT_RANDOM_STATE",
    "MAX_TREE_DEPTH",
    "COLLAPSE_THRESHOLD",
    "DEFAULT_MAX_SUMMARY_TOKENS",
    "STUB_SUMMARY_MAX_CHARS",
    "STUB_EMBEDDING_DIM",
    "ALLOWED_DOCUMENT_SUFFIXES",
    "MAX_DOCUMENT_ID_LENGTH",
]
