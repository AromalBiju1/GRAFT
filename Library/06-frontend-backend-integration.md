# 06 - Frontend/backend integration

How the React app talks to FastAPI: the request sequence, the exact payloads,
the SSE contract, and the failure points.

The short version: the flow is **two-phase**. Upload a file and wait for it to
be indexed, then send a JSON chat request that references the server-side file
IDs. Text-only chat skips the upload phase.

---

## 1. The three requests

```text
Browser                                   FastAPI
  |                                          |
  |  POST /api/files  (multipart "file")     |
  |----------------------------------------->|  201 {id, name, size, status:"uploaded"}
  |                                          |  background: parse -> index -> ready
  |  GET /api/files/{id}  (poll)             |
  |----------------------------------------->|  200 {status:"parsing"|"ready"|"failed"}
  |                                          |
  |  POST /api/chat  (JSON)                  |
  |  {"prompt": "...", "file_ids": ["..."]}  |
  |----------------------------------------->|  SSE: meta, token..., done
  |<-----------------------------------------|
```

There is no single multipart chat request in the mounted app. The old
`backend/api/uploads.py` prototype used that shape, but it is not mounted.

---

## 2. Dev topology

Two processes:

```text
Terminal 1: uvicorn api.main:app --reload --port 8000
Terminal 2: cd frontend && npm run dev
```

The browser talks to Vite on `http://localhost:5173`. Vite proxies:

| Prefix | Target | Used by |
|---|---|---|
| `/query` | `http://127.0.0.1:8000` | the optional `submitQuery` client |
| `/api` | `http://127.0.0.1:8000` | upload, poll, chat |
| `/health` | `http://127.0.0.1:8000` | manual checks |
| `/index` | `http://127.0.0.1:8000` | manual JSON/multipart indexing |

`/analytics` is not proxied; the mounted UI does not call it. Because the
browser uses the Vite origin, these requests are same-origin and no CORS
preflight is involved in development.

### Direct API URL instead of the proxy

`frontend/src/api.js` builds every URL from:

```js
const BASE_URL = import.meta.env.VITE_API_URL ?? "";
```

If `VITE_API_URL` is empty, requests are relative and go through Vite. If it is
set, for example `VITE_API_URL=http://127.0.0.1:8000`, the browser calls the API
origin directly. That requires the API's `CORS_ORIGINS` to include
`http://localhost:5173`, and it must be set before Vite starts.

---

## 3. Transport matrix

| Call | Method and path | Request body | Response |
|---|---|---|---|
| Upload | `POST /api/files` | `multipart/form-data`, field `file` | JSON record, 201 |
| Poll | `GET /api/files/{id}` | none | JSON record |
| Chat | `POST /api/chat` | JSON `{"prompt": str, "file_ids": [str]}` | `text/event-stream` |
| Direct query | `POST /query` | JSON `{"query": str, ...}` | JSON |

The upload and chat endpoints are both mounted under the `/api` prefix from
`api/uploads.py`. The chat endpoint does **not** accept multipart and does
**not** accept file parts.

---

## 4. Frontend implementation

### 4.1 Upload and poll (`frontend/src/api.js`)

`uploadFile(file, onProgress)`:

- creates `FormData` and appends the file under `file`;
- POSTs to `` `${BASE_URL}/api/files` ``;
- reports upload progress from `xhr.upload.onprogress`;
- resolves the parsed JSON record, or rejects with `detail` from an error
  response.

`pollFileStatus(fid, maxAttempts = 60, intervalMs = 500)`:

- calls `GET /api/files/{fid}`;
- returns the record as soon as `status === "ready"`;
- throws the stored `error` on `status === "failed"`;
- throws `Document processing timed out` after 60 attempts (30 seconds).

The server indexes in a background task, so polling is required. A 201 from
the upload only means the file is stored, not searchable.

### 4.2 Chat (`frontend/src/api.js`)

`sendChat({prompt, fileIds, onEvent, onError, onDone})`:

- uses `XMLHttpRequest`, because it needs incremental `responseText` plus
  cancellation;
- sets `Content-Type: application/json`;
- POSTs to `` `${BASE_URL}/api/chat` `` with
  `JSON.stringify({prompt, file_ids: fileIds})`;
