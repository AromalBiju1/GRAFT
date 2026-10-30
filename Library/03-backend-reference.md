# 03 - Backend reference

A file-by-file guide to every Python module in the repository, plus the
supporting project files. Read the package summary first; dive into the file
entries when you need to change something.

For the conceptual version, read
[02 - Architecture and data flow](02-architecture-and-data-flow.md) first.

---

## Package map

| Package / area | Responsibility | Depends on |
|---|---|---|
| `config.py` | Typed settings from env/.env | pydantic-settings |
| `embeddings.py` | Shared embedder + deterministic fallback | sentence-transformers (optional at runtime) |
| `indexing/` | Parse -> chunk -> embed -> tree -> persist | embeddings, tiktoken, pypdf, python-docx, scikit-learn (optional) |
| `vector_store/` | Public alias for the Chroma wrapper | indexing.vector_store |
| `tree_store/` | Public alias for `TreeNode` | indexing.tree_node |
| `router/` | Complexity score, depth, module activation | config |
| `retrieval/` | Depth-aware search over Chroma | vector_store, embeddings, config |
| `modules/` | Four specialist reasoners | modules.base |
| `generation/` | Deterministic/LLM answer synthesis | modules.base |
| `baseline/` | Flat ungated comparison path | retrieval, modules, generation |
| `api/` | FastAPI app, schemas, upload/chat, analytics | all of the above |
| `backend/` | Legacy minimal app (`/health`, `/index`) | indexing, api.routes.indexing |
| `benchmark/` | Metrics, dataset, CLI runner, judge helpers | router, retrieval, modules, generation, baseline |
| `db/` | SQLite query logger | stdlib sqlite3 |
| `scripts/` | Corpus indexing and chunk-ID regeneration | indexing, benchmark |
| `tests/` | Offline unit/integration coverage | pytest |

Dependency direction is intentionally one-way:

```text
config <- embeddings <- indexing <- vector_store <- retrieval <- router/modules
                                                   \-> generation <- api
```

`tree_store` and `vector_store` are compatibility facades, not second
implementations. If you are tempted to add logic there, add it to the canonical
module instead.

---

## Root files

### `config.py`

**Role:** The single source of truth for tunables. Defines `Settings`
(`pydantic-settings`) and the module-level `settings = Settings()` singleton.

**Key fields:** LLM provider/key/model and timeout, embedding provider/model,
Chroma path and collection base name, SQLite path, API host/port/reload, CORS
origins, startup warmup, router thresholds, judge aliases.

LLM provider fields: Gemini and OpenAI keys/models, Groq key/model/base URL,
Ollama base URL/model, and llama.cpp base URL/model. All are optional.

**Notes:**
- Reads `.env` and environment variables, case-insensitively.
- No `env_prefix`; the real names are `API_PORT`, `CHROMA_PATH`, etc.
  `GRAFT_LOG_LEVEL` is separate and read by `logging_config.py`.
- `llm_enabled=False` by default. Setting a provider alone does not turn LLM
  synthesis on.
- `llm_timeout_seconds` applies to Groq, Ollama, and llama.cpp. Local models
  may need a larger value while they load.
- `default_retrieval_depth` is declared but not used by the current router.
- `judge_*` / `evaluation_*` fields are aliases used by benchmark judge code.

### `embeddings.py`

**Role:** One embedder shared by indexing and querying, with a deterministic
offline fallback.

**Key functions:**
- `embed_text(text)` -> vector
- `embed_texts(texts)` -> batch vectors
- `active_provider()` -> `"sentence-transformers"` or `"stub"`
- `embedding_dim()` -> 384 for MiniLM, 16 for the stub
- `collection_name(base=None)` -> embedder-qualified Chroma name
- `_load_model()` / `reset_model_cache()` -> lifecycle hooks (mainly tests)

**Notes:**
- The stub is a SHA-256 hash normalized to unit length. It is deterministic but
  not semantic. Use it only for offline tests or quick smoke runs.
- If the real model fails to load, GRAFT logs a warning and falls back instead
  of crashing. `/health` reports which provider is actually live.
- `collection_name()` is why switching embedders does not cause a dimension
  mismatch: it selects a different collection instead.
- The model is loaded lazily and cached behind a lock. `WARMUP_ON_STARTUP`
  moves that cost to server startup.

### `logging_config.py`

**Role:** Configures the root logger once so INFO records (startup warmup,
fallback warnings) are not silently dropped.

**Key functions:** `configure_logging()`, `resolve_level()`, `get_logger()`,
`logging_config_dict()`.

**Notes:**
- `GRAFT_LOG_LEVEL` accepts `debug|info|warning|error`.
- Noisy third-party loggers (chromadb, httpx, sentence-transformers,
  transformers, urllib3) are raised to WARNING.
- Idempotent; safe to call from the FastAPI lifespan or a CLI.

### `version.py`

**Role:** Defines `__version__ = "0.1.0"`, reported by `GET /health` and the
OpenAPI description.

---

## `api/`: HTTP surface

### `api/__init__.py`

