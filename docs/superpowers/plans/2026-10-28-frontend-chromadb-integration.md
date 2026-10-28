# Frontend & ChromaDB Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate the GRAFT React frontend with the ChromaDB vector database and adaptive RAG query engine, resolving the `/api/chat` stub, enabling two-phase document upload and tree indexing, and rendering routing badges and evidence cards in chat.

**Architecture:** A shared execution runner (`api/query_runner.py`) powers both `/query` and `/api/chat`. In `api/uploads.py`, `run_query` applies ChromaDB metadata filters for uploaded documents. The frontend uploads documents asynchronously to `/api/files`, tracks indexing to ChromaDB, sends chat prompts with active file IDs to `/api/chat`, and streams answers while displaying GRAFT routing complexity and ChromaDB evidence nodes.

**Tech Stack:** Python 3.10+, FastAPI, ChromaDB, Sentence-Transformers, React 19, Vite.

## Global Constraints

- No external network access during tests; use deterministic fallbacks and local ChromaDB.
- Preserve all existing comments and docstrings.
- Tests must pass cleanly with `pytest tests/`.
- Frontend must build cleanly with `npm run build`.

---

### Task 1: Extract Shared Query Engine (`api/query_runner.py`)

**Files:**
- Create: `api/query_runner.py`
- Modify: `api/main.py:220-286`
- Test: `tests/test_query_endpoint.py`

**Interfaces:**
- Produces: `execute_query(query_text: str, request_id: str | None = None, retrieval_depth_override: int | None = None, filters: dict[str, Any] | None = None) -> dict[str, Any]`

- [ ] **Step 1: Create `api/query_runner.py`**

```python
"""Shared query execution pipeline for GRAFT."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from fastapi import HTTPException

from config import settings
from db.logger import init_db, log_query
from embeddings import embed_text
from generation import synthesize
from modules.contradiction_detection import ContradictionDetectionModule
from modules.fact_lookup import FactLookupModule
from modules.multi_hop import MultiHopModule
from modules.numeric_reasoning import NumericReasoningModule
from retrieval import retrieve
from router import route

logger = logging.getLogger(__name__)

MODULE_REGISTRY: dict[str, Any] = {
    "fact_lookup": FactLookupModule(),
    "multi_hop": MultiHopModule(),
    "numeric_reasoning": NumericReasoningModule(),
    "contradiction_detection": ContradictionDetectionModule(),
}


def _embed_query(query: str) -> list[float]:
    return embed_text(query)


def execute_query(
    query_text: str,
    request_id: str | None = None,
    retrieval_depth_override: int | None = None,
    filters: dict[str, Any] | None = None,
    llm_client: Any | None = None,
) -> dict[str, Any]:
    """Execute the full GRAFT pipeline: route -> retrieve -> modules -> synthesize -> log."""
    if not query_text or not query_text.strip():
        raise HTTPException(status_code=422, detail="query must not be empty")

    req_id = request_id or f"req_{uuid.uuid4().hex[:10]}"
    t0 = time.perf_counter()

    # 1. Route
    decision = route(query_text, retrieval_depth_override=retrieval_depth_override)

    # 2. Retrieve
    q_emb = _embed_query(query_text)
    try:
        retrieved = retrieve(
            q_emb,
            n_results=5,
            retrieval_depth=decision.retrieval_depth,
            filters=filters,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"retrieval failed: {exc}") from exc

    # 3. Specialist modules
    module_results = []
    for name in decision.activated_modules:
        mod = MODULE_REGISTRY.get(name)
        if mod is None:
            continue
        try:
            module_results.append(mod(req_id, query_text, retrieved))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"module {name} failed: {exc}") from exc

    # 4. Synthesize
    try:
        final = synthesize(req_id, query_text, retrieved, module_results, llm_client=llm_client)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"generation failed: {exc}") from exc

    latency_ms = (time.perf_counter() - t0) * 1000.0

    # 5. Log
    try:
        init_db()
        log_query(
            query_text=query_text,
            system="graft",
            latency_ms=latency_ms,
            modules_fired=decision.activated_modules,
            retrieval_depth=str(decision.retrieval_depth),
        )
    except Exception as exc:
        logger.warning(
            "Query logging failed for request %s: %s: %s", req_id, type(exc).__name__, exc
        )

    return {
        "request_id": req_id,
        "answer": str(final.get("answer", "")),
        "evidence": final.get("evidence") or retrieved,
        "routing": decision.to_dict(),
        "latency_ms": round(latency_ms, 2),
    }
```

