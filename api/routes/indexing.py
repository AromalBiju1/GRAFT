"""FastAPI /index endpoint for document indexing (issue #17).

Implements:
  POST /index
  Payload: multipart/form-data with one or more files (.pdf, .docx)
  Flow: save temp -> parse (indexing/ingest.py) -> chunk (indexing/chunker.py)
        -> embed + tree (indexing/builder.py) -> persist (ChromaVectorStore)

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
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile

from indexing.builder import build_tree
from indexing.ingest import parse_document
from indexing.vector_store import ChromaVectorStore

try:
    from graft.config import settings as graft_settings
    DEFAULT_PERSIST_PATH = graft_settings.chroma_path
    DEFAULT_COLLECTION = graft_settings.chroma_collection
except Exception:
    DEFAULT_PERSIST_PATH = Path(".graft/chroma")
    DEFAULT_COLLECTION = "graft_tree_nodes"

router = APIRouter(tags=["indexing"])

ALLOWED_EXTENSIONS = {".pdf", ".docx"}
# Chunking tunables — small enough to yield multiple chunks for test PDFs,
# large enough for real RFCs. Mirrors smoke-test expectations (24 leaf etc).
DEFAULT_CHUNK_SIZE = 200
DEFAULT_CHUNK_OVERLAP = 20
DEFAULT_CLUSTER_SIZE = 4


def _sanitize_document_id(filename: str) -> str:
    """Derive a safe document_id from filename stem."""
    stem = Path(filename).stem or "doc_001"
    # Replace non-alphanumeric with underscore, keep lowercase
    sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", stem).strip("_")
    if not sanitized:
        sanitized = "doc_001"
    # Ensure non-empty and filesystem-safe
    return sanitized[:64]


def _validate_extension(filename: str) -> None:
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext or '<none>'}'; only .pdf and .docx are supported",
        )


def _build_levels_dict(nodes) -> dict[str, int]:
    """Return levels dict like {"0_leaf": 24, "1_summary": 6, "2_root": 1}."""
    if not nodes:
        return {}
    max_level = max(n.level for n in nodes)
    counts = Counter(n.level for n in nodes)
    levels: dict[str, int] = {}
    for lvl in sorted(counts):
        count = counts[lvl]
        if lvl == 0:
            label = f"{lvl}_leaf"
        elif lvl == max_level and max_level > 0:
            label = f"{lvl}_root"
        else:
            label = f"{lvl}_summary"
        levels[label] = count
    return levels


@router.post("/index")
async def index_documents(
    files: Annotated[list[UploadFile] | None, File(description="One or more .pdf or .docx files")] = None,
    file: Annotated[UploadFile | None, File(description="Single file alias")] = None,
) -> dict:
    """Ingest raw documents into the hierarchical tree + vector store."""
    # Support both `files` (multiple) and `file` (single) field names, and also
    # plain `files` sent as single file (FastAPI may wrap it as list).
    upload_files: list[UploadFile] = []
    if files:
        upload_files.extend(files)
    if file:
        upload_files.append(file)

    if not upload_files:
        raise HTTPException(status_code=422, detail="At least one file must be uploaded")

    # Validate extensions early
    for uf in upload_files:
        if not uf.filename:
            raise HTTPException(status_code=422, detail="Uploaded file must have a filename")
        _validate_extension(uf.filename)

    all_nodes = []
    document_ids: list[str] = []
    temp_dir: Path | None = None

    try:
        # Use a temporary directory to save uploaded files
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            for uf in upload_files:
                assert uf.filename is not None
                document_id = _sanitize_document_id(uf.filename)
                # Ensure unique document_ids for duplicate filenames
                base_id = document_id
                suffix = 1
                while document_id in document_ids:
                    document_id = f"{base_id}_{suffix}"
                    suffix += 1
                document_ids.append(document_id)

                ext = Path(uf.filename).suffix.lower()
                tmp_file = tmp_path / f"{document_id}{ext}"
                # Read upload content
                content = await uf.read()
                if not content:
                    raise HTTPException(status_code=422, detail=f"File {uf.filename} is empty")
                tmp_file.write_bytes(content)

                # Parse document via indexing/ingest -> indexing/parser
                try:
                    text = parse_document(tmp_file)
                except FileNotFoundError as exc:
                    raise HTTPException(status_code=500, detail=str(exc)) from exc
                except ValueError as exc:
                    raise HTTPException(status_code=422, detail=str(exc)) from exc
                except Exception as exc:
                    raise HTTPException(status_code=500, detail=f"Failed to parse {uf.filename}: {exc}") from exc

                if not text or not text.strip():
                    raise HTTPException(status_code=422, detail=f"No extractable text in {uf.filename}")

                # Chunk + embed + tree (builder handles embeddings)
                try:
                    nodes = build_tree(
                        text,
                        document_id=document_id,
                        source=uf.filename,
                        chunk_size=DEFAULT_CHUNK_SIZE,
                        chunk_overlap=DEFAULT_CHUNK_OVERLAP,
                        cluster_size=DEFAULT_CLUSTER_SIZE,
                    )
                except ValueError as exc:
                    raise HTTPException(status_code=422, detail=str(exc)) from exc
                except Exception as exc:
                    raise HTTPException(status_code=500, detail=f"Failed to build tree for {uf.filename}: {exc}") from exc

                if not nodes:
                    raise HTTPException(status_code=422, detail=f"No chunks generated for {uf.filename}")

                all_nodes.extend(nodes)

            # Persist all nodes to ChromaVectorStore
            if not all_nodes:
                raise HTTPException(status_code=422, detail="No nodes generated from uploaded files")

            # Use default persist path (.graft/chroma) for endpoint
            store = ChromaVectorStore(
                persist_path=DEFAULT_PERSIST_PATH,
                collection_name=DEFAULT_COLLECTION,
            )
            try:
                from indexing.store import persist_tree_nodes

                persist_tree_nodes(all_nodes, store)
            finally:
                try:
                    store.close()
                except Exception:
                    pass

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Indexing failed: {exc}") from exc

    # Compute stats
    total_chunks = sum(1 for n in all_nodes if n.level == 0)
    total_nodes = len(all_nodes)
    levels = _build_levels_dict(all_nodes)

    return {
        "status": "success",
        "document_ids": document_ids,
        "total_chunks": total_chunks,
        "tree_stats": {
            "total_nodes": total_nodes,
            "levels": levels,
        },
    }
