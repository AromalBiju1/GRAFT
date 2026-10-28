# 01 - Getting started

This chapter takes you from a fresh clone to a working query, then shows how to
start the React frontend and the benchmark. Every command is copy-pasteable.

If you hit an error, [08 - Troubleshooting](08-troubleshooting.md) has the
common ones. If you want to understand what just happened, read
[02 - Architecture](02-architecture-and-data-flow.md) afterwards.

---

## Quick start

Four terminals-worth of commands, compressed:

```bash
# 1. Install
git clone https://github.com/AromalBiju1/GRAFT
cd GRAFT
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env

# 2. Start the API (terminal 1)
uvicorn api.main:app --reload --port 8000

# 3. Index a document, then ask a question (terminal 2)
curl -X POST http://127.0.0.1:8000/index \
  -H 'Content-Type: application/json' \
  -d '{"text":"TLS 1.3 removes static RSA key exchange. TLS 1.2 still permits it.","document_id":"tls_note","source":"tls_note.txt"}'

curl -X POST http://127.0.0.1:8000/query \
  -H 'Content-Type: application/json' \
  -d '{"query":"What changed between TLS 1.2 and TLS 1.3?"}'

# 4. Optional: start the React app (terminal 3)
cd frontend
npm install
npm run dev
```

The first query downloads/loads the `all-MiniLM-L6-v2` model and can take
10-20 seconds. Later queries are fast. `/health` tells you whether the real
model or the fallback stub is active.

---

## Prerequisites

| Tool | Version | Why |
|---|---|---|
| Python | 3.10+ (3.11 is what CI uses) | Backend and pipeline |
| pip / venv | Bundled with Python | Isolated dependencies |
| Node.js | `^20.19.0` or `>=22.12.0` | Vite 7 and React 19 |
| npm | Bundled with Node | Frontend dependencies |
| Git | Any recent | Clone and branch |

Optional extras:

- `pip install -e ".[nli]"` for transformer-based contradiction detection.
- `pip install -e ".[llm]"` for the Gemini/OpenAI SDKs. Groq, Ollama, and
  llama.cpp use the OpenAI-compatible HTTP path through `httpx`, which is
  already a core dependency.
- `pip install -e ".[dev]"` for pytest, ruff, and mypy.

`requirements.txt` contains the runtime set. `pyproject.toml` contains the same
set plus optional groups. If you change one, change the other. CI installs
`requirements.txt`.

---

## 1. Clone and create a virtual environment

```bash
git clone https://github.com/AromalBiju1/GRAFT
cd GRAFT

python -m venv .venv
source .venv/bin/activate          # macOS / Linux
# .venv\Scripts\activate           # Windows PowerShell
```

Install one of:

```bash
# Recommended for contributors: editable install + dev tools
pip install -e ".[dev]"

# Minimal runtime only
pip install -r requirements.txt
```

If you use `pip install -r requirements.txt` only, the `graft-api` and
`graft-index` console scripts are not installed. Use `uvicorn api.main:app` and
call the indexing functions directly from Python (see below).

---

## 2. Create the environment file

```bash
cp .env.example .env
```

The defaults are safe for local use:

- LLM synthesis is **off** (`LLM_ENABLED=false`; see the note below).
  Generation stays deterministic and makes no network calls.
- The embedder is `sentence-transformers` with `all-MiniLM-L6-v2`.
- Chroma lives in `.graft/chroma`.
- The API listens on `127.0.0.1:8000`.

You do **not** need an API key to run the pipeline. You only need one if you
want LLM-written answers.

### Environment variable names

`config.py` uses `pydantic-settings`. Variable names are the field names,
case-insensitively. There is **no `GRAFT_` prefix** on `Settings` fields.
`GRAFT_LOG_LEVEL` is the one exception: it is read directly by
`logging_config.py`, not by `Settings`.

