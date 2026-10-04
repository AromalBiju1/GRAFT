# GRAFT Interface Contracts

This document defines the input and output contracts between the major
components of the GRAFT system.

The purpose of these contracts is to allow different components to be
developed independently while maintaining compatibility during integration.

## 1. General Conventions

- Data exchanged between components should use JSON-compatible structures.
- Field names use `snake_case`.
- Each request should contain a unique `request_id`.
- Components should depend only on the defined interface, not on the internal
  implementation of another component.
- Changes to required fields or field meanings should be discussed with the
  dependent component before merging.

---

## 2. Indexing Pipeline → Tree Store

The indexing pipeline processes documents and constructs the hierarchical
document tree.

### Input

    {
      "document_id": "doc_001",
      "text": "Document text...",
      "metadata": {
        "source": "example.pdf",
        "page": 1
      }
    }

### Ingestion chunks (Issue #13)

Call `indexing.ingest.ingest_document(filepath, document_id="doc_001")`
with a PDF or DOCX path to obtain `list[dict]`. Supply the source document's
unique ID; the upload route already derives IDs from sanitized filenames and
disambiguates duplicates. This helper does not generate a competing ID system.
Parsing alone is available as `indexing.parser.parse_document(filepath)`;
raw text can be chunked with `indexing.chunker.chunk_text(text, document_id=...)`.

```python
{
    "chunk_id": str,
    "document_id": str,
    "text": str,
    "chunk_index": int,
    "token_count": int,
}
```

| Field | Meaning |
|---|---|
| `chunk_id` | SHA-256 hex digest of UTF-8 `f"{document_id}:{chunk_index}"`; identical ID and index always produce the same chunk ID |
| `document_id` | Caller-supplied unique source document identifier |
| `text` | Extracted text slice, with outer whitespace trimmed and internal formatting preserved |
| `chunk_index` | Contiguous zero-based position in the document |
| `token_count` | Actual `cl100k_base` token count of `text` |

Tokenization uses `tiktoken.get_encoding("cl100k_base")`, treating literal
special-token strings as ordinary document text. Defaults are approximately
400 tokens per chunk and 50 tokens of overlap (`chunk_size=400, overlap=50`).
Chunk ends prefer paragraph boundaries, then sentence boundaries. Overlap
reuses the whole-sentence suffix closest to 50 tokens that leaves room for
new content; it can be smaller or zero when sentences are long. A single
sentence exceeding the target is emitted intact without overlap, so 400 is
a soft target, not a strict upper bound. Sentence boundaries use punctuation
followed by whitespace (a heuristic, not a linguistic sentence tokenizer).

PDF pages and DOCX paragraphs are extracted in order, separated by blank
lines; empty paragraphs/pages are skipped. No OCR is performed.
`DocumentParsingError` (a `ValueError` subclass) covers empty, non-extractable,
missing, unreadable and corrupted documents; underlying parser failures are
chained. Unsupported extensions raise a clear `ValueError`.

Compatibility note: `chunk_text` still accepts `source`, but returns only the
five fields above. Its previous `metadata`/word-count output and positional
chunk IDs are replaced by this contract. Existing tree builders accept these
chunks, but external consumers of the old metadata must migrate before merge.
Existing callers that explicitly set chunk sizes now specify token counts.

### Tree Node

`indexing.tree_node.TreeNode` is a Python dataclass. The MVP uses a strict
tree with hard clustering: each node has at most one `parent_id`, and
`child_ids` is a list of child node identifiers. Multi-parent nodes are not
supported. Callers supply identifiers and maintain parent-child links.

The `to_dict()` tree-store representation is:

    {
      "node_id": "node_001",
      "level": 0,
      "text": "Chunk or summary text...",
      "parent_id": null,
      "child_ids": [],
      "metadata": {
        "document_id": "doc_001",
        "source": "example.pdf",
        "page": 1
      }
    }

