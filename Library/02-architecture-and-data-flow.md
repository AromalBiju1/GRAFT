# 02 - Architecture and data flow

This chapter explains what happens inside GRAFT, in the order it happens. It is
the conceptual companion to [03 - Backend reference](03-backend-reference.md),
which lists every file.

The design in one sentence: **build a tree once, then spend only the retrieval
and reasoning effort that each question actually needs.**

---

## 1. The problem GRAFT is solving

Conventional RAG treats every question the same:

```text
question -> embed -> retrieve top-k chunks -> put them in a prompt -> answer
```

That is simple and robust, but it has two costs:

- **Easy questions overpay.** "What is the capital of France?" retrieves the
  same amount of context as "compare these three contracts and flag every
  conflict."
- **Hard questions are underserved.** A flat chunk list has no summaries, no
  document-level view, and no mechanism to notice that two passages disagree.

GRAFT addresses both with two ideas:

1. **Hierarchical trees (RAPTOR-style).** Leaves are chunks; higher levels are
   summaries of groups of children; the top is a root summary. Retrieval can
   stop at the leaves for a simple question or walk deeper for a broad one.
2. **Gating.** A cheap router classifies the question and decides both the
   retrieval depth and which specialist modules to run. Everything not
   activated is skipped.

A flat baseline is kept alongside the gated path so the project can measure
whether gating actually helps.

---

## 2. The components

```text
                         +----------------------+
                         |     config.py        |
                         |  settings from .env  |
                         +----------+-----------+
                                    |
        +---------------------------+---------------------------+
        |                                                       |
        v                                                       v
+----------------+                                     +------------------+
|   indexing/    |                                     |     router/      |
| parse -> chunk |                                     | score -> tier    |
| -> embed       |                                     | -> depth/modules |
| -> cluster     |                                     +--------+---------+
| -> summarize   |                                              |
| -> persist     |                                              v
+-------+--------+                                     +------------------+
        |                                              |   retrieval/     |
        v                                              | depth-aware ANN  |
+----------------+       +------------------+          +--------+---------+
|  ChromaDB      |<------+  embeddings.py   +------------------>|
|  (.graft/      |       | MiniLM or stub   |                   |
|   chroma)      |       +------------------+                   v
+----------------+                                     +------------------+
        ^                                              |    modules/      |
        |                                              | only activated   |
        |                                              +--------+---------+
        |                                                       v
+----------------+                                     +------------------+
|  baseline/     |                                     |   generation/    |
| flat retrieval |                                     | deterministic or |
| all modules    |                                     | optional LLM     |
+----------------+                                     +--------+---------+
                                                                |
                                                                v
                                                       +------------------+
                                                       |  api/main.py     |
                                                       |  response + log  |
                                                       +------------------+
```

Ownership boundaries matter in this repository. Each top-level package is a
pipeline stage with a narrow public surface, documented in
`docs/interfaces.md`. If you change a data shape at a boundary, update that
contract and tell the adjacent owner.

---

## 3. Write path: indexing a document

Entry points:

- `POST /index` with JSON or multipart (`api/main.py`)
- `indexing.pipeline.index_document(text, ...)` from Python
- `python scripts/index_sample_corpus.py` for the bundled corpus
- `graft-index` console script, if the package is installed editable

### Step by step

1. **Parse** (`indexing/parser.py`).
   `parse_document(path)` extracts text from `.pdf` (pypdf) or `.docx`
   (python-docx). Unsupported extensions raise `ValueError`;
   unreadable/empty documents raise `DocumentParsingError` (a `ValueError`
   subclass). The HTTP layer maps these to 400/422/500 as appropriate.

2. **Chunk** (`indexing/chunker.py`).
   Text is split into token windows using `tiktoken`'s `cl100k_base`
   encoding. Defaults are 400 tokens per chunk with 50 tokens of overlap
   (`indexing/config.py`). Splits prefer paragraph and sentence boundaries, so
   chunks are not arbitrary character slices. Each chunk gets a stable
   `chunk_id` of the form `sha256(f"{document_id}:{chunk_index}")`, plus
   `document_id`, `text`, `chunk_index`, and `token_count`.

3. **Embed** (`embeddings.py`).
   Every leaf is embedded with the same function the query path uses. With
   `sentence-transformers` and the default model, vectors are 384-dimensional.
   If the model cannot load, GRAFT falls back to a deterministic 16-dimensional
   SHA-256 hash. That keeps CI and offline machines runnable, but the vectors
   are semantically meaningless. Collection names include the provider and
   dimension, so the two modes never share a collection by accident.

