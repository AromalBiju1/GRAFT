"""Parse documents and return standard ingestion chunks.

Usage:
    from indexing.ingest import ingest_document
    chunks = ingest_document("/path/to/file.pdf", document_id="doc_001")
"""

from os import PathLike

from indexing.chunker import chunk_text
from indexing.parser import DocumentParsingError, parse_document


def ingest_document(
    filepath: str | PathLike[str],
    *,
    document_id: str,
    chunk_size: int = 400,
    overlap: int = 50,
) -> list[dict]:
    """Parse a PDF/DOCX path and chunk it using the caller's unique document ID."""
    return chunk_text(
        parse_document(filepath), document_id=document_id,
        chunk_size=chunk_size, overlap=overlap,
    )


__all__ = ["parse_document", "ingest_document", "DocumentParsingError"]