| Field | Type | Description |
|---|---|---|
| `node_id` | string | Unique tree node identifier |
| `level` | integer | Tree level; chunks are level 0, summaries use higher levels |
| `text` | string | Text represented by the node |
| `embedding` | list[float]/null | Optional precomputed embedding; defaults to `None`, excluded from `to_dict()` |
| `parent_id` | string/null | Single parent identifier; defaults to `None` for an unlinked node or root |
| `child_ids` | list[string] | Child identifiers; defaults to a new empty list per node |
| `metadata` | object | Source and processing metadata; defaults to a new empty dictionary per node |

`node_id`, `text`, and `level` are required constructor arguments. Source
document identifiers can be stored in `metadata["document_id"]`; there is no
top-level `document_id` field on `TreeNode`.

The `is_leaf` property is true when `level == 0` or `child_ids` is empty.
`to_dict()` returns exactly `node_id`, `text`, `level`, `parent_id`,
`child_ids`, and `metadata`. Embedding generation, clustering, summarization,
and persistence are outside this data structure's responsibilities.

---

## 3. Indexing Pipeline → Vector Store

The indexing pipeline supplies precomputed embeddings for searchable document
chunks and higher-level summary nodes.

### Input

    {
      "chunk_id": "chunk_001",
      "document_id": "doc_001",
      "text": "Chunk text...",
      "embedding": [0.012, -0.034, 0.056],
      "metadata": {
        "node_id": "node_001",
        "level": 0,
        "source": "example.pdf",
        "page": 1
      }
    }

| Field | Type | Description |
|---|---|---|
| `chunk_id` | string | Unique chunk identifier |
| `document_id` | string | Source document identifier |
| `text` | string | Original chunk text |
| `embedding` | array[number] | Vector representation of the chunk |
| `metadata` | object | Tree and source information |

The vector store should store the embedding together with its associated
metadata and return matching chunks and relevance scores during retrieval.

### Persisting TreeNode objects

`indexing.store.persist_tree_nodes(nodes, vector_store)` persists existing
`TreeNode` objects through `ChromaVectorStore.insert(chunk_id, document_id,
text, embedding, metadata)`. It uses `node.node_id` as `chunk_id`, so inserting
the same node ID again updates the record rather than creating a duplicate.
It does not generate embeddings. An embedding of `None` raises
`ValueError("Node <node_id> missing required embedding vector.")`.

The adapter stores only the following metadata; arbitrary node metadata
(including nested dictionaries) is not forwarded:

| Metadata field | Type | Stored value |
|---|---|---|
| `node_id` | string | `node.node_id`, also used as the record ID |
| `document_id` | string | `node.metadata["document_id"]`; must be non-empty |
| `level` | integer | Level 0 is a leaf/document chunk; levels 1+ are summary layers |
| `node_type` | string | `"leaf"` when `node.is_leaf`, otherwise `"summary"` |
| `parent_id` | string | Single parent ID, or `""` when absent |
| `child_ids` | string | Child IDs joined with commas, or `""` when empty |

The existing `TreeNode.is_leaf` contract also treats a node with no children
as a leaf, even at a higher level. Child IDs should not contain commas if
consumers need to split the stored string back into IDs.

The wrapper copies the separate `document_id` argument into stored metadata.
Although the adapter defaults a missing document ID to `""`, the wrapper
rejects missing, empty, or whitespace-only document IDs with `ValueError`.
Callers must supply a non-empty document ID for every node, including summaries.
Writes are sequential: an error does not roll back previously persisted nodes.

Use the wrapper's metadata filter to select a tree level:

```python
leaves = vector_store.query([1.0, 0.0], n_results=5, filters={"level": 0})
summaries = vector_store.query([1.0, 0.0], n_results=5, filters={"level": 1})
```

Query embeddings must match the stored embedding dimensions. Results contain
`chunk_id`, `text`, `distance`, `score`, and `metadata`.

---

## 4. Router → Retrieval

The router determines how the query should be retrieved from the document
tree.

### Request

    {
      "request_id": "req_001",
      "query": "What are the main differences between the two policies?",
      "retrieval_depth": 2
    }

| Field | Type | Description |
|---|---|---|
| `request_id` | string | Unique request identifier |
| `query` | string | User query |
| `retrieval_depth` | integer | Maximum retrieval depth |

### Complexity → depth mapping

