# 08 - Troubleshooting

A symptom-first guide. Start with the diagnostics, then jump to the section that
matches the failure.

If the problem is specifically the React chat UI, read
[06 - Frontend/backend integration](06-frontend-backend-integration.md) first.

---

## 1. First diagnostics

Run these before changing anything. They separate environment problems from
code problems.

```bash
# Which Python and where?
which python
python --version
python -c "import sys; print(sys.executable)"

# Are the project packages importable from this directory?
python -c "import api, indexing, router, retrieval; print('imports ok')"

# Is the real embedder available?
python -c "from embeddings import active_provider, embedding_dim; print(active_provider(), embedding_dim())"

# What settings are actually loaded?
python -c "from config import settings; print(settings.model_dump())"

# Is the API up, and which collection is it using?
curl -s http://127.0.0.1:8000/health

# What is listening on the ports?
ss -ltnp | grep -E ':(8000|5173)'    # Linux
# lsof -i :8000 -i :5173             # macOS
```

Expected:

- You are running from the repository root, with `.venv` active.
- `active_provider()` is `sentence-transformers` for real retrieval, or `stub`
  if it fell back.
- `/health` returns JSON with the same provider and a collection name ending in
  the dimension.

If any of these fail, fix that before debugging the UI.

---

## 2. Installation problems

### `python: command not found`

Install Python 3.10+ and ensure it is on `PATH`. On Windows, use `py -3.11` or
the full path to `python.exe`. On macOS, `brew install python@3.11`.

### `No module named venv`

Debian/Ubuntu splits venv into a package:

```bash
sudo apt install python3-venv
```

### `pip install -e ".[dev]"` fails on `pyproject.toml`

- Make sure you are at the repository root (the directory containing
  `pyproject.toml`).
- Upgrade pip: `python -m pip install --upgrade pip`.
- If the build backend is missing: `pip install setuptools wheel`.

### `ruff: command not found`

`ruff` comes from the dev extra:

```bash
pip install -e ".[dev]"
# or
pip install ruff
```

If you cannot modify the environment, use the binary that matches your install
(for example `~/.local/bin/ruff`).

### `ModuleNotFoundError: No module named 'api'`

The repository root is not on `sys.path`. Run commands from the root, or set
`PYTHONPATH`:

```bash
PYTHONPATH=. python -m pytest tests/ -q
```

Inside scripts, the project inserts the root explicitly; ad-hoc commands do
not.

### `ImportError: sentence_transformers`

```bash
pip install -r requirements.txt
# or
pip install -e ".[dev]"
```

If the install succeeds but loading still fails (offline machine, proxy, disk
space), GRAFT falls back to the stub. `/health` will say `stub` and retrieval
will not be semantic. Fix the model install for real use; use the stub only for
smoke tests.

---

## 3. Embedder and collection problems

### `/health` says `embedding_provider: "stub"`

Causes, in order of likelihood:

1. `EMBEDDING_PROVIDER=stub` is set in `.env`.
2. The model download failed (no network, Hugging Face blocked, no disk space).
3. `sentence-transformers` or `torch` is not installed correctly.

Fix the environment, delete `.graft/chroma`, and re-index. Vectors produced by
the stub are not compatible in meaning with real vectors, even if the
collection name changes.

### `Collection expecting embedding with dimension of 384, got 16`

You are querying a collection indexed with a different embedder. This should
normally be prevented by the embedder-qualified collection name, but it can
happen if you override `CHROMA_COLLECTION` or point two processes at the same
collection with different settings.

```bash
# Check what the API thinks it is using
curl -s http://127.0.0.1:8000/health

# Start clean (destroys indexed vectors)
rm -rf .graft/chroma
python scripts/index_sample_corpus.py
```

Do not mix `EMBEDDING_PROVIDER=stub` and `sentence-transformers` against one
collection.

### Queries return unrelated passages

- Confirm `/health` shows the real provider and 384 dimensions.
- Confirm the document was indexed into the same collection the API uses.
- Confirm you are not filtering by a stale `document_id`.
- Re-index after changing chunking, because chunk IDs change.

### First query takes 10-20 seconds

That is the model cold start. `WARMUP_ON_STARTUP=true` moves it to startup.
If startup warmup is disabled, the first query pays the cost. On a slow disk or
first-ever download it can be longer. Subsequent queries should be fast.

### Model download is blocked

Options:

- Configure your proxy/Hugging Face mirror according to your environment.
- Pre-download `all-MiniLM-L6-v2` into the Hugging Face cache and set
  `HF_HUB_OFFLINE=1`.
- Use `EMBEDDING_PROVIDER=stub` for a non-semantic smoke test only.

---

## 4. API startup problems