Empty package marker. There is no re-export layer here; import from
`api.main` or `api.schemas` directly.

### `api/main.py`

**Role:** The main FastAPI application. This is the process you start with
`uvicorn api.main:app`.

**Responsibilities:**
- Lifespan: configure logging, optionally warm the embedder/Chroma, reset the
  shared Chroma cache on shutdown.
- CORS middleware with env-driven origins and the wildcard/credentials rule.
- `GET /health` with version, collection, embedder, dimension, and LLM fields.
- `POST /query`: validates the request, builds the optional LLM client, and
  delegates the pipeline to `api.query_runner.execute_query()`.
- `POST /index` with two branches: JSON raw text and multipart PDF/DOCX.
- Includes `api.uploads.router` (`/api/*`) and `api.routes.analytics.router`
  (`/analytics/*`).
- `_build_llm_client()`: builds a provider client when enabled, with a
  compatibility shim that supports tests patching either
  `api.main.create_llm_client` or `generation.create_llm_client`.

**Key constants:** `UPLOAD_CHUNK_SIZE_TOKENS=200`,
`UPLOAD_CHUNK_OVERLAP_TOKENS=20`, `UPLOAD_CLUSTER_SIZE=4`. `MODULE_REGISTRY`
is imported from `api.query_runner` for backwards compatibility, so
`api.main.MODULE_REGISTRY` still resolves.

**Notes:**
- `_embed_query_stub()` is a historical name; it now calls the real
  `embeddings.embed_text`.
- The module docstring mentions `POST /baseline/query`, but no such route
  exists. The baseline is exercised through `benchmark.runner`.
- `/index`'s multipart branch and `api/routes/indexing.py` are two separate
  implementations of the same idea. Only the former is mounted in this app.
- `python -m api.main` is a no-op because there is no `__main__` guard; use
  `uvicorn` or `graft-api`.

### `api/query_runner.py`

**Role:** The shared query pipeline used by both `POST /query` and
`POST /api/chat`, so the two endpoints cannot drift apart.

**Responsibilities:**
- `MODULE_REGISTRY`: one instance of each specialist module.
- `execute_query(query_text, request_id=None, retrieval_depth_override=None,
  filters=None, llm_client=None)`: route -> retrieve -> run activated modules ->
  synthesize -> log; returns `{request_id, answer, evidence, routing,
  latency_ms}`.
- `to_evidence_dicts()`: projects evidence into the API shape and truncates
  text to 600 characters.
- Builds an LLM client from settings when `llm_client` is `None` and
  `LLM_ENABLED=true`; a construction failure logs a warning and falls back to
  deterministic synthesis.

**Errors:** `HTTPException(422)` for an empty query and `HTTPException(500)` for
retrieval, module, or generation failures. Query logging is best-effort.

`api.main` imports `MODULE_REGISTRY` from here for backwards compatibility.

### `api/schemas.py`

**Role:** Pydantic request/response models, aligned with `docs/interfaces.md`
sections 9-10.

**Models:**
- `QueryRequest`: `query` (required, min length 1), optional `request_id`,
  optional `retrieval_depth` (>= 0), optional `filters`.
- `EvidenceItem`: `source`, `page`, `chunk_id`, `node_id`, `level`, `score`,
  `text` (all optional).
- `QueryResponse`: `request_id`, `answer`, `evidence`, `routing`,
  `latency_ms`.
- `HealthResponse`: status/version/paths plus several LLM boolean aliases and
  `llm_provider`.

The multiple LLM booleans (`llm_enabled`, `llm_in_use`, `llm_active`,
`llm_available`, `llm_used`) exist for backwards compatibility with different
consumers. They currently report the same value: whether `_build_llm_client()`
actually returned a client. They are **not** the raw `LLM_ENABLED` setting, so
they stay false when the setting is true but the provider is misconfigured.

### `api/uploads.py`

**Role:** Upload and chat endpoints, mounted under `/api`.

**Endpoints:**
- `POST /api/files` (201): multipart single file, max 10 MB, PDF/DOCX/TXT.
  Streams to `data/uploads/<uuid>.<ext>`, sniffs the header (`%PDF`, `PK` for
  docx, no NUL bytes for txt), records metadata in the in-memory `FILES` dict,
  and schedules background indexing.
- `GET /api/files/{fid}`: returns the in-memory record (404 if unknown).
- `POST /api/chat`: JSON `{prompt, file_ids}`. Returns an SSE stream. It waits
  for each file to reach `ready`, then calls `run_query()`.

**SSE events emitted here:**
- `status`: `{file, status, state}` while waiting for processing; both status
  keys carry the same value.
- `meta`: `{routing, evidence}` after `run_query` returns.
- `token`: `{t, text}` per word (simulated streaming); both keys carry the same
  string.
- `error`: `{message}`; the stream then ends without `done`.
- `done`: `{}` only after a successful stream.

`run_query()` calls `api.query_runner.execute_query()`. It converts `file_ids`
into a metadata filter: one ID becomes `{"document_id": id}`, several become
`{"document_id": {"$in": [...]}}`. Files must already be uploaded through
`POST /api/files`; the chat endpoint accepts JSON, not file parts.