4. **Build the tree** (`indexing/builder.py` + `indexing/summarizer.py`).
   Leaves are grouped by the `RecursiveSummarizer`:
   - default clustering is GMM (`DEFAULT_CLUSTER_METHOD = "gmm"`), with
     `kmeans` and `umap_gmm` alternatives;
   - if embeddings are unavailable, uniform, or scikit-learn is missing, it
     falls back to sequential groups of `DEFAULT_MIN_CLUSTER_SIZE` (3);
   - each group is summarised by an LLM client, or by the deterministic
     **stub summariser** when none is provided;
   - the summary becomes a parent: every child's `parent_id` points to it, and
     the parent stores `child_ids`;
   - the process repeats on the new layer until one root remains, or a layer of
     at most 3 nodes is collapsed into a final root. `MAX_TREE_DEPTH = 20`
     guards against runaway recursion.

   The default stub summariser joins child text and truncates it
   (`STUB_SUMMARY_MAX_CHARS = 600`). It keeps the same prompt path but produces
   extractive, not abstractive, summaries. Pass a real `llm_client` to
   `index_document` / `build_tree` for genuine summaries.

5. **Persist** (`indexing/store.py` + `indexing/vector_store/chroma_store.py`).
   All nodes in a tree are validated first, then written in one Chroma
   `insert_many` batch. Each record contains the node ID (as `chunk_id`), text,
   embedding, and metadata:

   ```text
   node_id, document_id, level, node_type (leaf|summary),
   parent_id, child_ids (comma-joined)
   ```

   Upsert semantics: `chunk_id` is the key. Re-indexing the same document ID
   replaces matching nodes, but does not delete old nodes that no longer exist
   in the new version. Clear the collection or use a new ID when replacing a
   document.

### Tree shape

```text
level 2 (root)          [root summary]
                         /          \
level 1 (summaries)  [summary A]  [summary B]
                      /    \        /    \
level 0 (leaves)   [c0]  [c1]   [c2]  [c3]
```

Invariants enforced by the implementation and tests:

- exactly one root (`parent_id is None`);
- each non-root node has at most one parent;
- parent and child links are bidirectional;
- node IDs are unique across documents (they include the document ID);
- every persisted node has an embedding;
- a single-chunk document still gets a summary/root above the leaf.

---

## 4. Read path: answering a query

Entry point: `api.query_runner.execute_query()`, called by `POST /query` and
`POST /api/chat`. The benchmark has its own runner but uses the same router,
retrieval, modules, and generation functions.

### Step by step

```text
request
  |
  v
1. validate query
  |
  v
2. route(query, retrieval_depth_override?)
  |     complexity, confidence, depth, activated_modules
  v
3. embed query (same embedder as indexing)
  |
  v
4. retrieve(query_embedding, n_results=5, retrieval_depth=depth, filters)
  |     nodes at levels 0..depth, score-sorted
  v
5. for each activated module: run(request_id, query, retrieved)
  |
  v
6. synthesize(request_id, query, retrieved, module_results, llm_client)
  |
  v
7. log_query(...) best-effort
  |
  v
8. return {answer, evidence, routing, latency_ms, request_id}
```

### 4.1 Router

`router.route()` is deterministic and dependency-free. It returns:

```python
RoutingDecision(
    complexity="simple" | "moderate" | "complex",
    confidence=0.0..1.0,
    retrieval_depth=0 | 1 | 2,
    activated_modules=["fact_lookup", ...],
)
```

Complexity is a weighted sum of six signals, clamped to 0..1:

| Signal | Weight | How it is measured |
|---|---:|---|
| Length | 0.35 | word count / 25, capped at 1 |
| Clauses | 0.25 | count of `;`, `,`, ` and `, ` then `, ` also `, ` plus `, ` as well as ` / 3, capped |
| Extra operations | 0.12 per op | distinct among compare, explain, enumerate, aggregate, detect_conflict, beyond the first, capped at 2 |
| Numeric intent | 0.30 | digits, `%`/currency, table/column/sum/average/total |
| Multi-hop intent | 0.35 | compare/contrast/differences/relationships/across/between...and/both/multiple |
| Contradiction intent | 0.35 | conflict/contradict/inconsist/disagree/versus/vs/outdated/superseded/obsolete |

Thresholds are `settings.router_threshold_simple` (0.35) and
`settings.router_threshold_complex` (0.65):

- `score < 0.35` -> `simple`
- `score > 0.65` -> `complex`
- otherwise -> `moderate`

Confidence is derived from the distance to the nearest threshold. A query that
sits exactly on a boundary reports low confidence, which is useful when
evaluating whether the classifier is making a close call.