### `python -m api.main` exits immediately

Known issue: `api/main.py` defines `main()` but has no
`if __name__ == "__main__": main()` guard. Use:

```bash
uvicorn api.main:app --reload --port 8000
# or, after `pip install -e ...`
graft-api
```

The same applies to `python -m indexing.pipeline`; use `graft-index` or call
`index_document()`.

### `Address already in use` / `EADDRINUSE`

Another process owns port 8000. Either stop it or use another port:

```bash
uvicorn api.main:app --reload --port 8001
```

Then update the frontend proxy target in `frontend/vite.config.js` (or set
`VITE_API_URL=http://127.0.0.1:8001`) and restart Vite. Do not rely on an
untracked local config file.

### `ValidationError` from `config.py` on startup

A setting has the wrong type. Common cases:

- `API_PORT` is not an integer.
- `ROUTER_THRESHOLD_SIMPLE` or `ROUTER_THRESHOLD_COMPLEX` is not a float.
- `WARMUP_ON_STARTUP` is not a boolean.
- `LLM_TIMEOUT_SECONDS` is zero or negative.

Print the values and compare with `.env.example`.

### Startup logs are silent

Set `GRAFT_LOG_LEVEL=debug` and restart. `logging_config.py` configures the root
logger once; if a library reconfigured logging, `configure_logging(force=True)`
is available in code but not exposed as an env flag.

### Warmup fails but the server still starts

The lifespan catches warmup errors and logs a warning so the API can come up for
diagnostics. Check the warning; `/health` will reveal a stub fallback or a
collection problem. A server that starts is not proof that retrieval works.

---

## 5. Query problems

### `422 query must not be empty`

The query was empty or whitespace. Send a non-empty string:

```bash
curl -X POST http://127.0.0.1:8000/query \
  -H 'Content-Type: application/json' \
  -d '{"query":"What is TLS?"}'
```

### `500 retrieval failed: ...`

Usually a Chroma problem: missing collection, corrupt persistence directory,
permission error, or a filter Chroma cannot parse. Try a query without
`filters`. Inspect the server traceback. If the directory is disposable, reset
`.graft/chroma` and re-index.

### `500 module <name> failed: ...`

The router activated a module that raised. Reproduce the query in Python to get
the full traceback:

```python
from api.main import app
from fastapi.testclient import TestClient
client = TestClient(app)
r = client.post("/query", json={"query": "..."})
print(r.status_code, r.text)
```

Module failures should be rare because the modules are defensive. If a new
change introduced one, add a regression test in the module's test file.

### `500 generation failed: ...`

Often an LLM problem (enabled but unreachable, bad key, timeout, empty
response). Deterministic generation does not fail this way. Check `/health`:

- `llm_*` fields false: deterministic mode; the failure is more likely a code
  bug in synthesis.
- `llm_*` fields true: the provider client was built, but the request failed.
  Check the base URL, model name, key, and timeout.

For Groq/Ollama/llama.cpp:

- Groq: `GROQ_API_KEY` is required; the base URL is
  `https://api.groq.com/openai/v1`.
- Ollama: run `ollama serve` and `ollama pull <model>`; the base URL is
  `http://localhost:11434/v1` by default.
- llama.cpp: start `llama-server`, use the `--alias` value as
  `LLAMACPP_MODEL` if the server requires it; the base URL is
  `http://localhost:8080/v1` by default.

Raise `LLM_TIMEOUT_SECONDS` if a local model is still loading.

### Answer says "No answer could be generated..."

Either retrieval returned nothing, or the modules had no usable result.

- Is the collection empty? Index a document.
- Does `/health` name the collection you indexed into?
- Did you filter by a `document_id` that does not exist?
- Is the embedder a stub? Stub retrieval is not semantic.

### Answers are too short or generic

That is expected with deterministic synthesis. It prioritizes a contradiction
summary, then `fact_lookup`, then the first module result, then the first
passage. Enable an LLM for written synthesis, or improve the module heuristics.

### `filters` returns nothing

Chroma filters match exact metadata. Check the indexed metadata:

```bash
python - <<'PY'
from indexing.vector_store import ChromaVectorStore
store = ChromaVectorStore.shared()
print(store.query([0.0] * 384, n_results=3))
PY
```

Use the same dimension as your embedder (384 real, 16 stub). The safest filter
is `{"document_id": "<id>"}` with the exact ID used at indexing time. For upload
files, the document ID is the server file ID, not the original filename.

---

## 6. Indexing and upload problems

### `POST /index` rejects a `.txt` file

The multipart branch accepts `.pdf` and `.docx` only. Use the JSON branch for
raw text, or upload TXT through `POST /api/files` (which parses it during
background indexing).

