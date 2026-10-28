# 04 - API reference

Everything the HTTP layer exposes, with request bodies, responses, status
codes, and curl examples.

Two FastAPI applications exist:

| App | Import path | What it serves |
|---|---|---|
| Main app (use this) | `api.main:app` | `/health`, `/query`, `/index`, `/api/*`, `/analytics/*` |
| Legacy app | `backend.app.main:app` | `/health` and a multipart `/index` only |

Start the main app:

```bash
uvicorn api.main:app --reload --port 8000
```

Interactive docs: <http://127.0.0.1:8000/docs> and
<http://127.0.0.1:8000/openapi.json>. The OpenAPI schema is generated from
`api/schemas.py` and the route signatures, so it is the quickest way to see the
current request/response shapes.

---

## Conventions

### Base URL

```text
http://127.0.0.1:8000
```

### Content types

- JSON endpoints: `Content-Type: application/json`
- Multipart endpoints: `Content-Type: multipart/form-data`
- Chat stream (`/api/chat`): `text/event-stream`

### Error shape

FastAPI's standard shape is used:

```json
{"detail": "human-readable message"}
```

Validation errors (422) use a list:

```json
{
  "detail": [
    {
      "type": "string_too_short",
      "loc": ["body", "query"],
      "msg": "String should have at least 1 character",
      "input": ""
    }
  ]
}
```

The React `query.js` helper understands both shapes, plus an
`{error: {message: ...}}` shape, and turns them into a displayable string.

### CORS

`CORS_ORIGINS` controls browser access:

- Empty or `*`: every origin is allowed, with `Access-Control-Allow-Origin: *`
  and **credentials disabled** (the spec forbids `*` plus credentials).
- Comma-separated list: only those origins are echoed, with credentials
  allowed. Whitespace is trimmed. Any `*` item collapses the whole list to
  wildcard mode.

For local Vite development, the simplest setup is to use the Vite proxy and
leave CORS alone. If you point the browser directly at port 8000 with
`VITE_API_URL`, set `CORS_ORIGINS=http://localhost:5173` (and the same for
`127.0.0.1:5173` if you use that host).

---

## `GET /health`

Liveness plus the live embedder, collection, and LLM status. No parameters.

```bash
curl http://127.0.0.1:8000/health
```

```json
{
  "status": "ok",
  "version": "0.1.0",
  "chroma_path": ".graft/chroma",
  "collection": "graft_tree_nodes_all-minilm-l6-v2_384",
  "embedding_provider": "sentence-transformers",
  "embedding_dim": 384,
  "llm_enabled": false,
  "llm_in_use": false,
  "llm_active": false,
  "llm_available": false,
  "llm_used": false,
  "llm_provider": null
}
```

Notes:

- `embedding_provider` is the provider **actually in use**. If the model failed
  to load, this is `"stub"` and `embedding_dim` is `16`.
- The five LLM booleans are aliases kept for different consumers; they report
  whether an LLM client was actually built, not the raw `LLM_ENABLED` setting.
- `status` is always `"ok"` if the process can answer. It does not probe
  Chroma or the LLM.
- This endpoint calls `_build_llm_client()`, which attempts to construct a
  provider client when `LLM_ENABLED=true`. Missing keys/SDKs are treated as
  "LLM not in use", not as a health failure.

---

## `POST /query`

The main pipeline: route, retrieve, run activated modules, generate, log.

### Request