`router.route` maps the classified complexity to a retrieval depth. This
is the core gating contract — a simple query must stay at the leaves.

| Complexity | `retrieval_depth` | Meaning |
|---|---|---|
| `simple` | 0 | Leaf chunks only |
| `moderate` | 1 | Leaves + first summary layer |
| `complex` | 2 | Leaves + all summary layers up to the root |

The mapping is exported as `router.COMPLEXITY_TO_DEPTH`. A
`retrieval_depth_override` keyword (or the request field) forces a depth for
ablation without changing classification.

Thresholds live in `config.py` as `router_threshold_simple` (0.35) and
`router_threshold_complex` (0.65); the score is `router.complexity_score`
and the signal weights are named constants in the same module.

---

## 5. Retrieval → Router

The retrieval component returns relevant document chunks to the router.

### Depth semantics

`retrieval_depth` is the **deepest** level to consider, not the only level.
`retrieval.retrieve` queries the requested level first, then walks
progressively shallower levels down to the leaves, merges the hits, dedupes by
`chunk_id`, and returns the top `n_results` ordered by descending score.

Restricting to a single exact level returned nothing whenever a document's
tree lacked that level — a one-chunk document only has levels 0 and 1, so a
depth-2 query found zero rows and answered "No context provided." Level 0
remains the floor, so a `simple` query's retrieval is still genuinely shallow.

Depths above `retrieval.MAX_RETRIEVAL_DEPTH` (2) are clamped, as are
negative depths (clamped to 0).

### Filters

Additional equality filters are combined with the level filter using Chroma's
`$and` operator. A flat merge such as `{"document_id": "doc_001", "level": 1}`
is rejected by Chroma with
`ValueError: Expected where to have exactly one operator`.

### Embeddings and collection naming

Query and index embeddings come from the same entry point,
`embeddings.embed_text`, so their dimensions cannot diverge. The active
Chroma collection is qualified by the embedder via
`embeddings.collection_name()`:

| Provider | Collection name | Dimension |
|---|---|---|
| `stub` | `graft_tree_nodes_stub_16` | 16 |
| `sentence-transformers` (`all-MiniLM-L6-v2`) | `graft_tree_nodes_all-minilm-l6-v2_384` | 384 |

Chroma fixes an embedding dimension per collection, so switching embedder
starts a new collection instead of raising
`InvalidArgumentError: Collection expecting embedding with dimension of 16, got 384`.
`GET /health` reports `embedding_provider` and `embedding_dim`; a `stub` value
means retrieval is running on hash vectors and answers are not semantically
meaningful.

### Response

    {
      "request_id": "req_001",
      "results": [
        {
          "chunk_id": "chunk_001",
          "text": "Retrieved passage...",
          "score": 0.91,
          "metadata": {
            "document_id": "doc_001",
            "source": "example.pdf",
            "page": 2,
            "node_id": "node_001",
            "level": 0
          }
        }
      ]
    }

---

## 6. Router → Specialist Modules

The router selects one or more specialist modules based on the query and
passes the relevant retrieved context to them.

### Request

    {
      "request_id": "req_001",
      "query": "Compare the revenue values in the two reports.",
      "context": [
        {
          "chunk_id": "chunk_001",
          "text": "Retrieved context...",
          "score": 0.91,
          "metadata": {
            "source": "report_a.pdf",
            "page": 4
          }
        }
      ],
      "activated_modules": [
        "numeric_reasoning"
      ]
    }

| Field | Type | Description |
|---|---|---|
| `request_id` | string | Unique request identifier |
| `query` | string | Original user query |
| `context` | array[object] | Retrieved evidence |
| `activated_modules` | array[string] | Selected specialist modules |

---

## 7. Specialist Modules

GRAFT contains the following specialist modules:

1. Fact Lookup
2. Multi-hop Reasoning
3. Numeric/Table Reasoning
4. Contradiction Detection

All specialist modules should follow a common input/output structure.

### Common Input

    {
      "request_id": "req_001",
      "query": "User query...",
      "context": []
    }

### Common Output

    {
      "request_id": "req_001",
      "module": "fact_lookup",
      "result": "Module result...",
      "evidence": [],
      "confidence": 0.92,
      "metadata": {}
    }