**State:** `FILES` is an in-process dict. Records and statuses are lost on
restart. `UPLOAD_DIR` is hard-coded to `data/uploads`.

### `api/routes/__init__.py`

Exports the indexing router: `from .indexing import router`. It does not export
the analytics router; `api/main.py` imports that directly.

### `api/routes/analytics.py`

**Role:** Read-only query analytics over `db/logger.py`.

**Endpoints:**
- `GET /analytics/queries?limit=50` (1..1000): recent rows, newest first.
- `GET /analytics/summary`: total queries, average latency, per-module usage
  counts.
- `GET /analytics/slow?threshold_ms=1000&limit=100`: rows slower than the
  threshold.

**Helpers:** `_parse_modules_fired()` normalizes the JSON/string column;
`_row_to_entry()` projects a SQLite row to the public shape.

**Notes:** The router has prefix `/analytics` and tag `analytics`. All reads
call `db_logger.init_db()` first, so the table is created if missing.

### `api/routes/indexing.py`

**Role:** An alternative `POST /index` implementation for multipart uploads.
It validates extensions, parses in a temp directory, builds documents with
`indexing.builder.build_documents`, persists to a `ChromaVectorStore`, and
returns `{status, document_ids, total_chunks, tree_stats}`.

**Mounted only in the legacy app** (`backend/app/main.py`). `api/main.py`
defines its own `/index`, so this router is not active in the main app. It is
kept because the legacy backend and its tests use it.

**Notes:** It uses `DEFAULT_CHUNK_SIZE` (400), `DEFAULT_CHUNK_OVERLAP` (50), and
`DEFAULT_MIN_CLUSTER_SIZE` (3) from `indexing.config`, builds with the default
stub embedder (because no `embedding_fn` is passed), persists to the **base**
`graft_tree_nodes` collection, and creates its own store rather than using the
shared cache. That makes it isolated from the main app's embedder-qualified
collection: documents uploaded through the legacy app are not visible to
`api.main`'s `/query` unless you deliberately align the storage settings. If
you are looking for "the" `/index` implementation for normal use, use
`api/main.py`; this file is the older sibling.

---

## `backend/`: legacy app

### `backend/__init__.py` and `backend/app/__init__.py`

Package markers. They contain no logic.

### `backend/app/main.py`

**Role:** Minimal FastAPI app that mounts only the indexing router and a health
check.

**Run it with:**
```bash
uvicorn backend.app.main:app --reload --port 8000
```

**Endpoints:** `GET /health` -> `{"status":"ok"}` and `POST /index` (multipart
via `api.routes.indexing`).

**Notes:** No CORS, no `/query`, no analytics, no upload/chat. It exists for the
old upload flow and as a smaller integration target. `README.MD` correctly
recommends `api.main:app` for anything that queries.

### `backend/api/uploads.py`

**Role:** A standalone prototype of the multipart chat endpoint
`POST /chat`, with SSE. It accepts `prompt` + `files` form fields, validates
type/size, saves files, and streams `status`/`token`/`error`/`done` events.

**Status:** Not mounted anywhere. Its `ingest_file()` and `answer_stream()`
raise `NotImplementedError` with TODO comments. It was the original multipart
prototype (`/chat`, `prompt` + `files`). The application has since moved to the
two-phase flow in `api/uploads.py`: upload through `/api/files`, then send JSON
`{prompt, file_ids}` to `/api/chat`. The current React client uses that flow.

**Why keep it:** It is a historical contract and a starting point if you ever
want a one-request multipart chat endpoint.

---

## `baseline/`

### `baseline/__init__.py`

**Role:** The flat "retrieve everything, run every module" comparison system.

**Key function:** `run_baseline(request_id, query, query_embedding, *,
n_results=8, filters=None)`.

**Behavior:**
1. Calls `retrieval.retrieve()` with no complexity gating (caller supplies
   depth; default is all levels because `retrieval_depth` is omitted).
2. Runs `FactLookupModule`, `MultiHopModule`, `NumericReasoningModule`, and
   `ContradictionDetectionModule` unconditionally.
3. Calls `generation.synthesize()` with the same function GRAFT uses.
4. Returns `request_id`, `answer`, `evidence`, `module_results`, `retrieved`,
   and latency metadata.

**Notes:** There is no HTTP route. `benchmark.runner` is the consumer. If this
module gains gating, the benchmark comparison stops being meaningful; the tests
pin its ungated contract.

---

## `benchmark/`

### `benchmark/__init__.py`

**Role:** Small, dependency-free metric helpers.

**Functions:**
- `measure_latency(fn, *args, **kwargs)` -> `(result, latency_ms)`
- `modules_fired_count(module_results)` -> integer
- `retrieval_precision_at_k(retrieved, relevant_ids, k=5)` -> hits / min(k, n)

**Notes:** These are reused by the runner. Keep metric definitions explicit
and reviewable; changing them invalidates prior results.

### `benchmark/datasets.py`