| Variable | Default | Meaning |
|---|---|---|
| `LLM_PROVIDER` | `gemini` | `gemini`, `openai`, `groq`, `ollama`, `llamacpp`, or `local` |
| `LLM_ENABLED` | `false` | Build an LLM client in `/query` |
| `LLM_TIMEOUT_SECONDS` | `120` | Timeout for Groq/Ollama/llama.cpp |
| `GEMINI_API_KEY` / `OPENAI_API_KEY` | unset | Provider credentials |
| `GROQ_API_KEY` | unset | Groq credential |
| `GEMINI_MODEL` / `OPENAI_MODEL` | `gemini-2.5-flash` / `gpt-4o-mini` | Model names |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | Groq model |
| `OLLAMA_BASE_URL` / `OLLAMA_MODEL` | `http://localhost:11434/v1` / `gpt-oss:120b` | Local Ollama |
| `LLAMACPP_BASE_URL` / `LLAMACPP_MODEL` | `http://localhost:8080/v1` / `gpt-oss-120b` | Local llama.cpp |
| `EMBEDDING_PROVIDER` | `sentence-transformers` | Or `stub` for offline runs |
| `EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | Sentence-transformers model |
| `CHROMA_PATH` | `.graft/chroma` | Chroma persistence directory |
| `CHROMA_COLLECTION` | `graft_tree_nodes` | Base name; embedder suffix is appended |
| `DB_PATH` | `db/graft_logs.db` | Declared, but `db/logger.py` currently uses a hard-coded path |
| `API_HOST` | `127.0.0.1` | Uvicorn bind host |
| `API_PORT` | `8000` | Uvicorn bind port |
| `API_RELOAD` | `true` | Used by `graft-api` (and by `main()` when called) |
| `CORS_ORIGINS` | empty | Comma-separated browser origins; empty or `*` = allow all |
| `WARMUP_ON_STARTUP` | `true` | Load embedder and open Chroma before serving |
| `ROUTER_THRESHOLD_SIMPLE` | `0.35` | Below this score = `simple` |
| `ROUTER_THRESHOLD_COMPLEX` | `0.65` | Above this score = `complex` |
| `DEFAULT_RETRIEVAL_DEPTH` | `1` | Declared default (the router still maps by tier) |
| `GRAFT_LOG_LEVEL` | `info` | `debug`, `info`, `warning`, `error` |

> **Correction to `docs/setup.md`:** that file lists `GRAFT_CHROMA_PATH`,
> `GRAFT_DB_PATH`, and `GRAFT_API_HOST/PORT`. Those names do not work because
> `Settings` has no `env_prefix`. Use `CHROMA_PATH`, `DB_PATH`, `API_HOST`, and
> `API_PORT`. This Library and `.env.example` use the names that actually work.

> **LLM enabled flag:** `Settings.llm_enabled` defaults to `False`. Setting
> `LLM_PROVIDER=gemini` alone does not enable it; set `LLM_ENABLED=true` as well,
> plus the matching API key. Without that, `/query` uses deterministic synthesis.

> **Provider notes:** Groq, Ollama, and llama.cpp are called through the
> OpenAI-compatible `/chat/completions` HTTP API, so they need no extra Python
> SDK. Ollama and llama.cpp must already be running. `LLM_PROVIDER=local` does
> not start a server; it means you will inject your own callable into
> `generation.synthesize()`. For a local server, use `ollama` or `llamacpp`.

---

## 3. Verify the backend without starting a server

A few import-level smoke checks catch most environment problems:

```bash
python -c "from router import route; print(route('Compare the two RFCs'))"
python -c "from embeddings import active_provider, embedding_dim; print(active_provider(), embedding_dim())"
python -m db.logger
python -m pytest tests/test_health.py tests/test_router.py -q
```

Expected:

- The router prints a `RoutingDecision` with `complexity`, `confidence`,
  `retrieval_depth`, and `activated_modules`.
- The embedder prints `sentence-transformers 384`, or `stub 16` if the model
  could not load. `stub` means retrieval is not semantically meaningful; useful
  for offline smoke tests only.
- The logger creates `db/graft_logs.db` and prints a smoke-test row.
- The two test files pass.

Running the full suite is covered in [07 - Testing and CI](07-testing-and-ci.md).
A fresh clone may need the sample corpus indexed first for one benchmark test.

---

## 4. Start the API

From the repository root, with the virtual environment active:

```bash
uvicorn api.main:app --reload --port 8000
```

Alternatives:

```bash
# If you installed with `pip install -e ...`
graft-api