| Field | Type | Description |
|---|---|---|
| `request_id` | string | Original request identifier |
| `module` | string | Name of the specialist module |
| `result` | string/object | Module result |
| `evidence` | array[object] | Supporting evidence |
| `confidence` | number | Confidence score between 0 and 1 |
| `metadata` | object | Optional module-specific information |

### 7.1 Fact Lookup

Used for answering direct factual questions using retrieved evidence.

### 7.2 Multi-hop Reasoning

Used for questions requiring information from multiple passages or
documents.

### 7.3 Numeric/Table Reasoning

Used for numerical calculations, comparisons, and reasoning over tables.

### 7.4 Contradiction Detection

Used for identifying conflicting claims between retrieved sources.

`ContradictionDetectionModule(nli_client=None)` preserves the common module API
and returns `{"conflicts": [...], "summary": ...}`. An injectable `NLIClient`
exposes `predict(premise, hypothesis)` returning `{"label": str, "score": float}`.
Passage pairs with keyword overlap >= 0.25 are evaluated in context order using
their full `text` fields. NLI conflicts require a normalized `contradiction`
label and a score strictly greater than 0.7; each adds `nli_label` and `nli_score`
and uses `signal="nli_contradiction"`. Existing passage and overlap fields remain.
Unavailable NLI predictions use the original negation-mismatch heuristic.
The default model is loaded lazily, with initialization failures cached for the
process lifetime and fallback reasons logged.

---

## 8. Specialist Modules → Generation

The generation component receives the original query, retrieved context,
and results from the specialist modules.

### Request

    {
      "request_id": "req_001",
      "query": "User query...",
      "context": [],
      "module_results": []
    }

The generation component uses these results to produce the final answer.

---

## 9. Generation → API

The generation component returns the final response to the API.

### Response

    {
      "request_id": "req_001",
      "answer": "Final generated answer...",
      "evidence": []
    }

The API should expose a stable response format to the frontend.

---

### Optional LLM answer synthesis

`generation.synthesize(request_id, query, context, module_results, llm_client=None)`
preserves deterministic answer priority and evidence deduplication when no client
is supplied. An injected callable `(prompt) -> str` or object with
`.generate(prompt) -> str` enables grounded synthesis. The prompt includes the
query, the first five retrieval hits' `text` fields (retrieval already sorts by
score), and normalised module results as sorted, indented JSON. All original
evidence remains in the response, including hits beyond the prompt's five-hit limit.

Install `pip install -e ".[llm]"` for optional provider SDKs. Explicitly call
`generation.create_llm_client()` and pass its result as `llm_client` to select
`settings.llm_provider`. Gemini uses `settings.gemini_api_key` and
`settings.gemini_model`; OpenAI uses `settings.openai_api_key` and
`settings.openai_model`. SDK imports and client construction occur only inside
the factory. Missing keys/dependencies and provider failures raise clear errors;
they do not silently switch to deterministic answers. `local` requires an
injected adapter returning text; this repository has no local model loader.
The API and benchmark callers continue using the deterministic default.

LLM answer wording changes token-overlap F1. Runs using LLM synthesis should
record the provider/model and should not be compared directly with pre-LLM F1
results. Existing benchmark results are unchanged.

## 10. API → Frontend

The frontend sends the user's query to the API.

### Request

    {
      "query": "What is the main conclusion of the document?"
    }

### Response

    {
      "request_id": "req_001",
      "answer": "The main conclusion is...",
      "evidence": [
        {
          "source": "example.pdf",
          "page": 3
        }
      ]
    }

---

## 11. Error Handling

Components should return clear errors instead of silently failing.

### Example

    {
      "request_id": "req_001",
      "error": {
        "code": "INVALID_REQUEST",
        "message": "Query must not be empty."
      }
    }

Suggested error codes:

| Code | Meaning |
|---|---|
| `INVALID_REQUEST` | Invalid input |
| `RETRIEVAL_ERROR` | Retrieval failed |
| `MODULE_ERROR` | Specialist module failed |
| `GENERATION_ERROR` | Generation failed |
| `INDEXING_ERROR` | Indexing failed |
| `INTERNAL_ERROR` | Unexpected internal error |

