"""Document indexing and hierarchical tree construction.

Public surface (see `docs/interfaces.md` sections 2, 3, 12 and 13):

    parse_document / ingest_document   PDF + DOCX text extraction
    chunk_text / count_tokens          token-based chunking
    build_tree / index_documents       RAPTOR tree construction + persistence
    RecursiveSummarizer                recursive cluster+summarize engine
    TreeNode                           strict-tree node
    persist_tree_nodes                 ChromaDB upsert

Typical use:

    from indexing import build_tree, index_documents

    nodes = build_tree(text, document_id="doc_001", source="rfc793.pdf")
    index_documents([("doc_001", text, "rfc793.pdf")])  # build + persist
"""

from indexing.builder import build_tree, build_tree_from_chunks, index_documents
from indexing.chunker import chunk_text, count_tokens
from indexing.ingest import DocumentParsingError, ingest_document, parse_document
from indexing.prompts import SUMMARIZATION_PROMPT, format_summarization_prompt
from indexing.store import persist_tree_nodes
from indexing.summarizer import RecursiveSummarizer, build_raptor_tree
from indexing.tree_node import TreeNode
from indexing.vector_store import ChromaVectorStore

__all__ = [
    "ChromaVectorStore",
    "DocumentParsingError",
    "RecursiveSummarizer",
    "SUMMARIZATION_PROMPT",
    "TreeNode",
    "build_raptor_tree",
    "build_tree",
    "build_tree_from_chunks",
    "chunk_text",
    "count_tokens",
    "format_summarization_prompt",
    "index_documents",
    "ingest_document",
    "parse_document",
    "persist_tree_nodes",
]