**Role:** A small seed question bank (`SEED_QUESTIONS`) with IDs, queries,
relevant documents, tags, and expected depths. It mirrors examples in
`data/sample_docs/sample_docs.md`.

**Notes:** The CLI runner reads a JSONL dataset
(`data/sample_docs/benchmark.jsonl`), not this list. `datasets.py` is useful for
experiments and for understanding the intended query categories.

### `benchmark/prompts.py`

**Role:** Prompt templates for LLM-as-judge evaluation: `JUDGE_PROMPT`,
`GRADING_PROMPT`, `EVALUATION_PROMPT`, `LLM_JUDGE_PROMPT`.

**Key class:** `JudgePrompt(str)` overrides `.format()` to accept common field
aliases (`question`/`query`, `expected_answer`/`gold`, and so on), so judge
callers do not have to agree on one spelling.

### `benchmark/evaluation.py`

**Role:** Convenience re-export layer. It imports judge/grade/evaluate helpers
from `benchmark.runner` and prompts from `benchmark.prompts`, then defines
`__all__`.

**Notes:** There is no logic here. If you are looking for the implementation,
it is in `runner.py`.

### `benchmark/runner.py`

**Role:** The benchmark CLI and the largest module in the repo (~1,400 lines).

**CLI:**
```bash
python3 -m benchmark.runner \
  --dataset data/sample_docs/benchmark.jsonl \
  --output results.json \
  [--seed 42] [--ablation]
```

**Dataset format:** one JSON object per line:
```json
{"query": "...", "relevant_chunk_ids": ["..."], "expected_answer": "..."}
```

**What it does:**
- `load_dataset()` validates fields and line numbers.
- `verify_benchmark_chunk_ids_exist()` checks that every labelled chunk ID is
  present in the default Chroma collection. This is why
  `scripts/index_sample_corpus.py` must run first.
- `run_graft()` embeds, routes, retrieves, runs activated modules, generates,
  and records metrics.
- The baseline path uses `baseline.run_baseline()` with all modules.
- Metrics: token F1 (`token_f1`), latency, modules fired, precision@5.
- `aggregate()` / `run_benchmark()` produce per-system summaries.
- `run_ablation()` runs named setups (`AblationSetup`) and computes deltas.
- `format_summary()` / `format_ablation_summary()` render terminal tables.
- Judge helpers (`judge_answer`, `JudgeResult`, etc.) optionally call an LLM
  to score answer quality. They are separate from the deterministic metrics
  and are skipped when no client is configured.

**Notes:**
- `METRIC_DEFINITIONS` is copied into the results file so a number can be
  traced to its definition.
- The runner is deterministic by default (no LLM generation), so it is safe
  for CI and offline machines.
- It creates the output parent directory and overwrites the target file.

---

## `db/`

### `db/logger.py`

**Role:** SQLite query logging used by `/query` and the analytics routes.

**Key functions:**
- `init_db()`: creates `query_logs` and an index if missing.
- `get_connection()`: context manager; commits and always closes.
- `log_query(...)`: inserts a row and returns its ID.
- `update_accuracy(log_id, flag)`: sets `correct|incorrect|unscored`.
- `get_logs(system=None, limit=100)`: newest-first rows.
- `_validate_accuracy_flag()`: rejects unknown flags.

**Schema:** `query_logs(id, query_text, system, timestamp, latency_ms,
modules_fired, retrieval_depth, accuracy_flag)`.

**Notes:**
- `DB_PATH` is hard-coded to `db/graft_logs.db` (relative to this file), not
  `settings.db_path`. This is a known inconsistency.
- `timestamp` is UTC ISO-8601.
- `modules_fired` is stored as JSON text.
- Running `python -m db.logger` executes a smoke test and prints a row.

---

## `generation/`

### `generation/__init__.py`

**Role:** Turns retrieved context + module results into the final answer.

**Key functions:**
- `build_prompt(query, context, module_results)`: first 5 passages plus module
  JSON, with the grounding instruction.
- `create_llm_client()`: builds a Gemini/OpenAI SDK client, or an
  OpenAI-compatible HTTP client for Groq/Ollama/llama.cpp. Raises on missing
  keys, SDKs, or base URLs; `local` requires an injected client.
- `synthesize(request_id, query, context, module_results, llm_client=None)`:
  returns `{"request_id", "answer", "evidence"}`.

**Deterministic answer priority:** contradiction summary > fact_lookup >
first module result > first context passage > no-answer fallback.

**Evidence:** union of context and module evidence, deduplicated by
`chunk_id`/`node_id`, preserving insertion order.

**Notes:**
- The LLM call is optional and never happens unless a client is passed.
- Provider errors are converted to a generic message so credentials do not
  leak.
- The grounding instruction tells the model to answer only from context and
  not use outside knowledge. It deliberately avoids a short refusal phrase
  because small local models echoed "Insufficient evidence" even when the
  answer was present. Models still decline naturally when context is unrelated.

---

## `indexing/`

### `indexing/__init__.py`

