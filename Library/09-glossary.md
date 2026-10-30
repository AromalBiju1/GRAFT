# 09 - Glossary

Terms used throughout the codebase and this Library. Each entry says what it
means **in GRAFT**, not just its general definition, and where to look.

---

## A-B

### Ablation

An experiment that disables one mechanism to measure its contribution. In
GRAFT, `benchmark.runner --ablation` runs fixed setups (different depths and
module sets) so the gated path can be compared with partial variants.
See `benchmark/runner.py`, `AblationSetup`.

### Activation (module activation)

The router's decision about which specialist modules should run for a query.
Not every module runs on every query. `activated_modules` in the routing
response is the list. See `router/__init__.py`.

### ANN (approximate nearest neighbour)

The class of vector search Chroma performs. GRAFT supplies precomputed
embeddings, so Chroma does not run its own embedding function.

### Answer synthesis

Turning retrieved passages and module results into the final `answer` string.
Implemented in `generation/__init__.py`. Deterministic by default, optionally
LLM-backed.

### Baseline (flat baseline)

The ungated comparison path in `baseline/__init__.py`: retrieve without routing,
run all four modules, generate with the same function. It answers the question
"what would a non-adaptive system do?"

---

## C

### Chunk

A token-bounded slice of a document, typically 400 tokens with 50 tokens of
overlap (`indexing/config.py`). Chunks are the level-0 leaves of the tree.

### `chunk_id`

The stable identifier of a chunk or summary node. It is a SHA-256-derived hex
string, not a sequential integer. For leaves it is derived from
`document_id:chunk_index`; changing either changes the ID. The benchmark labels
depend on these IDs.

### ChromaDB (Chroma)

The persistent vector database. Default path: `.graft/chroma`. Wrapped by
`indexing/vector_store/chroma_store.py` and re-exported from `vector_store/`.

### Collection

A named group of vectors in Chroma. GRAFT uses one collection per
embedder/dimension combination. The base name comes from `CHROMA_COLLECTION`;
the suffix is added by `embeddings.collection_name()`.

### Complexity

The router's coarse label for a query: `simple`, `moderate`, or `complex`. It
is derived from a weighted score, not from an LLM. See `router/__init__.py`.

### Confidence

A 0..1 value. In routing, it reflects distance from the tier threshold. In a
module, it reflects that module's certainty about its own result. They are
different scales and should not be compared directly.

### Context

The retrieved passages passed to modules and generation. In code, a list of
dicts with `chunk_id`, `text`, `score`, and `metadata`.

### Contradiction detection

The specialist module that looks for conflicting claims across passages. It
uses an NLI model when available, and a keyword/negation heuristic otherwise.
`modules/contradiction_detection/`.

### CORS

Cross-Origin Resource Sharing. Controls whether a browser on one origin may
call the API on another. Configured with `CORS_ORIGINS`. Wildcard mode disables
credentials.

### Coverage

A test metric measuring which lines/branches ran. Config is in
`pyproject.toml`; it is a diagnostic, not a CI gate.

### `CSV` / JSONL

The benchmark dataset is **JSONL** (one JSON object per line), not CSV. See
`data/sample_docs/benchmark.jsonl`.

---

## D-E

### Deepest-level semantics (retrieval depth)

`retrieval_depth=1` means "search levels 0 and 1", not "search only level 1".
The value is the deepest level to include. This is why a document without a
level-2 node still returns results at depth 2.

### Deterministic synthesis

The default answer mode in `generation/`. It selects from module results and
context using fixed priority rules, so the same input produces the same output.
No network, no key, no sampling.

### Dimension (embedding dimension)

The length of a vector. `all-MiniLM-L6-v2` produces 384; the stub produces 16.
Indexing and querying must use the same dimension. GRAFT enforces this by
collection naming, not by truncating vectors.

### Document ID (`document_id`)

The logical identity of a document. Used in chunk IDs, metadata, and filters.
For `/index` uploads it comes from the filename stem; for `/api/files` it is
the generated upload ID. It is not necessarily a filename.

### Embedding

A numeric vector representing text. GRAFT embeds leaves at indexing time and
the query at request time, using the same `embed_text()`.

### Evidence

The passages and module findings returned to the caller. Deduplicated by
`chunk_id`/`node_id` in generation. The API truncates evidence text to 600
characters.

