# GRAFT — Gated Retrieval Activation Framework for Trees

An adaptive, complexity-aware Retrieval-Augmented Generation (RAG) system that grafts
specialist reasoning modules onto a RAPTOR-style document tree, activating only the
retrieval depth and reasoning modules each query actually needs.

Final year project — Department of Computer Science and Engineering, College of
Engineering, Chengannur (2026–27).

---

## The Problem

Conventional RAG systems retrieve and process every query identically — a simple
factual question ("what are your business hours") and a genuinely complex one
("compare these three contracts and flag conflicts") get the same fixed-depth
retrieval and the same generic reasoning pass. This wastes compute on easy queries
and under-serves hard, multi-hop, or cross-document questions. Flat RAG also has no
mechanism to detect when retrieved sources contradict each other — conflicts must
still be caught manually.

## What GRAFT Does

- **Builds a hierarchical document tree** (RAPTOR-style): raw chunks at the leaves,
  progressively abstract summaries above, a single root summary at the top.
- **Routes each query by complexity**: a gating module decides how deep into the tree
  to retrieve — shallow for simple queries, deep for complex ones.
- **Activates specialist modules selectively**: fact lookup, multi-hop reasoning,
  numeric/table reasoning, and contradiction detection only fire when a query needs
  them, instead of running every module on every query.
- **Detects contradictions**: compares claims across retrieved sources and flags
  conflicts with source passages — a capability flat RAG lacks entirely.
- **Benchmarks against a flat RAG baseline**: an "always retrieve everything" system
  is built alongside GRAFT specifically to produce honest, reproducible comparisons.

## Architecture

```
Document Corpus → Indexing Pipeline → Tree Store → Router
    → Retrieval → Specialist Modules → Generation → Answer
```

| Stage | Description |
|---|---|
| Indexing Pipeline | Chunk, embed, cluster, and recursively summarize documents into a tree |
| Tree Store | Persisted hierarchical index (leaf chunks → summaries → root) |
| Router | Classifies query complexity, decides retrieval depth + module activation |
| Retrieval | Walks the tree at the depth the router selects |
| Specialist Modules | Fact lookup · Multi-hop reasoning · Numeric/table reasoning · Contradiction detection |
| Generation | Synthesizes the final answer from retrieved context + module output |

## Repository Structure

```
GRAFT/
├── api/                 # FastAPI app: /health, /query, /index
├── router/              # query complexity classification + activation gating
├── retrieval/           # depth-aware retrieval over the tree
├── modules/             # fact_lookup · multi_hop · numeric_reasoning · contradiction_detection
├── generation/          # final answer synthesis
├── baseline/            # flat "retrieve everything" RAG, for benchmarking
├── benchmark/           # metric helpers and datasets
├── indexing/            # tree construction: chunking, clustering, summarization
├── tree_store/          # TreeNode contract (re-exports indexing.tree_node)
├── vector_store/        # ChromaDB integration
├── db/                  # SQLite query logger
├── backend/             # minimal legacy FastAPI app
├── embeddings.py        # sentence-transformers with a deterministic stub fallback
├── config.py            # typed settings
├── version.py           # __version__
├── frontend/            # React + Vite application
├── tests/               # backend and vector-store tests
└── docs/                # architecture notes, SRS, references
```

There is one package per pipeline stage, at the top level. Every module named
in `.github/copilot-instructions.md` (`indexing/`, `router/`, `modules/*`,
`generation/`, `baseline/`) is a real implementation, not a forwarder.

## Tech Stack

- **Backend:** Python, FastAPI
- **Frontend:** React.js
- **Document processing:** PyPDF / Unstructured / python-docx
- **Embeddings:** SentenceTransformers (or Gemini/OpenAI embeddings)
- **Vector store:** FAISS / ChromaDB / pgvector
- **LLM:** Gemini / OpenAI API / local open-source model
- **NLI / contradiction detection:** transformer-based entailment models

## Getting Started

Clone the repository and create the Python environment:

```bash
git clone https://github.com/AromalBiju1/GRAFT
cd GRAFT
python -m venv venv
```

Activate it on Windows PowerShell and install dependencies:

```powershell
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

On macOS or Linux, activate it with `source venv/bin/activate` and copy the
environment template with `cp .env.example .env`.

Start the API from the repository root:

```bash
uvicorn api.main:app --reload
```

Verify it by opening `http://127.0.0.1:8000/health`. The response should be
`{"status":"ok"}`, plus the live embedder and its dimension:

```json
{
  "status": "ok",
  "version": "0.1.0",
  "chroma_path": ".graft/chroma",
  "collection": "graft_tree_nodes_all-minilm-l6-v2_384",
  "embedding_provider": "sentence-transformers",
  "embedding_dim": 384
}
```

`backend.app.main:app` is a smaller, legacy app that only serves `/health` and
the `/index` upload route. Use `api.main:app` for anything that queries.

