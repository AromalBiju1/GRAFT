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
graft/
├── backend/            # FastAPI application
├── frontend/           # React + Vite application
├── vector_store/       # ChromaDB integration
├── tests/              # backend and vector-store tests
├── indexing/           # tree construction: chunking, clustering, summarization
├── router/              # query complexity classification + activation gating
├── modules/
│   ├── fact_lookup/
│   ├── multi_hop/
│   ├── numeric_reasoning/
│   └── contradiction_detection/
├── generation/          # final answer synthesis
├── baseline/            # flat "retrieve everything" RAG, for benchmarking
├── benchmark/           # evaluation harness, metrics, test question sets
└── docs/                # architecture notes, SRS, references
```

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

Start the backend from the repository root:

```bash
uvicorn backend.app.main:app --reload
```

Verify it by opening `http://127.0.0.1:8000/health`. The response should be
`{"status":"ok"}`.

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
from indexing.vector_store import ChromaVectorStore

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

## Team

| Module | Owner |
|---|---|
| Indexing / tree construction | |
| Router / activation gating | |
| Specialist modules | Abhinav Suresh · Anugrah M Nair · Aromal Biju · S Vishnu Narayan |
| Benchmarking harness | |

Guide: _________________

## Status

🚧 In development — MVP scope: tree + router + 4 specialist modules + benchmark
against flat RAG baseline. See `docs/scope.md` for full MVP definition and cut-line.

## References

See `docs/references.md` for the full reading list (RAPTOR, Adaptive-RAG, Self-RAG,
CRAG, MoE/gating literature, contradiction/hallucination detection papers).

## License

Academic project — license to be finalized by the team / department guidelines.