# Explicit module entry point
uvicorn api.main:app --host 127.0.0.1 --port 8000
```

> **Known rough edge:** `python -m api.main` does **not** start the server in
> the current `dev` branch. `api/main.py` defines `main()` but has no
> `if __name__ == "__main__": main()` guard. Use `uvicorn` or `graft-api`.
> The same is true of `python -m indexing.pipeline`; use the `graft-index`
> console script or call `index_document()` from Python.

Leave this terminal running. Every request is logged to the terminal. The
startup warmup line reports the embedder and timing.

### Verify the server

```bash
curl http://127.0.0.1:8000/health
```

You should see something like:

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

The collection name suffix is the embedder slug and dimension. If you later set
`EMBEDDING_PROVIDER=stub`, the name becomes
`graft_tree_nodes_stub_16`, and any documents indexed under the old collection
will not be found. That is intentional: it prevents mixing vector dimensions.

Interactive API docs are available at:

- <http://127.0.0.1:8000/docs>
- <http://127.0.0.1:8000/openapi.json>

---

## 5. Index your first document

### Option A: raw text (JSON)

```bash
curl -X POST http://127.0.0.1:8000/index \
  -H 'Content-Type: application/json' \
  -d '{
    "text": "TLS 1.2 allows static RSA key exchange. TLS 1.3 removes static RSA and requires forward secrecy. TLS 1.3 also simplifies the cipher suite list.",
    "document_id": "tls_note",
    "source": "tls_note.txt"
  }'
```

The JSON branch accepts any text. It returns the first 20 nodes, not the whole
tree:

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

Chunk IDs are full 64-character SHA-256 hex strings, derived from
`sha256(f"{document_id}:{chunk_index}")`. Summary node IDs use the readable
form `<document_id>_summary_L<level>_<index>`. Both are used as the Chroma
`chunk_id`, so they are stable across re-indexing unless the document ID or
chunk index changes.

### Option B: PDF or DOCX (multipart)

```bash
curl -X POST http://127.0.0.1:8000/index \
  -F "files=@data/sample_docs/1705.pdf"
```

The multipart branch accepts `.pdf` and `.docx` only. It returns tree statistics:

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

Notes:

- `document_id` is derived from the file stem, sanitised to letters, digits,
  `_`, and `-`, and truncated to `MAX_DOCUMENT_ID_LENGTH`.
- Duplicate stems get `_1`, `_2`, and so on.
- A short document still gets a root summary; a single chunk is not left as a
  parentless leaf.
- Re-indexing the same `document_id` upserts nodes by `chunk_id`. If the new
  version has fewer chunks, stale nodes from the old version remain in Chroma.
  Use a new `document_id` or clear the collection when replacing a document.
- The upload runs synchronously in the request. A large PDF can take a while.

### Option C: Python

```python
from indexing.pipeline import index_document

nodes = index_document(
    "TLS 1.2 allows static RSA. TLS 1.3 removes it.",
    document_id="tls_note",
    source="tls_note.txt",
)
print(len(nodes), "nodes")
print([n.node_id for n in nodes if n.parent_id is None])
```

`index_document` builds the tree with the shared embedder **and** persists it to
Chroma. `build_tree()` builds without persisting, which is useful for tests.

---

## 6. Ask your first question

```bash
curl -X POST http://127.0.0.1:8000/query \
  -H 'Content-Type: application/json' \
  -d '{"query":"Compare TLS 1.2 and TLS 1.3 key exchange."}'