**Role:** Public surface for the indexing package. Re-exports
`parse_document`, `ingest_document`, `chunk_text`, `count_tokens`,
`build_tree`, `build_tree_from_chunks`, `index_documents`, `index_document`,
`persist_tree_nodes`, `RecursiveSummarizer`, `build_raptor_tree`, and
`TreeNode`.

Use this for stable imports:
```python
from indexing import build_tree, index_document
```

### `indexing/parser.py`

**Role:** Extract text from PDFs and DOCX files.

**Key symbols:** `parse_document(path)`, `DocumentParsingError`.

**Behavior:** `.pdf` -> pypdf; `.docx` -> python-docx. Unsupported extensions
raise `ValueError`; unreadable/empty files raise `DocumentParsingError`. The
error message names the file and the reason.

### `indexing/chunker.py`

**Role:** Token-based, paragraph-aware chunking.

**Key functions:** `chunk_text(...)`, `count_tokens(text)`.

**Behavior:**
- Uses `tiktoken` `cl100k_base`; chunk size and overlap are tokens, not words.
- Prefers paragraph breaks, then sentence boundaries, then a hard token cut.
- Produces a full 64-character hex `chunk_id`:
  `sha256(f"{document_id}:{chunk_index}").hexdigest()`.
- Each chunk has `document_id`, `text`, `chunk_index`, `token_count`.
- Overlap is applied by rewinding tokens between windows.

**Notes:** Changing chunk size changes chunk IDs and therefore benchmark
labels. Regenerate `data/sample_docs/benchmark.jsonl` with
`scripts/regenerate_benchmark_chunk_ids.py` if you change chunking.

### `indexing/config.py`

**Role:** Indexing tunables, all named constants.

**Key values:** `DEFAULT_CHUNK_SIZE=400`, `DEFAULT_CHUNK_OVERLAP=50`,
`DEFAULT_MIN_CLUSTER_SIZE=3`, `DEFAULT_CLUSTER_METHOD="gmm"`,
`DEFAULT_RANDOM_STATE=42`, `MAX_TREE_DEPTH=20`, `COLLAPSE_THRESHOLD=3`,
`DEFAULT_MAX_SUMMARY_TOKENS=400`, `STUB_SUMMARY_MAX_CHARS=600`,
`STUB_EMBEDDING_DIM=16`, `ALLOWED_DOCUMENT_SUFFIXES={".pdf",".docx"}`,
`MAX_DOCUMENT_ID_LENGTH=64`.

### `indexing/ingest.py`

**Role:** Convenience wrapper: `ingest_document(path, document_id=..., ...)`
parses a file and returns chunks. Re-exports `parse_document` and
`DocumentParsingError`.

### `indexing/pipeline.py`

**Role:** End-to-end orchestration: build the tree with the configured embedder
and persist it.

**Key functions:**
- `index_document(text, *, document_id, source=None, chunk_size=...,
  chunk_overlap=..., cluster_size=..., persist_path=None,
  collection_name=None, embedding_fn=None, llm_client=None)`.
- `persist_tree_nodes(nodes, *, persist_path=None, collection_name=None)`:
  opens the shared Chroma store and calls `indexing.store.persist_tree_nodes`.
- `main()`: CLI body (but **no `__main__` guard**, so `python -m
  indexing.pipeline` is currently a no-op; use `graft-index` or Python).

**Notes:** It imports `vector_store` lazily to break the
`indexing -> pipeline -> vector_store -> indexing` cycle. Persistence failures
raise loudly so indexing never silently "succeeds" without being queryable.

### `indexing/builder.py`

**Role:** RAPTOR tree construction: chunks -> leaves -> recursive summaries.

**Key functions:**
- `build_tree(text, *, document_id, source=None, chunk_size=...,
  chunk_overlap=..., cluster_size=..., embedding_fn=None, llm_client=None)`:
  parse text into chunks, embed, build layers, return all nodes.
- `build_tree_from_chunks(chunks, ...)`: accepts both the current flat chunk
  schema and the legacy nested-metadata schema.
- `build_documents(documents, ...)`: index multiple documents in one call.
- `index_documents(...)`: build + persist for a list of documents.
- `_stub_llm_client(prompt)`: default offline summariser.
- `_embed_many(...)`: batch embedding when the embedder supports it.
- `_chunk_metadata(...)`: preserves chunk provenance on leaves.

**Notes:** The default `embedding_fn` is the hash stub, not
`embeddings.embed_text`. `index_document()` overrides it with the real
embedder. `index_documents()` (in this file) also defaults to the base
`graft_tree_nodes` collection when no `collection_name` is passed; it does not
apply the embedder-qualified collection name. For the normal application path,
use `indexing.pipeline.index_document()`, which does both. If you call
`build_tree()` directly, pass `embedding_fn=embed_text` when you care about
semantic behavior.

### `indexing/prompts.py`

**Role:** The summarization prompt template.

**Symbols:** `SUMMARIZATION_PROMPT`, `format_summarization_prompt(texts)`.
The prompt asks for a standalone cohesive summary while retaining names,
entities, and numeric claims.

### `indexing/store.py`