**Negation-aware hints.** A hint match preceded within 4 word tokens by a
negation (`not`, `no`, `never`, `without`, `isn't`, `don't`, and similar) does
not count as intent. So "This is not a comparison, just tell me the capital of
France" stays simple instead of activating `multi_hop`. The `between ... and`
pattern is bounded to at most 3 intervening words, so unrelated sentence
fragments do not trigger multi-hop.

**Depth mapping** is fixed:

| Complexity | Depth | Levels searched |
|---|---:|---|
| simple | 0 | leaves only |
| moderate | 1 | leaves + summaries |
| complex | 2 | leaves + summaries + root |

`retrieval_depth_override` (the `retrieval_depth` request field) bypasses the
mapping for ablations. `settings.default_retrieval_depth` exists but is not used
by the router; the tier mapping is authoritative.

**Module activation** is tier-gated:

- `fact_lookup` always runs.
- For `moderate` and `complex`, hint matches add `contradiction_detection`,
  `numeric_reasoning`, and/or `multi_hop`.
- A `complex` query with no hint matches gets `multi_hop` as a default
  reasoning pass.
- A `simple` query never gets anything besides `fact_lookup`.

The order is stable and deduplicated: `fact_lookup` first, then contradiction,
numeric, multi-hop in the order they are checked.

Worked examples (values from the current router):

```text
"What is the capital of France?"
  score 0.084                                    -> simple
  depth 0, modules [fact_lookup]

"Compare the two documents."
  score 0.406                                    -> moderate
  depth 1, modules [fact_lookup, multi_hop]

"Compare the two documents and explain every difference in detail."
  score 0.8133                                   -> complex
  depth 2, modules [fact_lookup, multi_hop]

"Compare TLS 1.2 and TLS 1.3 key exchange."
  score 0.8453                                   -> complex
  depth 2, modules [fact_lookup, numeric_reasoning, multi_hop]

"This is not a comparison, just tell me the capital of France in one sentence."
  score 0.2933; negated "comparison" is vetoed   -> simple
  depth 0, modules [fact_lookup]
```

The TLS example shows why numeric intent is separate from multi-hop: the
version numbers trigger `numeric_reasoning`, while the comparison triggers
`multi_hop`, and the combination crosses the complex threshold.

### 4.2 Retrieval

`retrieval.retrieve()` treats `retrieval_depth` as the **deepest level to
consider**, not the only level. It performs one Chroma query with a
`{"level": {"$in": [0..depth]}}` filter, then sorts by score. This matters
because a short document may not have a level-2 node at all; searching only
level 2 would return nothing.

- `n_results=5` from `/query`.
- Additional filters are equality metadata filters, merged with the level
  filter using Chroma's `$and` (Chroma accepts only one top-level operator).
- Results are dictionaries with `chunk_id`, `text`, `distance`, `score`, and
  `metadata` (which includes `document_id`, `level`, `node_id`, `parent_id`,
  and `child_ids`).
- Depth is clamped to `MAX_RETRIEVAL_DEPTH = 2`.

### 4.3 Specialist modules

All modules receive `(request_id, query, retrieved)` and return a
`ModuleResult` with a common shape:

```python
{
    "request_id": str,
    "module": str,
    "result": str | dict,
    "evidence": list[dict],
    "confidence": 0.0..1.0,
    "metadata": dict,
}
```

| Module | What it does now | Upgrade path |
|---|---|---|
| `fact_lookup` | Picks the highest-scoring chunk and returns its text (up to 800 chars) as the answer | LLM synthesis over evidence |
| `multi_hop` | Concatenates the top 3 passages into a joint chain; needs >= 2 passages | Query decomposition + iterative retrieval |
| `numeric_reasoning` | Extracts numbers, then sums/averages/differences based on keywords | Table-aware parsing and arithmetic |
| `contradiction_detection` | Compares overlapping passage pairs. Uses an NLI model when installed (`microsoft/deberta-v3-mnli`), otherwise a keyword-overlap + negation heuristic | Better NLI thresholds and claim extraction |

These are deliberately simple, deterministic heuristics. The interfaces are the
stable part; each module can be upgraded without changing the pipeline.

### 4.4 Generation

`generation.synthesize()` has two modes:

**Deterministic (default).** No network, no key. It builds evidence by
deduplicating retrieved context and module evidence by `chunk_id`/`node_id`,
then chooses an answer in this priority order:

1. a contradiction summary if the contradiction module found conflicts;
2. the `fact_lookup` result;
3. the first module result string;
4. the first context passage (truncated);
5. `"No answer could be generated from the available context."`

**LLM (opt-in).** If a client is provided, `build_prompt()` renders a grounded
prompt with the first 5 context passages and the module results as JSON, then
calls the client. Supported providers are Gemini and OpenAI (optional SDKs),
Groq, Ollama, and llama.cpp (OpenAI-compatible HTTP through `httpx`), plus an
injected callable or object with `.generate(prompt)`. Any provider failure is
converted to a generic error so credentials cannot leak into a response.

