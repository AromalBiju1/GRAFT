"""FastAPI /index endpoint for document indexing (issue #17).

Implements:
  POST /index
  Payload: multipart/form-data with one or more files (.pdf, .docx)
  Flow: save temp -> parse (indexing/ingest.py) -> chunk (indexing/chunker.py)
        -> embed + tree (indexing/builder.py -> indexing/summarizer.py)
        -> persist (indexing/store.py -> ChromaVectorStore)

Tunables come from `indexing.config`; persistence targets come from
`indexing.vector_store.chroma_store`. This module only handles HTTP concerns
(upload validation, temp files, stats) — the pipeline itself lives in
`indexing.builder`.

Response:
{
  "status": "success",
  "document_ids": ["doc_001"],
  "total_chunks": 24,
  "tree_stats": {
    "total_nodes": 31,
    "levels": {
      "0_leaf": 24,
      "1_summary": 6,
      "2_root": 1
    }
  }
}
"""

from __future__ import annotations

import re
import tempfile
from collections import Counter
from pathlib import Path
from typing import Annotated, Sequence

from fastapi import APIRouter, File, HTTPException, UploadFile

from indexing.builder import build_documents
from indexing.config import (
    ALLOWED_DOCUMENT_SUFFIXES,
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_MIN_CLUSTER_SIZE,
    MAX_DOCUMENT_ID_LENGTH,
)
from indexing.ingest import parse_document
from indexing.store import persist_tree_nodes
from indexing.tree_node import TreeNode
from indexing.vector_store import ChromaVectorStore
from indexing.vector_store.chroma_store import DEFAULT_COLLECTION_NAME, DEFAULT_PERSIST_PATH

router = APIRouter(tags=["indexing"])

# Overridable per deployment (tests patch these); defaults are the
# ChromaVectorStore module constants so there is a single source of truth.
DEFAULT_PERSIST_PATH = Path(DEFAULT_PERSIST_PATH)
DEFAULT_COLLECTION = DEFAULT_COLLECTION_NAME

DOCUMENT_ID_FALLBACK = "doc_001"


def _sanitize_document_id(filename: str) -> str:
    """Derive a safe document_id from a filename stem."""
    stem = Path(filename).stem or DOCUMENT_ID_FALLBACK
    sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", stem).strip("_")
    return (sanitized or DOCUMENT_ID_FALLBACK)[:MAX_DOCUMENT_ID_LENGTH]


def _unique_document_id(candidate: str, taken: Sequence[str]) -> str:
    """Return *candidate* or a ``_1``/``_2`` suffixed variant not in *taken*."""
    if candidate not in taken:
        return candidate
    suffix = 1
    while f"{candidate}_{suffix}" in taken:
        suffix += 1
    return f"{candidate}_{suffix}"


def _validate_extension(filename: str) -> None:
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_DOCUMENT_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported file type '{ext or '<none>'}'; only "
                f"{' and '.join(sorted(ALLOWED_DOCUMENT_SUFFIXES))} are supported"
            ),
        )


def _build_levels_dict(nodes: Sequence[TreeNode]) -> dict[str, int]:
    """Return levels dict like {"0_leaf": 24, "1_summary": 6, "2_root": 1}."""
    if not nodes:
        return {}
    max_level = max(n.level for n in nodes)
    counts = Counter(n.level for n in nodes)
    levels: dict[str, int] = {}
    for lvl in sorted(counts):
        if lvl == 0:
            label = f"{lvl}_leaf"
        elif lvl == max_level and max_level > 0:
            label = f"{lvl}_root"
        else:
            label = f"{lvl}_summary"
        levels[label] = counts[lvl]
    return levels


@router.post("/index")
async def index_documents(
    files: Annotated[list[UploadFile] | None, File(description="One or more .pdf or .docx files")] = None,
    file: Annotated[UploadFile | None, File(description="Single file alias")] = None,
) -> dict:
    """Ingest uploaded documents into the hierarchical tree + vector store."""
    # Support both `files` (multiple) and `file` (single) field names, and also
    # plain `files` sent as single file (FastAPI may wrap it as list).
    upload_files: list[UploadFile] = []
    if files:
        upload_files.extend(files)
    if file:
        upload_files.append(file)

    if not upload_files:
        raise HTTPException(status_code=422, detail="At least one file must be uploaded")

    for upload in upload_files:
        if not upload.filename:
            raise HTTPException(status_code=422, detail="Uploaded file must have a filename")
        _validate_extension(upload.filename)

    document_ids: list[str] = []
    all_nodes: list[TreeNode] = []

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            documents: list[tuple[str, str, str]] = []

            for upload in upload_files:
                assert upload.filename is not None
                document_id = _unique_document_id(
                    _sanitize_document_id(upload.filename), document_ids
                )
                document_ids.append(document_id)

                content = await upload.read()
                if not content:
                    raise HTTPException(
                        status_code=422, detail=f"File {upload.filename} is empty"
                    )

                suffix = Path(upload.filename).suffix.lower()
                tmp_file = tmp_path / f"{document_id}{suffix}"
                tmp_file.write_bytes(content)

                try:
                    text = parse_document(tmp_file)
                except FileNotFoundError as exc:
                    raise HTTPException(status_code=500, detail=str(exc)) from exc
                except ValueError as exc:
                    # DocumentParsingError subclasses ValueError: unreadable or empty.
                    raise HTTPException(status_code=422, detail=str(exc)) from exc
                except Exception as exc:
                    raise HTTPException(
                        status_code=500, detail=f"Failed to parse {upload.filename}: {exc}"
                    ) from exc

                if not text.strip():
                    raise HTTPException(
                        status_code=422, detail=f"No extractable text in {upload.filename}"
                    )
                documents.append((document_id, text, upload.filename))

            if not documents:
                raise HTTPException(status_code=422, detail="No nodes generated from uploaded files")

            # Chunk -> embed -> cluster -> summarize for every document.
            try:
                all_nodes = build_documents(
                    documents,
                    chunk_size=DEFAULT_CHUNK_SIZE,
                    chunk_overlap=DEFAULT_CHUNK_OVERLAP,
                    cluster_size=DEFAULT_MIN_CLUSTER_SIZE,
                )
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            except Exception as exc:
                raise HTTPException(status_code=500, detail=f"Indexing failed: {exc}") from exc

            if not all_nodes:
                raise HTTPException(status_code=422, detail="No nodes generated from uploaded files")

            store = ChromaVectorStore(
                persist_path=DEFAULT_PERSIST_PATH,
                collection_name=DEFAULT_COLLECTION,
            )
            try:
                persist_tree_nodes(all_nodes, store)
            finally:
                store.close()

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Indexing failed: {exc}") from exc

    return {
        "status": "success",
        "document_ids": document_ids,
        "total_chunks": sum(1 for n in all_nodes if n.level == 0),
        "tree_stats": {
            "total_nodes": len(all_nodes),
            "levels": _build_levels_dict(all_nodes),
        },
    }