**Role:** Persist `TreeNode` objects through a `ChromaVectorStore`.

**Function:** `persist_tree_nodes(nodes, vector_store)`.

**Behavior:** Validates every node (embedding present, `document_id` present)
before writing, builds records with `chunk_id = node.node_id` and metadata
(`node_id`, `document_id`, `level`, `node_type`, `parent_id`, `child_ids`),
then calls `insert_many()` once. A bad node aborts the whole batch. Leaves use
the 64-character chunk hash; summaries use IDs such as
`tls_note_summary_L1_0000`.

### `indexing/summarizer.py`

**Role:** The recursive RAPTOR engine.

**Key class:** `RecursiveSummarizer(llm_client=None, min_cluster_size=3,
cluster_method="gmm", random_state=42, max_summary_tokens=400,
node_id_prefix="summary")`.

**Key methods:**
- `build_tree_layers(leaf_nodes)`: recursively cluster and summarize.
- `build_raptor_tree(leaf_nodes)`: alias.
- `summarize_cluster(cluster, level)`: one summary node.
- `_cluster_nodes(nodes)`: GMM/k-means/UMAP, with sequential fallback.
- `_call_llm(prompt)`: callable/object coercion, empty-result validation,
  truncation.

**Termination:** one node remains, or a layer of <= 3 nodes is collapsed into a
final root, or `MAX_TREE_DEPTH` is reached.

**Notes:** Cluster fallback triggers when embeddings are missing, uniform, or
scikit-learn is unavailable. Summary text is truncated to
`max_summary_tokens` whitespace tokens. The stub summariser is extractive.

### `indexing/tree_node.py`

**Role:** The one `TreeNode` dataclass.

**Fields:** `node_id`, `text`, `level`, `embedding`, `parent_id`, `child_ids`,
`metadata`.

**Methods:** `is_leaf` (level 0 or no children), `to_dict()` (excludes the
embedding).

**Notes:** This is the canonical tree type. `tree_store/__init__.py` only
re-exports it. Do not create a second node class.

### `indexing/vector_store/__init__.py`

Re-exports `ChromaVectorStore` from `chroma_store.py`.

### `indexing/vector_store/chroma_store.py`

**Role:** Persistent ChromaDB wrapper for precomputed embeddings.

**Key class:** `ChromaVectorStore(persist_path=..., collection_name=...)`.

**Key methods:**
- `insert(chunk_id, document_id, text, embedding, metadata=None)`
- `insert_many(records)`
- `query(embedding, n_results, filters=None)`
- `shared(persist_path, collection_name)` (class method, process cache)
- `reset_shared_cache()` (class method)
- `close()`

**Behavior:**
- Upserts by `chunk_id`.
- Returns `chunk_id`, `text`, `distance`, `score = 1 - distance`, `metadata`.
- `filters` become Chroma metadata `where` clauses.
- The shared cache is keyed by `(resolved path, collection)` and guarded by a
  lock. It is reset on app shutdown and in tests.

**Notes:** Opening a collection is not free (~6 ms); the shared cache exists
because that cost was a measurable share of warm query latency. Do not close a
shared store from request code.

---

## `modules/`

### `modules/__init__.py`

Re-exports `BaseModule` and `ModuleResult`.

### `modules/base.py`

**Role:** The shared module contract.

**`ModuleResult` fields:** `request_id`, `module`, `result`, `evidence`,
`confidence`, `metadata`. `to_dict()` clamps confidence to 0..1.

**`BaseModule`:** defines `run()` (abstract-ish; raises `NotImplementedError`)
and `__call__` (delegates to `run`). Modules are invoked as
`mod(request_id, query, context)`.

### `modules/fact_lookup/__init__.py`

**Class:** `FactLookupModule` (`name="fact_lookup"`).

**Behavior:** Extractive, deterministic, offline. Passages are ranked by
query-term coverage first and retrieval score second, so a highly relevant
lower-scored chunk can beat an unrelated higher-scored one. The best-matching
sentence is returned instead of the whole chunk (max 800 chars); a short
heading-like sentence is expanded with the next sentence or list item, and
source line breaks are preserved. Empty context returns "No context provided.",
confidence 0.0, metadata reason `empty_context`. Blank passages fall back to
"No answer found in context." Metadata records `selection`, `matched_terms`,
`query_coverage`, `source_chunk_id`, and `answer_chars`.

**Upgrade path:** LLM synthesis over the evidence set.

### `modules/multi_hop/__init__.py`

**Class:** `MultiHopModule` (`name="multi_hop"`).

**Behavior:** Requires at least 2 passages. Concatenates the top 3 by score
with `---` separators, confidence = average score * 0.9, metadata
`hops_used`. With fewer than 2 passages, returns a low-confidence
"not enough context" result.

**Upgrade path:** query decomposition and iterative retrieval.

### `modules/numeric_reasoning/__init__.py`

**Class:** `NumericReasoningModule` (`name="numeric_reasoning"`).

**Behavior:** Deterministic, offline extraction and arithmetic.