- parses SSE frames from the growing response text: split on blank lines, read
  `event:` and `data:`, call `onEvent(name, JSON.parse(data))`;
- ignores malformed frames;
- on HTTP >= 400, reads `detail` and calls `onError`;
- on network failure, reports "Can't reach the server...";
- calls `onDone` on load, error, and abort.

`onDone` is a transport callback, not the SSE `done` event. It runs whenever
the XHR finishes, including after an SSE `error` frame.

### 4.3 App state (`frontend/src/App.jsx`)

- A session is `{id, title, messages, files, fileIds}`.
- `addFiles()` validates, uploads, polls, then appends the server ID to
  `session.fileIds`.
- `send()` combines `session.fileIds` with any ready IDs still in the queue and
  sends one chat request.
- `token` appends `d.text || d.t`.
- `meta` stores `routing` and `evidence` on the assistant message.
- `status` updates a queued attachment using `d.state || d.status`.
- `error` marks the assistant message as an error.
- `onDone` clears `busy` and the abort handle; it does not clear ready
  attachments.

The UI never sends conversation history. Each chat request is one independent
query, optionally filtered to the session's files.

---

## 5. Backend implementation

### 5.1 `POST /api/files`

`api/uploads.py`:

- accepts one `UploadFile` under the field name `file`;
- allows `.pdf`, `.docx`, `.txt` and three MIME types;
- enforces 10 MB;
- writes to `data/uploads/<uuid><ext>` using a generated name, never the
  client filename;
- sniffs the first bytes (`%PDF`, `PK` for DOCX, no NUL for TXT);
- creates an in-memory record `{id, name, size, path, status, error}`;
- schedules `_process(fid)` as a FastAPI background task;
- returns 201 with the record.

`_process` parses with `indexing.ingest.ingest_document`, joins the chunk text,
and calls `indexing.pipeline.index_document` with `document_id=fid`. That
indexes into the normal embedder-qualified collection, so the document is
visible to both `/query` and `/api/chat`.

### 5.2 `GET /api/files/{fid}`

Returns the record or 404 `{"detail": "Unknown file"}`. Status values:

| Status | Meaning |
|---|---|
| `uploaded` | stored, background task not started yet |
| `parsing` | parsing/chunking/indexing |
| `ready` | indexed and queryable |
| `failed` | error stored in the `error` field |

### 5.3 `POST /api/chat`

Request model:

```json
{"prompt": "Summarize the document.", "file_ids": ["<server-id>"]}
```

`file_ids` defaults to `[]`. An empty list queries the whole collection.

Before running the query, the handler waits for each referenced file:

- while the status is `uploaded` or `parsing`, it emits a `status` event every
  0.5 seconds;
- if a file is missing or `failed`, it emits `error` with
  `Could not process document: <reason>` and ends the stream (no `done`);
- otherwise it calls `run_query()`.

`run_query()` delegates to `api.query_runner.execute_query()` with:

- one ID -> `{"document_id": "<id>"}`;
- several IDs -> `{"document_id": {"$in": ["<id>", ...]}}`;
- no IDs -> no filter.

That is the same route -> retrieve -> modules -> generate pipeline as
`POST /query`, including LLM selection and SQLite logging.

### 5.4 SSE contract

Every frame is:

```text
event: <name>
data: <JSON>

```

| Event | Data | When |
|---|---|---|
| `status` | `{"file": "<fid>", "status": "...", "state": "..."}` | while waiting for a file |
| `meta` | `{"routing": {...}, "evidence": [...]}` | once, before tokens |
| `token` | `{"t": "...", "text": "..."}` | per word; both keys are identical |
| `error` | `{"message": "..."}` | failure; stream ends, no `done` |
| `done` | `{}` | successful end |

The duplicate keys (`t`/`text`, `status`/`state`) exist so older and newer
clients can read the same stream. New code may use either; the frontend reads
`d.text || d.t` and `d.state || d.status`.

The simulated token stream is not model token streaming. The backend splits the
finished answer on spaces and sends one event per word with a 10 ms delay.

---

## 6. Failure modes