### `400 Unsupported file type`

Check the extension. `/index` allows `.pdf` and `.docx`; `/api/files` allows
`.pdf`, `.docx`, and `.txt`. Uppercase extensions are normalized.

### `422 File ... is empty` or `No extractable text`

The file has no text layer. Scanned PDFs need OCR before indexing; GRAFT does
not run OCR. A DOCX with only images behaves the same way.

### `500 Failed to parse ...`

A corrupt or encrypted PDF/DOCX. Verify it opens in a normal reader, then try a
different copy. Encrypted PDFs are not supported.

### Upload returns `413`

The file is larger than 10 MB (`MAX_UPLOAD_MB` in `api/uploads.py`). Either
split the document or raise the constant deliberately; the UI also enforces
10 MB client-side.

### Upload returns `415`

Three causes:

1. Extension not in the allow-list.
2. MIME type not in the allow-list (some browsers report an empty type).
3. The content does not match the extension (a `.pdf` that does not start with
   `%PDF`, a `.docx` that is not a ZIP, or a TXT containing NUL bytes).

The server sniffs content on purpose; renaming a file does not bypass it.

### Uploaded file stays `parsing` forever

Background indexing failed or the process was restarted. Check:

```bash
curl http://127.0.0.1:8000/api/files/<id>
```

- `failed` includes an error message.
- If the record is gone, the process restarted; the in-memory `FILES` dict is
  not durable. Re-upload.
- Check the API terminal for a traceback from `_process`.

The parsed file remains in `data/uploads/` even if the record disappears.

### File is `ready` but the query cannot find it

- The document ID is the upload `id`, not the filename. Filter with the `id`.
- The upload is indexed into the collection named by `/health`. If you
  restarted with a different embedder, it is a different collection.
- A short upload produces a leaf plus a level-1 summary; check that both were
  persisted and that the query's `file_ids` filter uses the same ID.

### Re-indexing leaves old chunks

Chroma upserts by `chunk_id`. Re-indexing a shorter document does not delete
chunks that no longer exist. Use a new `document_id`, or clear the collection
before re-indexing. There is no per-document delete endpoint.

---

## 7. Frontend problems

### Page is blank

1. Open the browser console. A React error will name the component.
2. Confirm `npm install` completed and `npm run dev` is running from
   `frontend/`.
3. Check that `frontend/index.html` loads `/src/main.jsx` and that
   `src/main.jsx` mounts `#root`.
4. If the logo is broken, confirm `frontend/public/graft-logo.png` exists.

### `npm install` fails with `EBADENGINE`

Vite 7 requires Node `^20.19.0` or `>=22.12.0`. Upgrade Node. Node 18 is not
sufficient for the committed Vite version.

### The chat send fails with "Request failed (404)"

The frontend is calling a path the API does not serve. Check the Network tab:
the request should be `POST /api/chat`, not `POST /chat`. If it is `/chat`,
either the frontend was rolled back or `VITE_API_URL` points at a stale build.
`vite.config.js` proxies `/api`; see chapter 06.

### The chat send fails with "Request failed (422)"

The chat endpoint expects JSON `{"prompt": "...", "file_ids": [...]}`. A
multipart body, a missing `prompt`, or `file_ids` as a string all produce a
validation error.

### The chat send fails with "Can't reach the server"

- API not running.
- Wrong port in the proxy.
- `VITE_API_URL` points at the wrong host.
- A network/firewall block (check the browser Network tab).

### CORS error in the console

You are calling the API cross-origin (usually via `VITE_API_URL`) and the origin
is not allowed. Either:

- use the Vite proxy (same-origin, no CORS), or
- set `CORS_ORIGINS` to the exact browser origin and restart the API.

Remember: wildcard origin plus credentials is forbidden. The backend handles
this by disabling credentials in wildcard mode.

### Attachments stay "uploading" or "processing"

The UI uploads to `POST /api/files`, then polls `GET /api/files/{id}` until
`ready` (30-second timeout). Check:

1. The upload request returned 201 with an `id`.
2. `GET /api/files/{id}` returns `parsing` then `ready`.
3. The API log has no background `_process` traceback.
4. The file is under 10 MB and is a real PDF/DOCX/TXT.

If the API restarted, the in-memory record is gone; re-upload.

### Upload succeeds but chat ignores the file

`file_ids` must contain the server `id`, not the original filename. The
frontend takes it from the upload record. Also confirm the file reached `ready`
before sending; a `parsing` file produces a `status` event and an `error` if it
never becomes ready.

### Answer text does not appear even though events arrive

The backend emits both `token {t}` and `token {text}`. If a custom client reads
only one key, use `d.text || d.t`. A malformed `data:` frame is also dropped by
the parser; check the raw Network response.

