"""Ingest alias for document parsing.

This module re-exports :func:`indexing.parser.parse_document` under the name
expected by the indexing pipeline spec (indexing/ingest.py). Keeping the
canonical implementation in parser.py avoids duplication while satisfying
the import path described in issue #17.

Usage:
    from indexing.ingest import parse_document
    text = parse_document("/path/to/file.pdf")
"""

from indexing.parser import parse_document

__all__ = ["parse_document"]