```json
{
  "query": "Compare TLS 1.2 and TLS 1.3 key exchange.",
  "request_id": "optional-client-id",
  "retrieval_depth": 2,
  "filters": {"document_id": "tls_note"}
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `query` | string | yes | Must be non-empty after trimming |
| `request_id` | string | no | Generated as `req_<10 hex>` when omitted |
| `retrieval_depth` | integer >= 0 | no | Overrides the router's depth; retrieval clamps to 2 |
| `filters` | object | no | Extra Chroma metadata equality filters |

### Response `200`

```json
{
  "request_id": "req_1a2b3c4d5e",
  "answer": "TLS 1.2 allows static RSA key exchange. TLS 1.3 removes static RSA ...",
  "evidence": [
    {
      "source": "tls_note.txt",
      "page": null,
      "chunk_id": "13c2399fd4a43867a637b09c84afb3b61ff2ff5e4eef937724d11615b84ab3d5",
      "node_id": "13c2399fd4a43867a637b09c84afb3b61ff2ff5e4eef937724d11615b84ab3d5",
      "level": 0,
      "score": 0.81,
      "text": "TLS 1.2 allows static RSA key exchange..."
    }
  ],
  "routing": {
    "complexity": "complex",
    "confidence": 0.867,
    "retrieval_depth": 2,
    "activated_modules": ["fact_lookup", "numeric_reasoning", "multi_hop"]
  },
  "latency_ms": 42.17
}
```

Field notes:

- `answer` is a string, always present. With no context it is a fallback
  message, not an error.
- `evidence` is deduplicated by `chunk_id`/`node_id`; `text` is truncated to
  600 characters at the API boundary.
- `routing` is the router's `to_dict()` output. `confidence` is 0..1.
- `latency_ms` covers route + retrieval + modules + generation. It excludes the
  SQLite log insert and network time before the request reached the server.

### Status codes / errors

| Status | When |
|---|---|
| 200 | Normal answer, including an empty collection |
| 422 | Empty query, or body fails validation |
| 500 | Retrieval, module, or generation raised |

Error examples:

```json
{"detail": "query must not be empty"}
{"detail": "retrieval failed: ..."}
{"detail": "module multi_hop failed: ..."}
{"detail": "generation failed: ..."}
```

A logging failure does not change the status; it is logged as a warning.

### Examples

Simple query:

```bash
curl -X POST http://127.0.0.1:8000/query \
  -H 'Content-Type: application/json' \
  -d '{"query":"What is the capital of France?"}'
```

Force depth 2 for an ablation:

```bash
curl -X POST http://127.0.0.1:8000/query \
  -H 'Content-Type: application/json' \
  -d '{"query":"Summarize the document","retrieval_depth":2}'
```

Restrict to one document:

```bash
curl -X POST http://127.0.0.1:8000/query \
  -H 'Content-Type: application/json' \
  -d '{"query":"What changed?","filters":{"document_id":"tls_note"}}'
```

Use your own request ID:

```bash
curl -X POST http://127.0.0.1:8000/query \
  -H 'Content-Type: application/json' \
  -d '{"query":"What is TLS?","request_id":"demo-001"}'
```

---

## `POST /index`

Ingest a document into the tree and vector store. The endpoint supports two
content types with different payloads and responses.

### JSON branch (raw text)

```bash
curl -X POST http://127.0.0.1:8000/index \
  -H 'Content-Type: application/json' \
  -d '{
    "text": "TLS 1.2 allows static RSA. TLS 1.3 removes it.",
    "document_id": "tls_note",
    "source": "tls_note.txt"
  }'
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `text` | string | yes | Non-empty |
| `document_id` | string | yes | Non-empty; used in node IDs and metadata |
| `source` | string | no | Provenance stored on nodes |

Response `200`:

```json
{
  "document_id": "tls_note",
  "nodes_indexed": 2,
  "nodes": [
    {
      "node_id": "13c2399fd4a43867a637b09c84afb3b61ff2ff5e4eef937724d11615b84ab3d5",
      "text": "...",
      "level": 0,
      "parent_id": "tls_note_summary_L1_0000",
      "child_ids": [],
      "metadata": {"document_id": "tls_note", "chunk_index": 0, "source": "tls_note.txt"}
    }
  ]
}
```

Only the first 20 nodes are returned, even if the tree is larger. The response
is a preview, not the full tree.

Errors: 422 for invalid JSON, non-object JSON, empty text, or empty
`document_id`.

### Multipart branch (PDF/DOCX)

```bash
curl -X POST http://127.0.0.1:8000/index \
  -F "files=@data/sample_docs/1705.pdf"
```

- Field names: any file-like field is captured; `files` and `file` are the
  documented aliases. Multiple files are supported.
- Extensions: `.pdf`, `.docx`. A `.txt` is rejected here (use
  `POST /api/files` for TXT, or the JSON branch).
- Each file is parsed in a temporary directory, then chunked with upload-
  specific settings: 200-token chunks, 20-token overlap, cluster size 4.
- Document IDs come from sanitised file stems; duplicates get `_1`, `_2`.
- Empty files and files with no extractable text return 422.

Response `200`:

```json
{
  "status": "success",
  "document_ids": ["1705"],
  "total_chunks": 42,
  "tree_stats": {
    "total_nodes": 55,
    "levels": {"0_leaf": 42, "1_summary": 12, "2_root": 1}
  }
}
```

Errors:

| Status | When |
|---|---|
| 400 | Unsupported extension |
| 422 | No files, empty file, no extractable text, no nodes |
| 500 | Unexpected parse or indexing failure |

### Re-indexing caveat

Chroma upserts by `chunk_id`. Re-indexing the same `document_id` replaces
matching nodes but does not delete stale nodes from a previous version. If the
document got shorter, old chunks remain queryable. Use a new `document_id` or
clear the collection.

---

## `POST /api/files`

Upload one file for the background indexing pipeline. This is the first step of
the chat upload flow.

```bash
curl -X POST http://127.0.0.1:8000/api/files \
  -F "file=@data/sample_docs/1705.pdf"
```

Constraints:

| Constraint | Value |
|---|---|
| Field name | `file` |
| Max size | 10 MB (`MAX_UPLOAD_MB`) |
| Allowed extensions | `.pdf`, `.docx`, `.txt` |
| Allowed MIME types | `application/pdf`, the DOCX MIME, `text/plain` |
| Content sniffing | PDF must start with `%PDF`; DOCX with `PK`; TXT must have no NUL bytes |

Response `201`:

```json
{
  "id": "056c6005ed3a4c4199edb317bd8396d6",
  "name": "1705.pdf",
  "size": 563200,
  "path": "data/uploads/056c6005ed3a4c4199edb317bd8396d6.pdf",
  "status": "uploaded",
  "error": null
}
```

The file is saved under a generated UUID name, never the client's filename. A
background task then:

1. sets `status` to `parsing`;
2. parses and indexes the document with `document_id = <file id>`;
3. sets `status` to `ready`, or `failed` with an `error` message.

Records live in the in-memory `FILES` dict. They disappear on restart. The
files themselves stay in `data/uploads/`.

Status codes:

| Status | When |
|---|---|
| 201 | Accepted and stored |
| 413 | Larger than 10 MB |
| 415 | Unsupported extension/MIME, empty, or content does not match the type |
| 422 | Missing `file` field (FastAPI validation) |
| 500 | Unexpected filesystem error |

> The returned `path` is a server filesystem path. Do not treat it as a public
> URL.

## `GET /api/files/{fid}`

Return the in-memory record for one upload.

```bash
curl http://127.0.0.1:8000/api/files/056c6005ed3a4c4199edb317bd8396d6
```

Response `200` is the same record as above with the current `status`.
Response `404`: `{"detail": "Unknown file"}`. The ID is the `id` from the
upload response, not the original filename.

---

## `POST /api/chat`

The working SSE chat endpoint. It accepts JSON, not multipart.

### Request

```json
{
  "prompt": "Summarize the uploaded document.",
  "file_ids": ["056c6005ed3a4c4199edb317bd8396d6"]
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `prompt` | string | yes | Empty is allowed by the schema but pointless |
| `file_ids` | list[string] | no | Defaults to `[]`; each must exist in `FILES` |

### Response

`Content-Type: text/event-stream`. Each event is:

```text
event: <name>
data: <JSON>

```

The final blank line is required. Event names currently emitted by
`api/uploads.py`:

| Event | Data | Meaning |
|---|---|---|
| `status` | `{"file": "<fid>", "status": "uploaded"\|"parsing", "state": "uploaded"\|"parsing"}` | Waiting for processing; `status` and `state` carry the same value |
| `meta` | `{"routing": {...}, "evidence": [...]}` | Pipeline metadata after a successful query |
| `token` | `{"t": "word ", "text": "word "}` | One simulated token; both keys carry the same string |
| `error` | `{"message": "..."}` | Failure; the stream then ends |
| `done` | `{}` | Stream complete; only sent after a successful stream |

### Behavior

`run_query()` calls `api.query_runner.execute_query()`, so chat uses the same
route -> retrieve -> modules -> generate path as `POST /query`. The only
difference is the filter: one `file_id` becomes
`{"document_id": "<id>"}`, several become `{"document_id": {"$in": [...]}}`.

Files must already be uploaded through `POST /api/files`; chat does not accept
file parts. A successful stream is:

```text
event: meta
data: {"routing": {...}, "evidence": [...]}

event: token
data: {"t": "TLS ", "text": "TLS "}

event: done
data: {}