---

## 12. Indexing API → Vector Store (File Upload)

File-based ingestion endpoint for the RAPTOR tree pipeline.

### Route

`POST /index` (multipart/form-data) — `api/routes/indexing.py`, registered in `backend/app/main.py` and `api/main.py`.

Alternative JSON route (legacy): `POST /index` with `application/json` `{"text": "...", "document_id": "...", "source": "..."}` remains on `api/main.py` for backwards compatibility.

### Multipart Request

Content-Type: `multipart/form-data`
Form fields: `files` (preferred, supports multiple) or `file` (single-file alias). Each part must be a `.pdf` or `.docx` file. Example with `curl`:

```bash
curl -X POST http://127.0.0.1:8000/index \
  -F "files=@rfc793.pdf;type=application/pdf" \
  -F "files=@sample.docx;type=application/vnd.openxmlformats-officedocument.wordprocessingml.document"
```

Validation:
- At least one file required (422 if missing).
- Only `.pdf` and `.docx` are accepted (400 for other extensions, 422 for empty or non-extractable files).
- Filename is sanitized to a `document_id` (stem, non-alphanumeric → `_`, truncated to 64 chars; duplicates get `_1`, `_2` suffixes).

### Processing Flow

1. Save each uploaded file to a temporary directory.
2. `indexing/ingest.py` (`parse_document`) extracts plain text from `.pdf` / `.docx`.
3. `indexing/chunker.py` (`chunk_text`) splits text into overlapping chunks. Sizes
   are `cl100k_base` **tokens** (default `chunk_size=400`, `overlap=50` from
   `indexing/config.py`), not words.
4. `indexing/builder.py` (`build_documents` / `build_tree` / `build_tree_from_chunks`)
   embeds every chunk (`_stub_embedding`, 16-dim, L2-normalised) and delegates
   clustering + summarisation to `indexing/summarizer.py` (`RecursiveSummarizer`,
   Section 13). Pass `llm_client=` to use a real model; the default is the
   offline `_stub_llm_client`, so CI runs without network access.
5. `indexing/store.py` (`persist_tree_nodes`) upserts every `TreeNode` (with
   `embedding`, `parent_id`, `child_ids`, `metadata["document_id"]`) into
   `ChromaVectorStore` (`.graft/chroma`, collection embedder-qualified per
   section 5, e.g. `graft_tree_nodes_all-minilm-l6-v2_384`) via
   `insert`/`upsert`.

### Chunk schema accepted by the builder

`build_tree_from_chunks` normalises both chunk shapes in circulation, so leaf
provenance survives either producer:

    # current: indexing.chunker.chunk_text (flat keys)
    {"chunk_id", "document_id", "text", "chunk_index", "token_count"}
    # legacy: nested metadata
    {"chunk_id", "document_id", "text", "metadata": {"source", "page", ...}}

