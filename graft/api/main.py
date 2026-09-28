"""FastAPI entrypoint — the 'tail' of the project.

Provides:
  GET  /health          liveness + version
  POST /query           gated GRAFT pipeline
  POST /baseline/query  flat baseline for benchmarking
  POST /index           ingest raw text into the tree + vector store
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from db.logger import init_db, log_query
from graft import __version__
from graft.api.schemas import EvidenceItem, HealthResponse, QueryRequest, QueryResponse
from graft.config import settings
from graft.embeddings import active_provider, collection_name, embed_text, embedding_dim
from graft.generation import synthesize
from graft.indexing.pipeline import build_tree, index_document, persist_tree_nodes
from graft.modules.contradiction_detection import ContradictionDetectionModule
from graft.modules.fact_lookup import FactLookupModule
from graft.modules.multi_hop import MultiHopModule
from graft.modules.numeric_reasoning import NumericReasoningModule
from graft.retrieval import retrieve
from graft.router import route
from indexing.config import MAX_DOCUMENT_ID_LENGTH

logger = logging.getLogger(__name__)

#: Upload-specific chunking. Smaller than the indexing default so freshly
#: uploaded PDFs produce useful leaf granularity without a re-index.
UPLOAD_CHUNK_SIZE_TOKENS = 200
UPLOAD_CHUNK_OVERLAP_TOKENS = 20
UPLOAD_CLUSTER_SIZE = 4

MODULE_REGISTRY: dict[str, Any] = {
    "fact_lookup": FactLookupModule(),
    "multi_hop": MultiHopModule(),
    "numeric_reasoning": NumericReasoningModule(),
    "contradiction_detection": ContradictionDetectionModule(),
}

app = FastAPI(
    title="GRAFT API",
    version=__version__,
    description="Gated Retrieval Activation Framework for Trees — adaptive RAG",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _embed_query_stub(query: str) -> list[float]:
    """Embed a query with the same embedder the index used.

    Named for backwards compatibility; it is no longer necessarily a stub. It
    delegates to :func:`graft.embeddings.embed_text`, which falls back to the
    deterministic hash embedding when the real model is unavailable, so
    indexing and querying can never disagree on vector dimensionality.
    """
    return embed_text(query)


def _to_evidence_items(evidence: list[dict[str, Any]]) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    for ev in evidence:
        meta = ev.get("metadata") or {}
        items.append(
            EvidenceItem(
                source=meta.get("source") or ev.get("source"),
                page=meta.get("page") or ev.get("page"),
                chunk_id=ev.get("chunk_id") or meta.get("chunk_id"),
                node_id=meta.get("node_id") or ev.get("node_id"),
                level=meta.get("level") if isinstance(meta.get("level"), int) else ev.get("level"),
                score=ev.get("score"),
                text=(ev.get("text") or "")[:600] if ev.get("text") else None,
            )
        )
    return items


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        version=__version__,
        chroma_path=str(settings.chroma_path),
        collection=collection_name(),
        embedding_provider=active_provider(),
        embedding_dim=embedding_dim(),
    )


@app.post("/query", response_model=QueryResponse)
def query(req: QueryRequest) -> QueryResponse:
    if not req.query or not req.query.strip():
        raise HTTPException(status_code=422, detail="query must not be empty")

    request_id = req.request_id or f"req_{uuid.uuid4().hex[:10]}"
    t0 = time.perf_counter()

    # 1. Route
    decision = route(req.query, retrieval_depth_override=req.retrieval_depth)

    # 2. Retrieve
    q_emb = _embed_query_stub(req.query)
    try:
        retrieved = retrieve(
            q_emb,
            n_results=5,
            retrieval_depth=decision.retrieval_depth,
            filters=req.filters,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"retrieval failed: {exc}") from exc

    # 3. Specialist modules (only activated ones)
    module_results = []
    for name in decision.activated_modules:
        mod = MODULE_REGISTRY.get(name)
        if mod is None:
            continue
        try:
            module_results.append(mod(request_id, req.query, retrieved))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"module {name} failed: {exc}") from exc

    # 4. Generation
    try:
        final = synthesize(request_id, req.query, retrieved, module_results)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"generation failed: {exc}") from exc

    latency_ms = (time.perf_counter() - t0) * 1000.0

    # 5. Optional logging. A logging failure must not fail the user's query,
    #    but it must not vanish silently either.
    try:
        init_db()
        log_query(
            query_text=req.query,
            system="graft",
            latency_ms=latency_ms,
            modules_fired=decision.activated_modules,
            retrieval_depth=str(decision.retrieval_depth),
        )
    except Exception as exc:
        logger.warning(
            "Query logging failed for request %s: %s: %s", request_id, type(exc).__name__, exc
        )

    return QueryResponse(
        request_id=request_id,
        answer=str(final.get("answer", "")),
        evidence=_to_evidence_items(final.get("evidence") or retrieved),
        routing=decision.to_dict(),
        latency_ms=round(latency_ms, 2),
    )


@app.post("/index")
async def index_document_endpoint(request: Request) -> dict[str, Any]:
    """Ingest raw text or uploaded files into the tree + vector store.

    Supports two content types:
    - application/json: {"text": "...", "document_id": "doc_123", "source": "optional.pdf"}
    - multipart/form-data: one or more .pdf/.docx files (fields: files, file)

    Both branches now run the *same* pipeline -- parse, token-based chunk,
    embed, cluster, summarise, persist -- via :func:`graft.indexing.pipeline`.
    They previously used different tree builders, which meant a PDF upload and
    a JSON POST produced structurally different trees in the same collection.
    """
    content_type = request.headers.get("content-type", "")

    if "multipart/form-data" in content_type:
        # --- Multipart branch: file uploads ---
        form = await request.form()
        upload_files: list[UploadFile] = []
        # Robust detection: starlette stores files as UploadFile-like objects;
        # fastapi.UploadFile may not be identical to starlette's, so use duck typing.
        def _is_file_like(obj: object) -> bool:
            return (
                hasattr(obj, "filename")
                and hasattr(obj, "read")
                and bool(getattr(obj, "filename", None))
            )

        seen: set[int] = set()
        for _, value in form.multi_items():
            if _is_file_like(value) and id(value) not in seen:
                upload_files.append(value)  # type: ignore[arg-type]
                seen.add(id(value))
        # Also check getlist for each known field name to ensure multi-file uploads are captured
        for key in ("files", "file"):
            try:
                items = form.getlist(key)  # type: ignore[attr-defined]
            except Exception:
                items = []
            for item in items:
                if _is_file_like(item) and id(item) not in seen:
                    upload_files.append(item)  # type: ignore[arg-type]
                    seen.add(id(item))

        if not upload_files:
            raise HTTPException(status_code=422, detail="At least one file must be uploaded")

        # Validate extensions
        allowed = {".pdf", ".docx"}
        for uf in upload_files:
            ext = Path(uf.filename or "").suffix.lower()
            if ext not in allowed:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Unsupported file type '{ext or '<none>'}'; "
                        "only .pdf and .docx are supported"
                    ),
                )

        # Lazy import to avoid circular deps at startup
        import tempfile
        from collections import Counter

        from indexing.ingest import parse_document

        def _sanitize(filename: str) -> str:
            stem = Path(filename).stem or "doc_001"
            s = re.sub(r"[^a-zA-Z0-9_-]", "_", stem).strip("_")
            return s[:MAX_DOCUMENT_ID_LENGTH] or "doc_001"

        def _levels(nodes: list[Any]) -> dict[str, int]:
            if not nodes:
                return {}
            max_lvl = max(n.level for n in nodes)
            cnt = Counter(n.level for n in nodes)
            out: dict[str, int] = {}
            for lvl in sorted(cnt):
                if lvl == 0:
                    label = f"{lvl}_leaf"
                elif lvl == max_lvl and max_lvl > 0:
                    label = f"{lvl}_root"
                else:
                    label = f"{lvl}_summary"
                out[label] = cnt[lvl]
            return out

        all_nodes: list[Any] = []
        document_ids: list[str] = []

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            for uf in upload_files:
                assert uf.filename is not None
                doc_id = _sanitize(uf.filename)
                base = doc_id
                suffix = 1
                while doc_id in document_ids:
                    doc_id = f"{base}_{suffix}"
                    suffix += 1
                document_ids.append(doc_id)

                ext = Path(uf.filename).suffix.lower()
                tmp_file = tmp_path / f"{doc_id}{ext}"
                content = await uf.read()
                if not content:
                    raise HTTPException(status_code=422, detail=f"File {uf.filename} is empty")
                tmp_file.write_bytes(content)

                try:
                    text = parse_document(tmp_file)
                except ValueError as exc:
                    raise HTTPException(status_code=422, detail=str(exc)) from exc
                except Exception as exc:
                    raise HTTPException(
                        status_code=500, detail=f"Failed to parse {uf.filename}: {exc}"
                    ) from exc

                if not text or not text.strip():
                    raise HTTPException(
                        status_code=422, detail=f"No extractable text in {uf.filename}"
                    )

                nodes = build_tree(
                    text,
                    document_id=doc_id,
                    source=uf.filename,
                    chunk_size=UPLOAD_CHUNK_SIZE_TOKENS,
                    chunk_overlap=UPLOAD_CHUNK_OVERLAP_TOKENS,
                    cluster_size=UPLOAD_CLUSTER_SIZE,
                )
                if not nodes:
                    raise HTTPException(
                        status_code=422, detail=f"No chunks generated for {uf.filename}"
                    )
                all_nodes.extend(nodes)

            if not all_nodes:
                raise HTTPException(
                    status_code=422, detail="No nodes generated from uploaded files"
                )

            persist_tree_nodes(all_nodes)

        total_chunks = sum(1 for n in all_nodes if n.level == 0)
        return {
            "status": "success",
            "document_ids": document_ids,
            "total_chunks": total_chunks,
            "tree_stats": {
                "total_nodes": len(all_nodes),
                "levels": _levels(all_nodes),
            },
        }

    # --- JSON branch: legacy behavior ---
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=422, detail="Invalid JSON payload") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="JSON payload must be an object")

    text = payload.get("text")
    document_id = payload.get("document_id")
    source = payload.get("source")
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(status_code=422, detail="text must be a non-empty string")
    if not isinstance(document_id, str) or not document_id.strip():
        raise HTTPException(status_code=422, detail="document_id must be a non-empty string")

    nodes = index_document(text, document_id=document_id, source=source)
    return {
        "document_id": document_id,
        "nodes_indexed": len(nodes),
        "nodes": [n.to_dict() for n in nodes[:20]],
    }


def main() -> None:  # pragma: no cover
    import uvicorn

    uvicorn.run(
        "graft.api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.api_reload,
    )