### 4.5 Logging and response

`/query` logs to SQLite through `db.logger.log_query()`. Logging is
best-effort: a failure is logged as a warning but does not fail the request.
The response includes `request_id`, `answer`, `evidence`, `routing`, and
`latency_ms`. Evidence text is truncated to 600 characters at the API boundary.

---

## 5. The baseline

`baseline.run_baseline()` is the comparison path:

- retrieves at a caller-supplied depth without routing;
- runs **all four** modules, every time;
- uses the same generation function;
- returns `request_id`, `answer`, `evidence`, `module_results`, `retrieved`,
  and latency metadata.

It is a Python library, not an HTTP route. `api/main.py`'s module docstring
mentions `POST /baseline/query`, but no such route exists. The benchmark runner
calls `run_baseline()` directly. This is intentional for now: the benchmark is
the only consumer.

---

## 6. Storage and configuration

### ChromaDB

- Default path: `.graft/chroma`.
- Collection name:
  - stub: `<base>_stub_16`
  - real model: `<base>_<model-slug>_<dimension>`, e.g.
    `graft_tree_nodes_all-minilm-l6-v2_384`.
- One collection can contain many documents; documents are separated by
  `document_id` metadata. Retrieval filters can target it.
- The vector store caches one client per `(path, collection)` for the process
  and exposes `reset_shared_cache()` for tests/shutdown.

### SQLite

- Default file: `db/graft_logs.db`.
- Table: `query_logs`, with `query_text`, `system`, `timestamp`, `latency_ms`,
  `modules_fired` (JSON text), `retrieval_depth`, and `accuracy_flag`.
- `db/logger.py` currently hard-codes `Path(__file__).parent / "graft_logs.db"`.
  The `DB_PATH` setting is not consulted. If you need a different location,
  change the logger or add support there.

### Configuration precedence

`Settings` reads, in order: environment variables, then `.env`, then defaults.
Names are case-insensitive. There is no `GRAFT_` prefix except for
`GRAFT_LOG_LEVEL`, which `logging_config.py` reads directly.

---

## 7. Failure behavior

| Failure | Behavior |
|---|---|
| Empty query | HTTP 422 `query must not be empty` |
| Retrieval error | HTTP 500 `retrieval failed: ...` |
| Module error | HTTP 500 `module <name> failed: ...` |
| Generation error | HTTP 500 `generation failed: ...` |
| Logging error | Request still succeeds; warning logged |
| Empty collection | HTTP 200, answer says no context/answer could be generated |
| Warmup error | Server starts anyway; warning logged |
| Stub embedder | Server starts; `/health` reports `stub`; retrieval is not semantic |
| Upload parse error | 422 for unreadable/empty files, 500 for unexpected parse failures |
| Unsupported upload extension | 400 on `/index`; 415 on `/api/files` |

The project convention is to fail loudly during development rather than hide
errors behind empty defaults. Some fallbacks (stub embedder, deterministic
summaries) are deliberate offline capabilities, not silent error swallowing.

---

## 8. Where to change what

| If you want to change... | Edit... | Also check... |
|---|---|---|
| How documents are parsed | `indexing/parser.py` | `tests/test_parser.py` |
| Chunk size/overlap | `indexing/config.py` | `tests/test_indexing.py` |
| Tree clustering/summaries | `indexing/summarizer.py` | `tests/test_summarizer.py`, `tests/test_builder_wiring.py` |
| Vector persistence | `indexing/store.py`, `indexing/vector_store/chroma_store.py` | `tests/test_chroma_store.py`, `tests/test_tree_persistence.py` |
| Complexity thresholds | `config.py` or `router/__init__.py` constants | `tests/test_router.py` |
| Which modules fire | `router/__init__.py` | `docs/interfaces.md`, benchmark owner |
| Retrieval depth semantics | `retrieval/__init__.py` | `tests/test_retrieval.py` |
| A module's reasoning | the relevant `modules/*/__init__.py` | its test file, `docs/interfaces.md` §7 |
| Answer wording | `generation/__init__.py` | `tests/test_generation.py` |
| API response shape | `api/schemas.py`, `api/main.py` | `docs/interfaces.md` §9-10, frontend |
| Query logging/analytics | `db/logger.py`, `api/routes/analytics.py` | `tests/test_analytics.py` |
| Benchmark metrics | `benchmark/runner.py` | `docs/evaluation.md`, tests |

Because router and indexing changes affect benchmark results, flag them to the
benchmarking owner before merging. `CONTRIBUTING.md` requires it.

---

Next: [03 - Backend reference](03-backend-reference.md)