- [ ] **Step 2: Update `api/main.py:query` to use `execute_query`**

Replace the manual pipeline in `api/main.py:query` with a call to `execute_query`, maintaining schema compatibility with `QueryResponse`.

- [ ] **Step 3: Run existing query tests to verify compatibility**

Run: `pytest tests/test_query_endpoint.py -v`
Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git add api/query_runner.py api/main.py
git commit -m "refactor(api): extract shared execute_query runner"
```

---

### Task 2: Implement `run_query` and ChromaDB Filtering in `api/uploads.py`

**Files:**
- Modify: `api/uploads.py:101-135`
- Test: `tests/test_upload.py`

**Interfaces:**
- Consumes: `api.query_runner.execute_query`
- Produces: `POST /api/chat` with SSE events `meta`, `token`, `done`, `error`.

- [ ] **Step 1: Implement `run_query` in `api/uploads.py`**

```python
def run_query(prompt: str, doc_ids: list[str]) -> dict[str, Any]:
    from api.main import _build_llm_client
    from api.query_runner import execute_query

    filters: dict[str, Any] | None = None
    if len(doc_ids) == 1:
        filters = {"document_id": doc_ids[0]}
    elif len(doc_ids) > 1:
        filters = {"document_id": {"$in": doc_ids}}

    llm_client = _build_llm_client()
    return execute_query(prompt, filters=filters, llm_client=llm_client)
```

- [ ] **Step 2: Ensure SSE formatting in `POST /api/chat` sends normalized evidence**

In `api/uploads.py`, serialize `result.get("evidence")` and `result.get("routing")` into the `meta` event. Also verify that word token streaming emits real tokens from `result["answer"]`.

- [ ] **Step 3: Run upload tests to verify no regressions**

Run: `pytest tests/test_upload.py -v`
Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git add api/uploads.py
git commit -m "feat(api): wire run_query and /api/chat to shared execution engine"
```

---

### Task 3: Comprehensive Integration Tests for `/api/chat` (`tests/test_chat_endpoint.py`)

**Files:**
- Create: `tests/test_chat_endpoint.py`
- Test: `tests/test_chat_endpoint.py`

- [ ] **Step 1: Write integration tests for `/api/chat`**

Test scenarios:
1. `test_chat_corpus_query`: Empty `file_ids` streams real answer, routing dictionary, and ChromaDB evidence items.
2. `test_chat_single_file_query`: Upload a `.txt` file, wait for indexing to reach `ready`, send `POST /api/chat` with that `file_id`, assert evidence nodes carry that `document_id`.
3. `test_chat_unknown_file`: Sending an unknown `file_id` returns an SSE `event: error`.

- [ ] **Step 2: Run tests to verify**

Run: `pytest tests/test_chat_endpoint.py -v`
Expected: ALL PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_chat_endpoint.py
git commit -m "test(api): add end-to-end integration tests for /api/chat"
```

---

### Task 4: Update Vite Dev Proxy Configuration

**Files:**
- Modify: `frontend/vite.config.js`
- Modify: `frontend/vite.local.config.js`

- [ ] **Step 1: Update proxies to include `/api`**

Ensure `/api` routes to `http://127.0.0.1:8000` (or `GRAFT_API` in `vite.local.config.js`).

- [ ] **Step 2: Commit**

```bash
git add frontend/vite.config.js frontend/vite.local.config.js
git commit -m "chore(frontend): proxy /api in vite config"
```

---

### Task 5: Implement Two-Phase Upload & Chat Client in `frontend/src/api.js`

**Files:**
- Modify: `frontend/src/api.js`

- [ ] **Step 1: Add `uploadFile` and `pollFileStatus` functions**

