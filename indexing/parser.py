"""Extract plain text from documents supported by the indexing pipeline."""

from __future__ import annotations

from os import PathLike
from pathlib import Path

from docx import Document
from pypdf import PdfReader


class DocumentParsingError(ValueError):
    """A document is unreadable or contains no extractable text."""


def parse_document(filepath: str | PathLike[str]) -> str:
    """Return readable text extracted from a PDF or DOCX document.

    Raises:
        DocumentParsingError: If parsing fails or no text can be extracted.
        ValueError: If the extension is unsupported.
    """
    path = Path(filepath)
    extension = path.suffix.lower()
    if extension not in {".pdf", ".docx"}:
        raise ValueError(
            f"Unsupported document type '{extension or '<none>'}'; "
            "only .pdf and .docx files are supported"
        )

    # Translate third-party container, XML, encryption and stream errors only
    # at the parsing boundary; keep the original exception for diagnosis.
    try:
        if not path.exists():
            raise FileNotFoundError(f"Document not found: {path}")
        if extension == ".pdf":
            parts = [
                text.strip()
                for page in PdfReader(path).pages
                if (text := page.extract_text()) and text.strip()
            ]
        else:
            parts = [p.text.strip() for p in Document(path).paragraphs if p.text.strip()]
    except Exception as exc:
        raise DocumentParsingError(f"Could not parse document: {path}: {exc}") from exc

    if not parts:
        raise DocumentParsingError(f"No extractable text found in document: {path}")

    return "\n\n".join(parts)
