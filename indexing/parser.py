"""Extract plain text from documents supported by the indexing pipeline."""

from __future__ import annotations

from os import PathLike
from pathlib import Path

from docx import Document
from pypdf import PdfReader


def parse_document(filepath: str | PathLike[str]) -> str:
    """Return readable text extracted from a PDF or DOCX document.

    Raises:
        FileNotFoundError: If ``filepath`` does not exist.
        ValueError: If the extension is unsupported or no text can be extracted.
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"Document not found: {path}")

    extension = path.suffix.lower()
    if extension == ".pdf":
        parts = [
            text.strip()
            for page in PdfReader(path).pages
            if (text := page.extract_text()) and text.strip()
        ]
    elif extension == ".docx":
        parts = [
            paragraph.text.strip()
            for paragraph in Document(path).paragraphs
            if paragraph.text.strip()
        ]
    else:
        raise ValueError(
            f"Unsupported document type '{extension or '<none>'}'; "
            "only .pdf and .docx files are supported"
        )

    if not parts:
        raise ValueError(f"No extractable text found in document: {path}")

    return "\n\n".join(parts)