`source`, `page`, `chunk_index`, `token_count` and `word_count` are copied into
leaf `TreeNode.metadata`; `metadata["document_id"]` is always set (the chunk's
own `document_id` wins over the caller's fallback). This is what backs the
`metadata.source` / `metadata.page` fields consumed by Retrieval → Router
(Section 5).

### Tree invariants

- Every leaf has a non-empty `embedding`; summary nodes inherit the mean child
  embedding, falling back to `_stub_embedding` when child vectors are unusable,
  because `persist_tree_nodes` raises on a missing embedding.
- `child.parent_id == parent.node_id`, `parent.child_ids` contains the child,
  and `parent.level == child.level + 1`.
- Node IDs are namespaced per document (`{document_id}_summary_L{level}_{n}`),
  so multi-document indexing cannot collide and break parent links.
- Every document yields a root summary with `parent_id is None`, including
  single-chunk documents.


### Multipart Response

```json
{
  "status": "success",
  "document_ids": ["rfc793", "sample"],
  "total_chunks": 24,
  "tree_stats": {
    "total_nodes": 31,
    "levels": {
      "0_leaf": 24,
      "1_summary": 6,
      "2_root": 1
    }
  }
}
```

| Field | Type | Description |
|---|---|---|
| `status` | string | `"success"` on completion |
| `document_ids` | array[string] | Sanitized document IDs derived from filenames, one per uploaded file |
| `total_chunks` | integer | Number of leaf nodes (level 0) |
| `tree_stats.total_nodes` | integer | Total count of all tree nodes (leaves + summaries + root) |
| `tree_stats.levels` | object | Mapping `"{level}_{kind}" → count`; kind is `leaf` (level 0), `summary` (intermediate), `root` (max level) |

Every leaf node produced has a non-empty `embedding`; parent-child links satisfy `child.parent_id == parent.node_id`, `parent.child_ids` contains the child, and `parent.level == child.level + 1`. All nodes are persisted and queryable via `level` metadata filter.

### JSON Request (legacy, api)

```json
{
  "text": "Document text...",
  "document_id": "doc_001",
  "source": "example.pdf"
}
```

### JSON Response (legacy)

```json
{
  "document_id": "doc_001",
  "nodes_indexed": 7,
  "nodes": [{"node_id": "...", "level": 0, "text": "...", "parent_id": null, "metadata": {}}]
}
```

---

## 13. Recursive Summarization Engine (RAPTOR)

`indexing/summarizer.py` (`RecursiveSummarizer`) builds the hierarchical
document tree by iteratively clustering layer-`L` nodes and summarizing
each cluster with an LLM into a layer-`L+1` node. Prompts live in
`indexing/prompts.py`. The engine operates on `indexing.tree_node.TreeNode`
(Section 2) and is covered by `tests/test_summarizer.py` with mocked LLM
calls.

### Prompt template (`indexing/prompts.py`)

`SUMMARIZATION_PROMPT` is the single summarization contract. Render it with
`format_summarization_prompt(concatenated_texts)` or `str.format`:

```
You are a precise technical summarizer building a hierarchical index.
Summarize the following related text passages into a cohesive, standalone summary.
Retain critical factual details, names, entities, and numeric claims.

Passages:
{concatenated_texts}

Cohesive Summary:
```

`{concatenated_texts}` is `"\n\n".join(child.text for child in cluster)`.
Do not rename the placeholder without updating `summarizer.py` and this doc.

### Class interface (`indexing/summarizer.py`)

```python
from typing import List
from indexing.tree_node import TreeNode

class RecursiveSummarizer:
    def __init__(self, llm_client, max_summary_tokens: int = 400, min_cluster_size: int = 3):
        self.llm_client = llm_client
        self.max_summary_tokens = max_summary_tokens
        self.min_cluster_size = min_cluster_size

    def summarize_cluster(self, child_nodes: List[TreeNode], level: int) -> TreeNode:
        """Generates a summary node for a given cluster of child nodes."""

    def build_tree_layers(self, leaf_nodes: List[TreeNode]) -> List[TreeNode]:
        """Recursively clusters and summarizes nodes layer-by-layer until the root node is generated."""
```

The implementation additionally accepts optional `cluster_method`
(`"gmm"` default, `"kmeans"`, `"umap_gmm"`), `random_state` and
`node_id_prefix` keyword arguments; the three spec parameters above are
unchanged.

| Member | Type | Description |
|---|---|---|
| `llm_client` | callable/object | `(prompt: str) -> str`, or an object with `generate` / `complete` / `summarize` / `invoke` / `chat`. Dict results with `text` / `summary` / `content` are coerced; empty summaries raise `ValueError` (fail loudly) |
| `max_summary_tokens` | integer | Word-level truncation applied to every LLM summary; must be `>= 1` (default `400`) |
| `min_cluster_size` | integer | Target group size; cluster count is `len(layer) // min_cluster_size`; sequential fallback groups by this size; must be `>= 2` (default `3`) |
| `cluster_method` | string | `"gmm"` (default), `"kmeans"`, or `"umap_gmm"` |
| `random_state` | integer | Clustering seed for reproducible trees (default `42`) |
| `node_id_prefix` | string | Prefix for generated IDs (`{prefix}_L{level}_{n}`). Callers building one tree per document must pass a document-scoped prefix or IDs collide |
| `summarize_cluster(child_nodes, level)` | method | Validates non-empty cluster and `level >= 1`, calls the LLM, creates the summary node, sets `child.parent_id` and `summary.child_ids` bidirectionally |
| `build_tree_layers(leaf_nodes)` | method | Full recursion; `[]` for empty input, single node returned as-is; otherwise loops cluster → summarize until 1 node remains. `build_raptor_tree(leaf_nodes)` is an alias; module-level `build_raptor_tree(leaf_nodes, llm_client, ...)` is a one-shot wrapper |

### Clustering

- `gmm`: `sklearn.mixture.GaussianMixture(n_components=n_clusters, covariance_type="diag")`.
- `kmeans`: `sklearn.cluster.KMeans(n_clusters=n_clusters, n_init=10)`.
- `umap_gmm`: `umap.UMAP` reduction then GMM; falls back to plain GMM when
  `umap` is not installed or fitting fails.
- `n_clusters = max(1, len(layer) // min_cluster_size)`, capped at
  `len(layer) - 1` and at the number of **distinct** embedding rows. All
  clustering is seeded (`random_state`) for determinism.
- Fallback to sequential groups of `min_cluster_size` when embeddings are
  missing, mismatched in dimension, non-finite, contain fewer than two
  distinct vectors, or any backend raises. `scikit-learn` / `numpy` are
  imported lazily, so the API still starts without them.
- Single-leaf input is returned unchanged by `build_tree_layers`; callers that
  need a root for a one-chunk document (the indexing pipeline does, see
  Section 12) add it themselves.

### Linking and termination

- Linking: `child.parent_id = summary.node_id`;
  `summary.child_ids = [child.node_id for child in cluster]`.
- Summary metadata: `{"document_id": <first child document_id or "doc_001">,
  "child_count": len(cluster)}` plus shared `source` when unanimous.
- Termination: stop when the current layer has 1 node (root, `parent_id is
  None`), or when a layer has `<= 3` nodes (collapsed into one final root
  summary). A `> 20` layer guard prevents runaway recursion.
- Output: flat list of all nodes across layers `0` through root; input
  leaves are mutated as links are created.

Example (mocked LLM, as in `tests/test_summarizer.py`):

```python
from indexing.summarizer import RecursiveSummarizer
from indexing.tree_node import TreeNode

llm = lambda prompt: "Mock summary."
summarizer = RecursiveSummarizer(llm, max_summary_tokens=400, min_cluster_size=3)
leaves = [TreeNode(node_id=f"leaf_{i:04d}", text=f"Passage {i}.", level=0,
                   embedding=[float(i), 0.0],
                   metadata={"document_id": "doc_001"}) for i in range(6)]
nodes = summarizer.build_tree_layers(leaves)
by_id = {n.node_id: n for n in nodes}
root = next(n for n in nodes if n.parent_id is None and n.level > 0)
assert root.child_ids
for child_id in root.child_ids:
    assert by_id[child_id].parent_id == root.node_id  # bidirectional
```

---

## 14. Contract Version

Initial contract version: **v1**

Changes to required fields, field types, or field meanings should be
communicated to dependent components before merging.

Optional fields may be added without breaking existing consumers.

Contract update **v1.1** — added Section 12 indexing upload endpoint.

Contract update **v1.2** — added Section 13 recursive summarization engine
and prompt template.

Contract update **v1.3** — Section 12 processing flow now describes the
token-based chunker and the rewired builder (delegates to
`RecursiveSummarizer`, document-scoped node IDs, root summary for
single-chunk documents, leaf provenance preserved). Tunables are now
named constants in `indexing/config.py`. No wire-format change: the
`POST /index` request and response shapes are unchanged.

Contract update **v1.4** — query-side integration. Additions and behaviour
changes, none of which alter a wire format:

- Section 4 documents the complexity → depth mapping
  (`simple`→0, `moderate`→1, `complex`→2), which previously was inverted for
  `simple` queries: `default_retrieval_depth` overrode the map, so simple
  queries retrieved at depth 1.
- Section 5 documents depth *cumulative* semantics (deepest level considered,
  falling back to the leaves), Chroma `$and` filter composition, and
  embedder-qualified collection naming.
- `GET /health` gains two optional fields, `embedding_provider` and
  `embedding_dim`.
- `POST /index` multipart and JSON now share one tree builder, one chunker
  (`indexing.chunker`, token-based) and one `TreeNode` type
  (`indexing.tree_node`, re-exported as `tree_store.TreeNode`). The
  word-based chunker stub and the second `TreeNode` dataclass are removed.
- Module activation is now hint-driven for both `moderate` and `complex`
  queries (the tier is the gate, the query is the selector), so a `moderate`
  query mentioning a conflict reaches contradiction detection.

Contract update **v1.5** — flattened the package layout. Every module is a
top-level package (`router`, `retrieval`, `modules/*`, `generation`,
`baseline`, `indexing`, `tree_store`, `db`, `vector_store`, `api`, `backend`,
`benchmark`) with root modules `config.py`, `embeddings.py` and `version.py`.
The interim `graft.*` namespace is removed; `graft.X` paths become `X`.
No behaviour, wire format or field changed — the move is import-path only, and
each destination module already held the real implementation (the `graft.*`
counterparts were forwarders). The PyPI distribution keeps the name `graft`.

---

## 15. Overall Data Flow

    Document
       |
       v
    Indexing Pipeline
       |-----------------> Tree Store
       |
       +-----------------> Vector Store
                               |
                               v
                           Retrieval
                               |
                               v
                             Router
                               |
                               v
                      Specialist Modules
                               |
                               v
                           Generation
                               |
                               v
                              API
                               |
                               v
                           Frontend


                           
## Embedding & Clustering (`indexing/embed_cluster.py`)

Takes leaf `TreeNode` chunks, embeds them, and groups semantically similar
chunks for recursive summarization (Issue #15).

### Data shapes

```python
@dataclass
class ClusterGroup:
    cluster_id: int        # contiguous, starting at 0
    node_ids: List[str]    # references to TreeNode.node_id

@dataclass
class ClusterResult:
    clusters: List[ClusterGroup]
    embeddings: Dict[str, List[float]]  # node_id -> embedding vector
```

| Field | Type | Notes |
|---|---|---|
| `ClusterGroup.cluster_id` | `int` | Unique within one `ClusterResult`; ids are contiguous from 0. |
| `ClusterGroup.node_ids` | `List[str]` | Non-empty. Every input node appears in exactly one cluster. |
| `ClusterResult.clusters` | `List[ClusterGroup]` | Empty list only when the input is empty. |
| `ClusterResult.embeddings` | `Dict[str, List[float]]` | One entry per input node. 384 floats for `all-MiniLM-L6-v2` (L2-normalized). |

### `EmbedClusterManager`

```python
EmbedClusterManager(
    model_name: str = "all-MiniLM-L6-v2",
    min_cluster_size: int = 4,
    algorithm: str = "gmm",      # "gmm" | "kmeans"
    random_state: int = 42,
    batch_size: int = 32,
    encoder: Optional[Any] = None,  # inject a custom encoder (tests)
)
```

| Method | Input | Output | Behavior |
|---|---|---|---|
| `generate_embeddings(nodes)` | `List[TreeNode]` (needs `node_id`, `text`) | same nodes | Sets `node.embedding` (list of floats) on every node. |
| `cluster_nodes(nodes)` | `List[TreeNode]` | `List[ClusterGroup]` | Embeds any node missing an embedding, then clusters. |
| `run(nodes)` | `List[TreeNode]` | `ClusterResult` | Embedding + clustering in one pass. |

### Cluster count and edge cases

- `K = max(1, N // min_cluster_size)`, where `N` is the number of chunks.
- `N < min_cluster_size` (so `K == 1`) returns a single cluster holding all nodes.
- Empty input returns an empty result; no error is raised.
- GMM uses diagonal covariance; if it fails to fit, it falls back to K-Means. If clustering fails entirely, all nodes go into a single cluster.
- GMM can leave a component empty; empty clusters are dropped, so the number of returned clusters may be less than `K`.
- A node with a missing/empty `node_id` or `text=None` raises `ValueError`.