- Parses numbers with currency symbols, percentages, magnitude suffixes
  (`k`, `m`, `bn`, `thousand`, `million`, ...), signs, and parenthesised
  negatives.
- Detects pipe-delimited and conservatively space-aligned tables, then labels
  each numeric cell with its column header. Non-table values get the nearest
  noun phrase from the same clause, falling back to the sentence subject.
- Excludes page numbers and document references from aggregates. Bare
  four-digit years are also excluded from `sum`, `average`, `min`, `max`, and
  `percent_change` unless the question explicitly asks about a year.
- Picks an operation from the query:
  - `sum` / `total` / `add` -> sum
  - `average` / `mean` -> mean
  - `minimum` / `lowest` / `smallest` -> min
  - `maximum` / `highest` / `largest` -> max
  - `difference` / `subtract` / `compare` with >= 2 operands -> first minus
    second
  - percentage increase/decrease/change -> percent change
  - otherwise -> extract the most query-relevant values (max 10)
- Orders `difference` and `percent_change` operands by where their entity
  appears in the question, so "difference between revenue and costs" computes
  revenue minus costs.
- Returns per-operand details (`operand_details`), a shared `unit`, and a
  preformatted string when possible. Evidence contains only passages that
  contributed an operand (max 3).
- No numbers -> `None`, operation `no_numbers`, confidence 0.2.

Confidence is 0.9 for a computed result whose operands matched the query
label, 0.85 for a fallback computation, 0.75 for mixed units, 0.3 when the
operation lacks operands, and 0.55 for plain extraction.

### `modules/contradiction_detection/__init__.py`

**Class:** `ContradictionDetectionModule` (`name="contradiction_detection"`).

**Behavior:** Compares overlapping passage pairs.
- If `transformers` and the NLI model are available, it lazily loads
  `microsoft/deberta-v3-mnli` and predicts entailment/contradiction with a 0.7
  threshold.
- Otherwise it falls back to keyword overlap plus a negation heuristic.
- Returns a structured result with a `summary`, `conflicts` list, and
  evidence. Metadata tracks how many pairs used NLI versus the fallback.

**Optional dependency:** `pip install -e ".[nli]"`. Set `HF_HUB_OFFLINE=1`
to prevent Hub access; an uncached model falls back to the heuristic.

**Notes:** NLI loading is lazy and thread-safe. Model/inference failures are
reported as `NLIUnavailableError` internally and fall back to the heuristic
rather than failing the whole query.

---

## `retrieval/`

### `retrieval/__init__.py`

**Role:** Depth-aware ANN search.

**Key function:** `retrieve(query_embedding, *, n_results=5,
retrieval_depth=None, filters=None, persist_path=None, collection_name=None)`.

**Semantics:** `retrieval_depth` is the deepest level to include. The function
issues one query with `{"level": {"$in": [0..depth]}}`, then sorts by score.
`None` searches all levels. Values above 2 are clamped.

**Helper:** `_where(filters, level, as_range=False)` merges equality filters
with the level clause using Chroma's `$and`, because Chroma allows only one
top-level operator.

**Returns:** `chunk_id`, `text`, `distance`, `score`, `metadata`, ordered by
descending score.

**Notes:** The level-range query replaced one-query-per-level, which was both
slower and wrong when a document lacked a requested level. `MAX_RETRIEVAL_DEPTH
= 2`.

---

## `router/`

### `router/__init__.py`

**Role:** Deterministic query classifier and gating policy.

**Public constants:** `AVAILABLE_MODULES`, `COMPLEXITY_LABELS`,
`COMPLEXITY_TO_DEPTH`, `NEGATION_WINDOW_WORDS`, `CONFIDENCE_FLOOR/CEILING`.

**Key class:** `RoutingDecision(complexity, confidence, retrieval_depth,
activated_modules)` with `to_dict()`.

**Key functions:**
- `complexity_score(query)` -> 0..1 weighted score.
- `classify(query)` -> `(label, confidence)`.
- `route(query, *, retrieval_depth_override=None)` -> `RoutingDecision`.
- `_is_negated(match, query)` / `_hint_fires(pattern, query)`: negation-aware
  hint matching.
- `_COMPOUND_OPERATION_COUNT(query)`: distinct reasoning operations.

**Scoring:** See chapter 02 for the weight table. All weights and thresholds
are named constants or settings, not inline magic numbers.

**Hints:** numeric, multi-hop, and contradiction regexes use stems so plurals
match. Hints are skipped when a negation word appears within 4 tokens before
the match. `between ... and` is bounded to <= 3 intervening words.

**Activation:** `fact_lookup` always; hints add modules above simple; complex
with no hints gets `multi_hop`. Results are deduplicated and filtered against
`AVAILABLE_MODULES`.

**Validation:** empty/non-string queries raise `ValueError`; inverted thresholds
raise `ValueError`.

---

## Compatibility facades

### `tree_store/__init__.py`

Re-exports `TreeNode` from `indexing.tree_node`. It exists so older imports
`from tree_store import TreeNode` keep working. Do not add a second data class
here. The docstring explains this history.

