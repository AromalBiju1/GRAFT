# Frontend & ChromaDB Integration Design

## Overview

This specification details the end-to-end integration of the GRAFT React frontend with the ChromaDB vector database and adaptive RAG pipeline. It resolves Issue A (`/api/chat` stub), unifies query execution across single-query and chat endpoints, implements two-phase document upload and RAPTOR indexing into ChromaDB, and enriches the chat user interface with real-time routing badges and collapsible ChromaDB evidence inspection.

---

## 1. Architecture & Core Pipeline

### 1.1 Shared Execution Engine (`api/query_runner.py`)
To prevent drift between `/query` and `/api/chat`, the core query orchestration logic is extracted from `api/main.py:query` into a shared module `api/query_runner.py`:

```python
def execute_query(
    query_text: str,
    request_id: str | None = None,
    retrieval_depth_override: int | None = None,
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ...
```

The function executes:
1. **Routing**: `decision = route(query_text, retrieval_depth_override=retrieval_depth_override)`
2. **Retrieval**: `retrieve(embed_text(query_text), n_results=5, retrieval_depth=decision.retrieval_depth, filters=filters)`
3. **Reasoning Modules**: Runs activated modules (`fact_lookup`, `multi_hop`, `numeric_reasoning`, `contradiction_detection`).
4. **Answer Generation**: `synthesize(request_id, query_text, retrieved, module_results, llm_client=llm_client)`
5. **Logging**: Logs query execution to SQLite (`db/logger.py:log_query`).
6. **Return Payload**:
   ```python
   {
       "request_id": request_id,
       "answer": str(final.get("answer", "")),
       "evidence": final.get("evidence") or retrieved,
       "routing": decision.to_dict(),
       "latency_ms": round(latency_ms, 2),
   }
   ```

Both `POST /query` and `POST /api/chat` delegate to `execute_query`.

### 1.2 ChromaDB Document Filtering (`api/uploads.py`)
In `api/uploads.py`, `run_query(prompt: str, doc_ids: list[str]) -> dict` is implemented using `execute_query`:
- When `doc_ids` has 1 item: `filters = {"document_id": doc_ids[0]}`.
- When `doc_ids` has multiple items: `filters = {"document_id": {"$in": doc_ids}}`.
- When `doc_ids` is empty: `filters = None` (retrieves from the entire pre-indexed ChromaDB corpus).

ChromaDB retrieval (`retrieval/__init__.py`) cleanly composes `filters` with depth restrictions via `$and`.

### 1.3 Server-Sent Events (SSE) Streaming (`POST /api/chat`)
`POST /api/chat` receives `ChatReq(prompt: str, file_ids: list[str] = [])`:
1. If any `fid` in `file_ids` is in `"uploaded"` or `"parsing"` state, yields `event: status` until ready.
2. If any file failed processing or is unknown, yields `event: error`.
3. Calls `execute_query` asynchronously.
4. Yields `event: meta` with `routing` and normalized `evidence` list.
5. Streams `event: token` containing word-level chunks.
6. Yields `event: done`.

---

## 2. Frontend Integration (`frontend/src/`)

### 2.1 API Client (`frontend/src/api.js`)
- `uploadFile(file, onProgress)`: Posts `multipart/form-data` to `/api/files` with single `file`. Reports upload percentage via XHR progress and returns file metadata `{ id, name, size, status, path }`.
- `pollFileStatus(fid)`: Fetches `/api/files/{fid}` to track indexing state (`uploaded` $\to$ `parsing` $\to$ `ready`).
- `sendChat({ prompt, fileIds, onEvent, onError, onDone })`: Issues `POST /api/chat` with JSON `{ prompt, file_ids: fileIds }` and parses SSE events (`status`, `meta`, `token`, `error`, `done`).

### 2.2 Chat & Session State (`frontend/src/App.jsx`)
- As soon as files are dropped or selected, they are validated and uploaded via `uploadFile`.
- Their indexing progress is tracked in `AttachmentTray`. Once `ready`, their IDs are retained in the session's active document list.
- Sending a message transmits `{ prompt, file_ids }`.
- When no files are attached to the session, prompt executes against the entire ChromaDB corpus.

### 2.3 Evidence & Routing Inspector (`frontend/src/components/Feed.jsx`)
- Updates message representation in `Feed.jsx` to store `routing` and `evidence` objects received from `event: meta`.
- Under the markdown answer, renders:
  1. `<RoutingBadges routing={message.routing} />`: displaying complexity, retrieval depth, and active modules.
  2. Collapsible `<details class="evidence-accordion">`: rendering `<EvidenceList evidence={message.evidence} />` displaying source file, RAPTOR level (`0_leaf`, `1_summary`, `2_root`), similarity score, and excerpt.

### 2.4 Vite Dev Proxy
Update `frontend/vite.config.js` and `frontend/vite.local.config.js` to proxy `/api` requests to the GRAFT backend.

---

## 3. Error Handling

- **Upload Rejections**: Invalid formats (non-PDF/DOCX/TXT) or $>10$ MB files are rejected with friendly UI notices.
- **Indexing Failures**: Background errors set status `failed` and stream SSE error without crashing the backend.
- **Retrieval Fallback**: When ChromaDB has no relevant matches, return fallback answer and indicate zero evidence in the accordion.
- **Stream Cancellation**: Clicking stop triggers `xhr.abort()` or `reader.cancel()`, halting token generation while preserving partial response.

---

## 4. Verification & Testing

1. **Backend Tests (`tests/test_chat_endpoint.py`)**:
   - `test_chat_corpus_query`: Validates `POST /api/chat` with empty `file_ids` returns full corpus answer, routing metadata, and Chroma evidence.
   - `test_chat_document_query`: Uploads a document via `POST /api/files`, waits for `ready`, calls `POST /api/chat` with `file_ids`, and asserts evidence matches the uploaded `document_id`.
   - `test_chat_unknown_file`: Asserts nonexistent file ID yields SSE error event.
   - Run full test suite: `pytest tests/`.
2. **Frontend Build & Component Validation**:
   - Verify frontend compiles cleanly: `npm run build` in `frontend/`.