| Symptom | Cause | Check |
|---|---|---|
| `POST /api/files` 415 | Bad extension/MIME, empty file, or content sniff mismatch | `file.name`, `file.type`, first bytes |
| `POST /api/files` 413 | File > 10 MB | `MAX_UPLOAD_MB` and client validation |
| Poll times out | Indexing is slow or the background task crashed | API logs, `GET /api/files/{id}` error |
| Chat 404 | Wrong path or missing `/api` proxy | Network tab; Vite config |
| Chat 422 | JSON body missing `prompt` or wrong shape | Request payload |
| Chat starts but no tokens | `error` frame arrived; stream ended | Network tab SSE frames; API logs |
| Answer ignores uploaded file | `file_ids` empty or wrong ID | Use the `id` from `POST /api/files`, not the filename |
| CORS error | Direct `VITE_API_URL`, origin not allowed | `CORS_ORIGINS` |
| Upload record vanished | API restarted; `FILES` is in memory | Re-upload; vectors remain in Chroma |

When the API returns an SSE `error`, the HTTP status is still 200 because
headers were already sent. The frontend must inspect the event, not just the
status code.

---

## 7. CORS and production

In development, use the Vite proxy and keep `VITE_API_URL` empty. In
production, either:

1. serve the built frontend and reverse-proxy `/api` and `/query` to FastAPI
   from the same origin, or
2. serve them on different origins and set `CORS_ORIGINS` to the exact
   frontend origin.

Wildcard `CORS_ORIGINS` is allowed, but the backend disables credentials in
that mode. The chat flow does not use cookies, so wildcard works, but an
explicit origin list is safer.

Minimal nginx shape:

```nginx
server {
    listen 80;
    root /srv/graft/frontend/dist;
    index index.html;

    location / {
        try_files $uri /index.html;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_buffering off;          # required for SSE
    }

    location /query {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
    }

    location /health {
        proxy_pass http://127.0.0.1:8000;
    }
}
```

`proxy_buffering off` matters for `/api/chat`; without it nginx may hold the
whole stream until the response ends. Also disable buffering in any CDN in
front of the API.

FastAPI does not serve `frontend/dist` itself.

---

## 8. Manual verification

```bash
# 1. API alive
curl -s http://127.0.0.1:8000/health

# 2. Upload a file
curl -s -X POST http://127.0.0.1:8000/api/files \
  -F "file=@data/sample_docs/sample.pdf"
# copy the "id" and "status"

# 3. Poll until ready
curl -s http://127.0.0.1:8000/api/files/<id>

# 4. Chat with that file
curl -N -X POST http://127.0.0.1:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Summarize the document.","file_ids":["<id>"]}'

# 5. Text-only chat (whole collection)
curl -N -X POST http://127.0.0.1:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"What is TLS?","file_ids":[]}'

# 6. The same pipeline without SSE
curl -s -X POST http://127.0.0.1:8000/query \
  -H 'Content-Type: application/json' \
  -d '{"query":"What is TLS?"}'
```

`curl -N` disables curl buffering so frames appear as they arrive.

Then repeat through the browser at <http://localhost:5173>. The Network tab
should show `POST /api/files`, repeated `GET /api/files/{id}`, and
`POST /api/chat` with `Content-Type: application/json`.

---

## 9. Tests

Current coverage:

- `frontend/tests/query.test.js` covers `submitQuery` and error normalization.
- Backend tests cover `/api/files` (`tests/test_upload.py`) and `/api/chat`
  including streaming and file filtering (`tests/test_chat_endpoint.py`).
  `execute_query()` is exercised through both endpoints.

Not covered by an automated frontend test:

- `uploadFile` progress and error handling;
- `pollFileStatus` timeout and failure;
- `sendChat` SSE parsing and abort;
- the two-phase `App` flow.

If you change the transport, add tests for those four areas. `query.test.js`
shows the Node test style; mock `XMLHttpRequest` or `fetch` rather than
starting a real server.

---

## 10. What not to do

- Do not send multipart to `/api/chat`; it expects JSON.
- Do not put the original filename in `file_ids`; use the server `id`.
- Do not send a chat request before the upload is `ready`.
- Do not assume `done` follows `error`; it does not.
- Do not rely on `VITE_API_URL` changes taking effect without restarting Vite.
- Do not proxy SSE through a buffering layer.
- Do not treat the 200 status of an SSE response as proof of success.

---

Next: [07 - Testing and CI](07-testing-and-ci.md)
