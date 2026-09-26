<<<<<<< HEAD
"""Vector-store shim re-exporting ChromaVectorStore from indexing.

This alias exists so graft.indexing.pipeline and graft.retrieval can import
`from vector_store import ChromaVectorStore` while the canonical implementation
lives in indexing.vector_store.chroma_store.
"""

from indexing.vector_store import ChromaVectorStore
=======
"""Vector-store integrations for GRAFT."""

from .chroma_store import ChromaVectorStore
>>>>>>> origin/main

__all__ = ["ChromaVectorStore"]