---

## F-G

### Fact lookup

The always-on specialist module: return the highest-scoring passage as a direct
factual answer. `modules/fact_lookup/`.

### Flat RAG

The conventional retrieve-top-k-then-answer pattern. GRAFT's baseline is a flat
RAG variant with all modules enabled, used for comparison.

### Gating

The core GRAFT idea: decide per query how much retrieval and reasoning to spend.
The router performs the gating.

### Generation

See answer synthesis. The package is `generation/`; the API calls
`generation.synthesize()`.

### GRAFT

Gated Retrieval Activation Framework for Trees. The project in this repository.

### Grounding instruction

The prompt text that tells an LLM to answer only from retrieved context and not
use outside knowledge. It is used only in LLM mode.

---

## H-L

### Hierarchical tree

The RAPTOR-style structure: leaves at level 0, summaries above them, one root
at the top. Retrieval can search a prefix of levels.

### Hybrid retrieval

Combining dense vectors with lexical/BM25 search. GRAFT does **not** currently
do this; it is dense-only. Do not assume a keyword fallback exists.

### LLM (large language model)

An optional answer writer or summarizer. Providers supported by
`generation.create_llm_client()`: `gemini`, `openai`, `groq`, `ollama`,
`llamacpp`, and `local` (an injected callable). The API only builds a client
when `LLM_ENABLED=true`.

### `LLM_ENABLED`

The switch that makes `/query` construct and use an LLM client. It is `false`
by default. Setting `LLM_PROVIDER` alone does not enable it.

### LLM judge

An optional benchmark helper that scores answer quality with a model. It is
separate from the deterministic token-F1 metric and is skipped when no client
is configured. See `benchmark/prompts.py`, `benchmark/runner.py`.

### Latency

Elapsed wall-clock time. `/query` measures route + retrieval + modules +
generation + logging in milliseconds. It does not include network time before
the server received the request.

### Leaf

A level-0 node, produced directly from a chunk. Leaves have no children in the
tree (their `child_ids` list is empty).

---

## M-O

### Metadata

Key/value data stored alongside a vector in Chroma. GRAFT uses `node_id`,
`document_id`, `level`, `node_type`, `parent_id`, and `child_ids`. Filters match
these fields exactly.

### Module (specialist module)

A reasoning component with a common contract: `fact_lookup`, `multi_hop`,
`numeric_reasoning`, `contradiction_detection`. A module returns a
`ModuleResult`, not a bare string.

### `ModuleResult`

The shared output dataclass in `modules/base.py`:
`request_id`, `module`, `result`, `evidence`, `confidence`, `metadata`.

### Multi-hop

Reasoning that needs more than one passage. The current module concatenates the
top passages; it does not iteratively retrieve. `modules/multi_hop/`.

### `n_results`

How many nearest neighbours to request from Chroma. `/query` uses 5. The
benchmark uses its own setting (`GRAFT_N_RESULTS = 5`).

### NLI (natural language inference)

A model task that classifies a pair of sentences as entailment, neutral, or
contradiction. Optional in contradiction detection
(`microsoft/deberta-v3-mnli`), with a heuristic fallback.

### Node

A vertex in the document tree. Leaves are chunks; non-leaves are summaries. The
dataclass is `TreeNode` in `indexing/tree_node.py`.

### `node_id`

The identifier stored on a tree node. In the current pipeline it matches
`chunk_id` for persisted nodes. Do not confuse it with `document_id`.

### Ollama

A local LLM server with an OpenAI-compatible API. Enabled with
`LLM_PROVIDER=ollama`, default base URL `http://localhost:11434/v1`. Uses
`httpx`, no SDK.

### OpenAI

A hosted LLM provider. Requires the optional `openai` package and
`OPENAI_API_KEY`. Enabled with `LLM_PROVIDER=openai`.

---

## P-R

### Parent/child link

Tree edges. Every non-root node has at most one `parent_id`; every summary has
`child_ids`. Links are bidirectional and enforced by tests.

### Precision@k

In the benchmark, the fraction of the top-k retrieved chunk IDs that are in the
labelled relevant set. `k=5` by default. See
`benchmark.retrieval_precision_at_k()`.

### Provenance

Where a passage came from: `source`, `page`, `document_id`. It travels in
metadata from parsing to retrieval to evidence.