In another terminal, install and start the frontend:

```bash
cd frontend
npm install
npm run dev
```

Open the local URL printed by Vite. The page should display “GRAFT” and
“Frontend is running.”

### Vector store

`ChromaVectorStore` persists precomputed tree-node embeddings locally. Its default
database location is `.graft/chroma`; pass `persist_path` to use another location.

```python
from vector_store import ChromaVectorStore

store = ChromaVectorStore(persist_path=".graft/chroma")
store.insert(
    chunk_id="chunk_001",
    document_id="doc_001",
    text="Example leaf chunk",
    embedding=[0.1, 0.2, 0.3],
    metadata={"node_id": "node_001", "level": 0, "node_type": "leaf"},
)

results = store.query(
    embedding=[0.1, 0.2, 0.3],
    n_results=5,
    filters={"level": 0},
)
```

`filters` is passed to ChromaDB as its metadata `where` filter. Insertions use
upsert behavior: inserting an existing `chunk_id` replaces that record.

Query and index embeddings must have the same dimension. `embeddings`
resolves the dimension once and qualifies the collection name by embedder, so
switching between the real model and the stub selects a different collection
rather than raising a dimension-mismatch error. `GET /health` reports which
embedder is live.

## The query pipeline

Start the API from the repository root:

```bash
uvicorn api.main:app --reload
```

```bash
# index a document
curl -X POST http://127.0.0.1:8000/index \
  -H 'Content-Type: application/json' \
  -d '{"text": "...", "document_id": "doc_001", "source": "example.pdf"}'

# ask a question
curl -X POST http://127.0.0.1:8000/query \
  -H 'Content-Type: application/json' \
  -d '{"query": "Compare the two documents and explain every difference"}'
```

`/query` routes the query by complexity, retrieves at the depth that complexity
maps to, runs only the activated specialist modules, and synthesises an answer
with evidence:

| Complexity | Retrieval depth | Modules activated |
|---|---|---|
| `simple` | 0 (leaves) | `fact_lookup` |
| `moderate` | 1 (+ summaries) | `fact_lookup` plus hinted reasoning modules |
| `complex` | 2 (+ root) | `fact_lookup` plus hinted reasoning modules |

`config.py` holds every tunable (paths, thresholds, embedder choice), and
`embeddings.py` is the single embedder shared by indexing and querying, so
stored vectors and query vectors can never disagree on dimension.

## Team

- Abhinav Suresh
- Anugrah M Nair
- Aromal Biju
- S Vishnu Narayan

## Status

🚧 In development. See `docs/scope.md` for the full MVP definition and cut-line.

The pipeline runs end to end today: `uvicorn api.main:app`, then
`POST /index` to build a document tree and `POST /query` to route, retrieve,
run the activated modules, and return a grounded answer with evidence.

**Working**

| Area | State |
|---|---|
| Indexing pipeline | PDF/DOCX ingestion, token-based chunking, GMM clustering, recursive summarisation, Chroma persistence |
| Tree store | Strict tree, bidirectional parent/child links, document-scoped node IDs, root summary even for single-chunk docs |
| Router | `simple`/`moderate`/`complex` → retrieval depth 0/1/2, with hint-driven module activation |
| Retrieval | Depth-aware search that falls back to the leaves when a document's tree lacks the requested level |
| 4 specialist modules | Fact lookup · multi-hop · numeric/table · contradiction detection, all sharing the §7 output contract |
| Generation | Deterministic, grounded synthesis with deduped, ordered evidence |
| Baseline | Flat "retrieve everything" RAG, ungated, for comparison |
| Embeddings | `all-MiniLM-L6-v2` (384-dim) with a deterministic stub fallback for offline runs |
| Tests | 245 passing, covering router, all four modules, generation, embeddings, retrieval, the `/query` endpoint, and index→query end to end |

**Not done yet**

| Area | State |
|---|---|
| Generation | Concatenates module output — no LLM call, so answers are grounded but not well-worded |
| Contradiction detection | Keyword-overlap + negation heuristic; high false-positive rate, needs an NLI model |
| Benchmarking | Metric helpers only — no dataset and no runner CLI, so no numbers yet |
| CI | No GitHub Actions workflow; tests are run manually |
| Frontend | Scaffold only (`App.jsx` renders a heading) — not wired to `/query` |
| Analytics | Query logger writes to SQLite, but no endpoint reads it back |

The honest summary: the retrieval and gating machinery is solid and tested,
but answer quality depends on work that hasn't been done, and the headline
claim — that GRAFT beats flat RAG — is unmeasured until the benchmark harness
lands.

## References

See `docs/references.md` for the full reading list (RAPTOR, Adaptive-RAG, Self-RAG,
CRAG, MoE/gating literature, contradiction/hallucination detection papers).

## License

Academic project — license to be finalized by the team / department guidelines.