### File chip stays in the wrong state

The backend emits both `status {status}` and `status {state}`. The current UI
reads `d.state || d.status`. If a custom client uses only one, align it. A
`status` frame is only sent while the chat handler is waiting; the normal UI
already polls to `ready` before sending.

---

## 8. Test failures

### `test_benchmark_dataset_chunk_ids_exist` fails

The default Chroma collection is empty or indexed with a different embedder.

```bash
python scripts/index_sample_corpus.py
python -m pytest tests/test_benchmark_dataset.py -q
```

CI does this before pytest. If you changed chunking, regenerate the labels:

```bash
python scripts/regenerate_benchmark_chunk_ids.py --verify
```

### Integration tests skip

They require the real model. Check:

```bash
python -c "from embeddings import active_provider; print(active_provider())"
```

If it prints `stub`, install/fix `sentence-transformers`. Running with
`-rs` shows the skip reason.

### Tests pass locally but fail in CI

Typical causes:

- A test depends on a local file that is gitignored (`.graft/chroma`,
  `data/uploads`, `db/graft_logs.db`).
- A test assumes the real model; CI checks it, but a dependency install may
  differ.
- A test mutates a shared collection instead of using `tmp_path`.
- Import order or an undeclared dependency (`ruff` catches some of this, mypy
  does not block).

Run the failing test in isolation, then with the full suite, to distinguish
state leakage from an environment difference.

### `pytest` cannot find tests

Run from the repository root. The suite lives in `tests/`; there is no separate
`pyproject` test path that relocates it.

---

## 9. Benchmark problems

### `DatasetFormatError` / exit code 1

The JSONL dataset is malformed. Each line must be an object with:

```json
{"query": "...", "relevant_chunk_ids": ["..."], "expected_answer": "..."}
```

Check the reported line number. Blank lines are ignored; trailing commas are
not valid JSON.

### `chunk_id ... not found in collection`

Index the corpus with the same embedder/chunking used to create the labels:

```bash
python scripts/index_sample_corpus.py
```

If chunking changed, regenerate IDs:

```bash
python scripts/regenerate_benchmark_chunk_ids.py --verify
```

### Benchmark result file is overwritten

`--output` is overwritten by design. Use a unique filename per run, for
example `results-$(date +%Y%m%d-%H%M).json`, if you need history.

### Numbers changed after a code change

That is expected. Check the PR's benchmarking-impact section and tell the
benchmark owner. Do not compare results produced with different embedders,
chunk sizes, datasets, or seeds.

---

## 10. Analytics and SQLite problems

### `/analytics/*` returns empty lists

No query has been logged yet, or the API is writing to a different database
file. `db/logger.py` hard-codes `db/graft_logs.db` relative to the `db/`
package, regardless of `DB_PATH`. Check that file exists and has rows.

### `sqlite3.OperationalError: database is locked`

A long-running writer or a stale journal. Stop the API, remove
`db/graft_logs.db-journal` if present, and retry. Avoid running multiple
uvicorn workers against the same SQLite file for heavy write workloads.

### Analytics do not include a query I just ran

Logging is best-effort. If the insert failed, the request still succeeds and a
warning is logged. Check the terminal. Also confirm the query returned 200; a
422/500 is not logged as a successful query.

### `accuracy_flag` is null

It is only set by `update_accuracy()`; the query path does not grade answers.
The benchmark uses its own metrics, not this column.

---

## 11. Resetting local state

Stop the API and Vite first.

```bash
# Vectors (safe to delete; re-index to rebuild)
rm -rf .graft/chroma

# Query analytics
rm -f db/graft_logs.db db/graft_logs.db-journal

# Uploaded files and in-memory records (records die with the process)
rm -rf data/uploads/*

# Frontend build artifacts and dependencies (optional)
rm -rf frontend/dist frontend/node_modules
```

Then rebuild what you need:

```bash
python scripts/index_sample_corpus.py   # optional but recommended
cd frontend && npm install && npm run dev
```

---

## 12. Collecting a useful bug report

When asking for help, include:

1. The exact command and its full traceback or curl output.
2. `python --version`, OS, and whether `.venv` is active.
3. `curl -s http://127.0.0.1:8000/health` output.
4. The relevant `.env` values **with keys redacted**.
5. Whether the embedder is real or stub.
6. The output of `ruff check .` and the failing test, if applicable.
7. For frontend issues: browser, Node version, the failing request URL, and
   the first SSE frames from the Network tab.

Never paste API keys, `.env` contents with secrets, or private document text
into a public issue.

---

Next: [09 - Glossary](09-glossary.md)