### `query_logs`

The SQLite table written by `/query` and read by `/analytics/*`. Columns
include `query_text`, `system`, `timestamp`, `latency_ms`, `modules_fired`,
`retrieval_depth`, and `accuracy_flag`.

### RAG

Retrieval-Augmented Generation: retrieve context, then generate an answer with
it. GRAFT is a RAG architecture with adaptive retrieval.

### RAPTOR

The tree-building approach GRAFT borrows: recursively cluster and summarize
chunks into a hierarchy. GRAFT's implementation is `indexing/summarizer.py`.

### Request ID (`request_id`)

A per-request identifier, either client-supplied or generated as
`req_<10 hex>`. It propagates through routing, modules, generation, and logs.
It is not a session ID.

### Retrieval

Finding relevant nodes for a query embedding. `retrieval/__init__.py`.

### Retrieval depth

See deepest-level semantics. Values 0, 1, 2 map to leaves, +summaries, +root.

### Root

The single top-level summary node (`parent_id is None`). Every document tree
has one.

### Router

The deterministic classifier and gating policy in `router/`. It returns a
`RoutingDecision`.

---

## S

### Sentence-transformers

The library used to load `all-MiniLM-L6-v2`. If it is unavailable, GRAFT falls
back to the stub embedder.

### SSE (Server-Sent Events)

A one-way streaming format: `event:` and `data:` lines, blank line between
events. Used by `/api/chat`. Not used by `/query`.

### Stub embedder

A deterministic 16-dimensional SHA-256 hash used for offline tests. It is not
semantic. `/health` reports `stub`, and the collection name ends in `_stub_16`.

### Stub summarizer

The deterministic extractive summarizer used when no `llm_client` is passed to
the tree builder. It joins and truncates child text; it does not paraphrase.

### Summary node

A non-leaf node created by clustering and summarizing its children. Level 1+
in the tree.

### SPA (single-page application)

The React app. It is served by Vite in development and as static files in
production; FastAPI does not serve it.

---

## T-Z

### Token

A unit produced by the `cl100k_base` tokenizer (the one used by GPT-style
models). GRAFT chunk sizes and summary limits are measured in tokens, not words
or characters.

### Token F1

The benchmark's deterministic answer metric: lower-cased word-token overlap
between the answer and `expected_answer`, computed as an F1 score. It is a
rough regression signal, not a semantic judgment.

### Tree node

See node. The canonical type is `TreeNode`.

### UMAP

A dimensionality-reduction method available as a clustering option
(`umap_gmm`) in the summarizer. It is optional; GMM is the default.

### Upsert

Insert-or-replace by key. Chroma upserts by `chunk_id`. It does not delete
nodes that are absent from the new version.

### Vector store

The persistence layer for embeddings. Canonical implementation:
`indexing/vector_store/chroma_store.py`. Public alias: `vector_store/`.

### Vite

The frontend dev server and build tool. Its proxy config forwards `/query`,
`/api`, `/health`, and `/index` to the backend, so the browser can use relative
URLs. Vite 7 requires Node `^20.19.0` or `>=22.12.0`.

### `VITE_API_URL`

A build-time frontend environment variable. When empty, requests are relative
and rely on the Vite proxy or a production reverse proxy. When set, the browser
calls that origin directly (CORS applies).

### Warmup

Loading the embedder and opening Chroma at API startup instead of on the first
request. Controlled by `WARMUP_ON_STARTUP`.

### `where` / filters

Chroma's metadata filter syntax. GRAFT builds `{"level": {"$in": [...]}}` and
merges user equality filters with `$and`. `retrieval._where()` is the helper.

---

## Quick acronym table

| Acronym | Expansion |
|---|---|
| ANN | Approximate nearest neighbour |
| API | Application programming interface |
| CORS | Cross-Origin Resource Sharing |
| GRAFT | Gated Retrieval Activation Framework for Trees |
| JSONL | JSON Lines |
| LLM | Large language model |
| NLI | Natural language inference |
| RAG | Retrieval-Augmented Generation |
| RAPTOR | Recursive Abstractive Processing for Tree-Organized Retrieval |
| SSE | Server-Sent Events |
| SPA | Single-page application |
| UMAP | Uniform Manifold Approximation and Projection |

---

Back to [the Library index](README.md).