```javascript
export function uploadFile(file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const body = new FormData();
    body.append("file", file);
    xhr.open("POST", `${import.meta.env.VITE_API_URL ?? ""}/api/files`);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) {
        onProgress(Math.round((e.loaded / e.total) * 100));
      }
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText));
        } catch (e) {
          reject(new Error("Invalid JSON response"));
        }
      } else {
        let msg = `Upload failed (${xhr.status}).`;
        try { msg = JSON.parse(xhr.responseText).detail ?? msg; } catch {}
        reject(new Error(msg));
      }
    };
    xhr.onerror = () => reject(new Error("Network error during file upload"));
    xhr.send(body);
  });
}

export async function pollFileStatus(fid, maxAttempts = 30) {
  for (let i = 0; i < maxAttempts; i++) {
    const res = await fetch(`${import.meta.env.VITE_API_URL ?? ""}/api/files/${fid}`);
    if (!res.ok) throw new Error(`Failed to check file status (${res.status})`);
    const data = await res.json();
    if (data.status === "ready") return data;
    if (data.status === "failed") throw new Error(data.error || "File parsing failed");
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error("File processing timed out");
}
```

- [ ] **Step 2: Update `sendChat` to send JSON `{ prompt, file_ids }` to `/api/chat`**

Change request method to `POST /api/chat` with `Content-Type: application/json` and body `JSON.stringify({ prompt, file_ids: fileIds })`.
Parse SSE events (`meta`, `token`, `status`, `error`, `done`).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api.js
git commit -m "feat(frontend): implement uploadFile, pollFileStatus, and JSON sendChat"
```

---

### Task 6: Wire Upload Lifecycle in `App.jsx` and `AttachmentTray.jsx`

**Files:**
- Modify: `frontend/src/App.jsx`
- Modify: `frontend/src/components/AttachmentTray.jsx`

- [ ] **Step 1: Automatically upload files when added to queue**

When `addFiles` is called:
1. Append file objects to queue with state `"uploading"`.
2. Invoke `uploadFile(file, onProgress)`.
3. Upon upload completion, switch state to `"processing"` and call `pollFileStatus(fid)`.
4. Upon ready, mark item as `"ready"` and store `fid` on the file entry.
5. If upload fails, set state to `"failed"` and display notice.

- [ ] **Step 2: Send ready `file_ids` on chat submit**

Collect `fid`s from `queue` (or session's uploaded file records) and pass them as `fileIds` to `sendChat`.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/App.jsx frontend/src/components/AttachmentTray.jsx
git commit -m "feat(frontend): wire two-phase file upload lifecycle in App and AttachmentTray"
```

---

### Task 7: Display Routing Badges and Collapsible Evidence in `Feed.jsx`

**Files:**
- Modify: `frontend/src/components/Feed.jsx`
- Modify: `frontend/src/chat.css`

- [ ] **Step 1: Store `routing` and `evidence` on assistant messages**

In `App.jsx`, when `onEvent("meta", d)` arrives:
Update the active assistant message with `routing: d.routing` and `evidence: d.evidence`.

- [ ] **Step 2: Render `<RoutingBadges />` and `<EvidenceList />` in `Feed.jsx`**

For assistant messages:
- Render `<RoutingBadges routing={m.routing} />` at the top of the message if `m.routing` exists.
- Render `<ReactMarkdown>{m.text}</ReactMarkdown>`.
- If `m.evidence && m.evidence.length > 0`, render:
  ```jsx
  <details className="evidence-accordion">
    <summary>Inspect Evidence ({m.evidence.length} sources)</summary>
    <EvidenceList evidence={m.evidence} />
  </details>
  ```

- [ ] **Step 3: Add CSS for evidence accordion in `chat.css`**

Add styles for `.evidence-accordion`, `.evidence-accordion summary`, and evidence cards within the chat feed.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/Feed.jsx frontend/src/chat.css
git commit -m "feat(frontend): render RoutingBadges and EvidenceList accordion in Feed"
```

---

### Task 8: Verification & Smoke Test

**Files:**
- Test: backend pytest suite
- Test: frontend build

- [ ] **Step 1: Run full backend test suite**

Run: `pytest tests/`
Expected: 509+ passed

- [ ] **Step 2: Run frontend production build**

Run: `cd frontend && npm run build`
Expected: Build succeeds without errors

- [ ] **Step 3: Commit any final polishing**

```bash
git commit --allow-empty -m "chore: complete frontend & chromadb integration verification"
```