### `vector_store/__init__.py`

Re-exports `ChromaVectorStore` from `indexing.vector_store`. Same story: one
canonical implementation, one public alias.

---

## `scripts/`

### `scripts/index_sample_corpus.py`

**Role:** Index every `.pdf` / `.docx` under `data/sample_docs` into the
default Chroma collection.

**Behavior:** Adds the repo root to `sys.path`, parses each file, calls
`index_document()` with the file stem as `document_id`, and prints progress.
It skips `sample.docx` (a deliberately excluded fixture). Returns exit code 1
if no documents are found.

**Why it matters:** CI runs this before pytest so
`test_benchmark_dataset_chunk_ids_exist` can resolve the labelled chunk IDs.
Run it locally before the benchmark too.

### `scripts/regenerate_benchmark_chunk_ids.py`

**Role:** Rewrite `data/sample_docs/benchmark.jsonl` chunk IDs from labelled
`(filename, chunk_index)` coordinates.

**Usage:**
```bash
python scripts/regenerate_benchmark_chunk_ids.py --verify
```

**Why:** Chunk IDs are SHA-256 of `document_id:chunk_index`. If chunking or
document IDs change, the hand-written hashes go stale. This script derives them
from the labelled evidence coordinates.

---

## `tests/` at a glance

The suite is covered in detail in [07 - Testing and CI](07-testing-and-ci.md).
File-by-file:

| Test file | Covers |
|---|---|
| `tests/conftest.py` | Session model warmup, cache preservation, shared `stub_provider` fixture |
| `tests/modules_helpers.py` | Helpers/builders for module tests |
| `tests/test_analytics.py` | `/analytics/queries`, `/summary`, `/slow`, live rows |
| `tests/test_baseline.py` | Ungated baseline contract (unit, fake retriever) |
| `tests/test_batch_indexing.py` | Batch embedding and batch persistence |
| `tests/test_benchmark_dataset.py` | Dataset labels and answer regressions |
| `tests/test_benchmark_runner.py` | Runner logic, fully offline with injected embedder/retriever |
| `tests/test_builder_wiring.py` | Builder <-> summarizer <-> chunker seams |
| `tests/test_chroma_store.py` | Chroma wrapper insert/query/upsert/edge cases |
| `tests/test_cors.py` | CORS parsing, preflight headers, wildcard/credentials invariant |
| `tests/test_embeddings.py` | Provider selection, dimension agreement, stub fallback |
| `tests/test_generation.py` | Prompt building, evidence dedup, deterministic priority, LLM injection |
| `tests/test_health.py` | `/health` shape/values |
| `tests/test_indexing.py` | Ingestion and token chunking contract |
| `tests/test_indexing_pipeline.py` | End-to-end index pipeline + multipart `/index` smoke |
| `tests/test_integration.py` | Real-model index -> query -> answer; baseline comparison |
| `tests/test_logging_config.py` | Logging levels, idempotence, noisy loggers |
| `tests/test_parser.py` | PDF/DOCX text extraction and errors |
| `tests/test_query_endpoint.py` | `/query` gating, response contract, error handling |
| `tests/test_retrieval.py` | Depth semantics against a real temp Chroma |
| `tests/test_router.py` | Scores, thresholds, depth mapping, activation, negation |
| `tests/test_summarizer.py` | Recursive clustering/summarization with mock LLM |
| `tests/test_tree_node.py` | TreeNode invariants |
| `tests/test_tree_persistence.py` | Persisting nodes in isolated Chroma |
| `tests/test_chat_endpoint.py` | `/api/chat` SSE, file filters, failure paths |
| `tests/test_upload.py` | `/api/files` type/size/sniffing behavior |
| `tests/test_modules/test_*.py` | One file per specialist module, fake clients, no network |

---

## Supporting project files

| File / folder | Purpose |
|---|---|
| `data/sample_docs/` | RFCs, papers, NIST OSCAL PDF, notes, `benchmark.jsonl` |
| `data/uploads/` | Runtime upload destination created by `/api/files`; not committed |
| `frontend/` | React app; see [05](05-frontend-guide.md) and [06](06-frontend-backend-integration.md) |
| `docs/` | Design docs, interface contracts, ADRs, setup, evaluation, references |
| `.github/workflows/ci.yml` | CI: install, verify real embedder, index corpus, pytest, ruff, advisory mypy |
| `.github/PULL_REQUEST_TEMPLATE.md` | Required PR sections |
| `.github/copilot-instructions.md` | Review focus: tests, interfaces, secrets, benchmark impact |
| `pyproject.toml` | Packaging, extras, pytest/ruff/mypy/coverage config, console scripts |
| `requirements.txt` | Runtime dependencies installed by CI |
| `.env.example` | Documented defaults for every setting |
| `.gitignore` | Ignores `.env`, `.graft/`, caches, and local artifacts |
| `CONTRIBUTING.md` | Branching, ownership, commit format, review, benchmark rules |
| `README.MD` | Project pitch, high-level status, quick start |

---

Next: [04 - API reference](04-api-reference.md)