```

Response shape (abridged):

```json
{
  "request_id": "req_1a2b3c4d5e",
  "answer": "TLS 1.2 allows static RSA key exchange. TLS 1.3 removes static RSA and requires forward secrecy. ...",
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

What just happened:

1. `router.route()` scored the question and selected depth 2 plus
   `fact_lookup`, `numeric_reasoning`, and `multi_hop` (the version numbers
   count as numeric intent).
2. `retrieval.retrieve()` embedded the query and searched Chroma at levels 0,
   1, and 2.
3. Only the three activated modules ran.
4. `generation.synthesize()` returned a deterministic answer from module
   output and context.
5. The query was logged to SQLite, best-effort.

Optional request fields:

| Field | Type | Effect |
|---|---|---|
| `request_id` | string | Use your own ID instead of the generated `req_...` |
| `retrieval_depth` | int (0-2) | Override the router's depth (ablation/testing) |
| `filters` | object | Extra Chroma metadata equality filters, e.g. `{"document_id":"tls_note"}` |

A `filters` example:

```bash
curl -X POST http://127.0.0.1:8000/query \
  -H 'Content-Type: application/json' \
  -d '{"query":"What changed in key exchange?","filters":{"document_id":"tls_note"}}'
```

If the collection is empty, retrieval returns nothing, modules report
"No context provided.", and the answer falls back to a no-context message. It
still returns HTTP 200; an empty knowledge base is not a server error.

---

## 7. Inspect query analytics

Every successful `/query` writes a row to SQLite. The analytics routes read it
back:

```bash
curl 'http://127.0.0.1:8000/analytics/queries?limit=10'
curl http://127.0.0.1:8000/analytics/summary
curl 'http://127.0.0.1:8000/analytics/slow?threshold_ms=100'
```

`summary` returns totals, average latency, and per-module usage counts. The
database file is `db/graft_logs.db` in the current working directory. Note that
`db/logger.py` hard-codes this path; the `DB_PATH` setting is declared but not
currently consulted by the logger. See chapter 03.

---

## 8. Start the frontend

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Vite prints a local URL, normally <http://localhost:5173>. The page renders the
GRAFT chat shell: sidebar, message feed, input bar, drag-and-drop attachments,
and a logo.

### What works today

`frontend/vite.config.js` proxies `/query`, `/api`, `/health`, and `/index` to
`http://127.0.0.1:8000`. The chat flow is two phases, three HTTP calls:

1. `POST /api/files` uploads the attachment and schedules indexing.
2. `GET /api/files/{id}` is polled until the file is `ready`.
3. `POST /api/chat` sends JSON `{prompt, file_ids}` and receives SSE events.

Text-only chat works without an upload: send an empty `file_ids` list and the
query runs over the whole collection. Uploaded files narrow the query to those
documents.

If chat fails, check the browser Network tab and the API terminal. The common
cases are an API that is not running, a port mismatch in `vite.config.js`, an
upload rejected by size/type validation, or an embedder/collection problem.
Chapter [06 - Frontend/backend integration](06-frontend-backend-integration.md)
walks through the sequence and the failure modes.

You can also use Swagger UI at <http://127.0.0.1:8000/docs> or the `POST /query`
examples above. The `QueryForm` / `AnswerPanel` components are still unmounted;
they are an optional simple `/query` view (see chapter 05).

### Production build

```bash
cd frontend
npm run build      # writes frontend/dist/
npm run preview    # serves the built files locally
```

FastAPI does **not** serve `frontend/dist`. A deployment needs a static file
host (or nginx) plus a reverse proxy for `/query`, `/api`, `/analytics`, and
`/health`. Chapter 06 includes a minimal nginx sketch.

---

## 9. Seed the sample corpus (recommended)

`data/sample_docs/` contains RFCs, the Transformer and ConvS2S papers, and a
NIST OSCAL PDF. Indexing them makes queries and the benchmark meaningful.

```bash
python scripts/index_sample_corpus.py
```

What it does:

- Finds `.pdf` / `.docx` files in `data/sample_docs`.
- Uses each file's stem as the `document_id` (the benchmark's chunk IDs depend
  on this).
- Calls `index_document()` for each file, embedding and persisting the tree.
- Writes into the configured Chroma collection.

This can take several minutes on the first run because the embedder model is
downloaded/loaded and every PDF is parsed and summarised. Re-running is safe
but repeats the work.

---

## 10. Run the benchmark (optional)

The benchmark compares GRAFT against the flat baseline. It needs the sample
corpus indexed first because it verifies that every labelled `chunk_id` exists
in the default Chroma store.

```bash
python3 -m benchmark.runner \
  --dataset data/sample_docs/benchmark.jsonl \
  --output results.json
```

Useful flags:

```bash
--seed 42          # recorded in the results file
--ablation         # run every ablation setup instead of GRAFT vs baseline
```

The runner prints a summary and writes `results.json`. It measures:

- token-overlap F1 against `expected_answer`
- wall-clock latency for both systems
- modules fired per query
- retrieval precision@5 against `relevant_chunk_ids`

The benchmark is offline by default (deterministic generation), so treat the
accuracy numbers as a pipeline sanity check, not a final quality claim. With an
LLM enabled, answer quality changes but the comparison becomes non-deterministic.

---

## 11. Reset local state

When you want a clean slate:

```bash
# Stop the API first.
rm -rf .graft/chroma
rm -f db/graft_logs.db
rm -rf data/uploads/*
```

`data/uploads/` may not exist on a fresh clone. It is created by
`POST /api/files`. Uploaded files are stored there with generated names; the
file records themselves live only in the process's memory and disappear on
restart.

To switch embedders, just change `EMBEDDING_PROVIDER` or `EMBEDDING_MODEL`. A
new collection name is selected automatically; old vectors are not reused.

---

## 12. Next steps

- Understand the pipeline: [02 - Architecture and data flow](02-architecture-and-data-flow.md)
- Find a file: [03 - Backend reference](03-backend-reference.md)
- Call every endpoint: [04 - API reference](04-api-reference.md)
- Work on the UI: [05 - Frontend guide](05-frontend-guide.md)
- Understand the chat flow: [06 - Frontend/backend integration](06-frontend-backend-integration.md)
- Run and extend tests: [07 - Testing and CI](07-testing-and-ci.md)