```

Failure cases:

- A `file_id` is not in `FILES` or is `failed`: the error message is
  `Could not process document: unknown file` or the stored failure reason.
- The query pipeline raises: the error message is
  `Something went wrong while processing the chat`.
- In both cases the stream ends **without** a `done` event.

See [06 - Frontend/backend integration](06-frontend-backend-integration.md) for
the full browser flow.

### Manual test

```bash
curl -N -X POST http://127.0.0.1:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"hello","file_ids":[]}'
```

`-N` disables curl buffering so events appear as they arrive.

---

## Analytics

All three endpoints are read-only and back onto
`db/graft_logs.db`. They call `init_db()` first, so they work on a fresh
install and return empty results rather than a 500.

### `GET /analytics/queries`

```bash
curl 'http://127.0.0.1:8000/analytics/queries?limit=50'
```

| Query param | Default | Range |
|---|---|---|
| `limit` | 50 | 1..1000 |

Response:

```json
{
  "queries": [
    {
      "query_text": "What is TLS?",
      "latency_ms": 42.17,
      "modules_fired": ["fact_lookup"],
      "retrieval_depth": "0",
      "timestamp": "2026-10-28T12:00:00+00:00"
    }
  ],
  "count": 1,
  "limit": 50
}
```

Newest first. `modules_fired` is normalized from stored JSON text to a list.
`retrieval_depth` is the stored text value (for example `"0"`), not an integer.

### `GET /analytics/summary`

```bash
curl http://127.0.0.1:8000/analytics/summary
```

```json
{
  "total_queries": 12,
  "avg_latency_ms": 38.4,
  "module_usage_counts": {
    "fact_lookup": 12,
    "multi_hop": 4,
    "contradiction_detection": 2
  }
}
```

With no rows, `total_queries` is 0 and `avg_latency_ms` is 0.0.

### `GET /analytics/slow`

```bash
curl 'http://127.0.0.1:8000/analytics/slow?threshold_ms=100&limit=20'
```

| Query param | Default | Range |
|---|---|---|
| `threshold_ms` | 1000 | >= 0 |
| `limit` | 100 | 1..1000 |

Returns `{queries, count, threshold_ms}`. Each entry has the same shape as
`/analytics/queries`; there is no `limit` field in this response.

---

## Legacy app endpoints

Start it with:

```bash
uvicorn backend.app.main:app --reload --port 8000
```

### `GET /health`

```json
{"status": "ok"}
```

No version, embedder, or LLM fields.

### `POST /index`

Multipart upload through `api/routes/indexing.py`. Same PDF/DOCX extension
check as the main app, but it is **not** the same index: it uses the default
stub embedder (16 dimensions), the base `graft_tree_nodes` collection, and the
400/50/3 chunking defaults. It builds its own `ChromaVectorStore` and does not
use the shared cache. Documents uploaded here are not visible to `api.main`'s
`/query` unless you align the embedder and collection deliberately.

```bash
curl -X POST http://127.0.0.1:8000/index -F "files=@document.pdf"
```

There is no `/query`, `/api/files`, `/api/chat`, or `/analytics` in this app.

---

## Routes that do not exist

| Path | Status |
|---|---|
| `POST /baseline/query` | Mentioned in `api/main.py`'s docstring, not implemented. Use `baseline.run_baseline()` or the benchmark CLI. |
| `POST /chat` (no prefix) | Only in the unmounted `backend/api/uploads.py`. The mounted route is `/api/chat`. |
| `GET /chat` | Not implemented. |
| `POST /query/stream` | Not implemented. `/query` is a normal JSON response. |

---

## curl cheat sheet

```bash
# Health
curl http://127.0.0.1:8000/health

# Index raw text
curl -X POST http://127.0.0.1:8000/index \
  -H 'Content-Type: application/json' \
  -d '{"text":"...","document_id":"doc_001","source":"doc.txt"}'

# Index a PDF
curl -X POST http://127.0.0.1:8000/index -F "files=@document.pdf"

# Query
curl -X POST http://127.0.0.1:8000/query \
  -H 'Content-Type: application/json' \
  -d '{"query":"Compare the documents"}'

# Upload for chat
curl -X POST http://127.0.0.1:8000/api/files -F "file=@document.pdf"

# Poll upload status
curl http://127.0.0.1:8000/api/files/<file-id>

# Chat stream (upload a file first, then pass its id)
curl -N -X POST http://127.0.0.1:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"hello","file_ids":[]}'

# Analytics
curl 'http://127.0.0.1:8000/analytics/queries?limit=10'
curl http://127.0.0.1:8000/analytics/summary
curl 'http://127.0.0.1:8000/analytics/slow?threshold_ms=100'
```

---

Next: [05 - Frontend guide](05-frontend-guide.md)
