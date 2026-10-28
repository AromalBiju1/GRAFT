# The GRAFT Library

A guided tour of the GRAFT codebase for someone who has never seen it before.

GRAFT (Gated Retrieval Activation Framework for Trees) is an adaptive,
complexity-aware Retrieval-Augmented Generation system. Instead of retrieving
the same amount of context for every question, it:

1. Builds a **hierarchical tree** over each document (leaf chunks, summaries,
   root summary).
2. **Routes** each question by complexity and chooses a retrieval depth.
3. **Activates only the specialist modules** that question needs.
4. Synthesises an answer with evidence, and optionally logs analytics.

This folder is the map. The rest of the repository is the territory.

> **Status snapshot (28 October 2026).** These pages describe the current
> working tree, including the two-phase upload/chat flow and the in-progress
> LLM provider expansion. If you are on an older commit, compare the relevant
> command or endpoint with the code before relying on it.

---

## Read this first

| If you want to... | Read |
|---|---|
| Run the project today | [01 - Getting started](01-getting-started.md) |
| Understand how the pieces fit | [02 - Architecture and data flow](02-architecture-and-data-flow.md) |
| Know what one file does | [03 - Backend reference](03-backend-reference.md) |
| Call the HTTP API | [04 - API reference](04-api-reference.md) |
| Work on the React app | [05 - Frontend guide](05-frontend-guide.md) |
| Connect the frontend to the API | [06 - Frontend/backend integration](06-frontend-backend-integration.md) |
| Run tests and understand CI | [07 - Testing and CI](07-testing-and-ci.md) |
| Fix a startup or runtime problem | [08 - Troubleshooting](08-troubleshooting.md) |
| Decode the vocabulary | [09 - Glossary](09-glossary.md) |

If you only have five minutes, read this page and run the four commands in
[01 - Quick start](01-getting-started.md#quick-start).

---

## GRAFT in one diagram

```text
WRITE PATH (indexing)

  PDF / DOCX / raw text
          |
          v
  indexing/parser.py  ->  indexing/chunker.py  ->  embeddings.py
          |                    (tokens)                 |
          v                                           v
  indexing/summarizer.py  <---- cluster + summarize ----+
          |                    (GMM / k-means / fallback)
          v
  indexing/store.py  ->  ChromaDB collection  (.graft/chroma)
          |
          +--> tree nodes: leaves (level 0), summaries (1..N), root


READ PATH (a query)

  question
     |
     v
  router/  ---> complexity + retrieval depth + activated modules
     |
     v
  retrieval/  ---> nearest nodes at levels 0..depth
     |
     v
  modules/  ---> only the activated specialists
     |            fact_lookup, multi_hop, numeric_reasoning,
     |            contradiction_detection
     v
  generation/  ---> deterministic synthesis, or an LLM when configured
     |
     v
  API response: answer + evidence + routing + latency
```

The two paths meet at a ChromaDB collection whose name is qualified by the
embedding model (for example `graft_tree_nodes_all-minilm-l6-v2_384`). If you
index with one embedder and query with another, you get a different collection,
not a dimension error. `GET /health` always tells you which one is live.

---

## Current status at a glance

This table describes the code in the `dev` branch, not the long-term plan.

| Area | State | Start here |
|---|---|---|
| Indexing (PDF/DOCX/text -> tree -> Chroma) | Working | `indexing/` |
| Query pipeline (`POST /query`) | Working end to end | `api/main.py`, `router/`, `retrieval/` |
| Router (heuristic complexity + gating) | Working, deterministic | `router/__init__.py` |
| Specialist modules | Working heuristics; each one has an LLM/NLI upgrade path | `modules/` |
| Generation | Deterministic by default; optional Gemini, OpenAI, Groq, Ollama, or llama.cpp | `generation/` |
| Analytics (`/analytics/*`) | Working, backed by SQLite | `api/routes/analytics.py` |
| Flat baseline | Library is complete; **no HTTP route** | `baseline/__init__.py` |
| Benchmark runner | Working CLI, needs an indexed corpus | `benchmark/runner.py` |
| Upload/file API (`/api/files`) | Working, in-memory record store | `api/uploads.py` |
| Chat streaming (`/api/chat`) | Working: JSON in, SSE out, filtered by `file_ids` | `api/uploads.py`, `api/query_runner.py` |
| React chat UI | Working two-phase flow: upload/poll, then JSON chat | `frontend/src/App.jsx`, `frontend/src/api.js` |
| Legacy frontend components (`QueryForm`, etc.) | Work in isolation; not mounted by `main.jsx` | `frontend/src/` |
| Legacy backend (`backend/app`) | Mounts only `/health` and `/index` | `backend/app/main.py` |

The honest summary: the backend pipeline is real and tested, and the
upload/chat flow is wired end to end. Upload records are still in memory, so a
restart loses file IDs even though the indexed vectors remain in Chroma.
Chapter 06 walks through the full request sequence.

---

## Repository map (one line each)

| Path | What lives there |
|---|---|
| `api/` | FastAPI app, request schemas, upload/chat endpoints, analytics routes |
| `backend/` | Small legacy FastAPI app kept for the old `/index` upload flow |
| `baseline/` | Flat "retrieve everything, run every module" comparison path |
| `benchmark/` | Metrics, datasets, prompts, and the GRAFT-vs-baseline CLI |
| `db/` | SQLite query logger used by `/query` and the analytics routes |
| `docs/` | Design docs, interface contracts, ADRs, setup notes |
| `frontend/` | React 19 + Vite chat UI |
| `generation/` | Deterministic answer synthesis plus optional LLM clients |
| `indexing/` | Parse, chunk, embed, cluster, summarise, persist the tree |
| `modules/` | The four specialist reasoning modules and their base contract |
| `retrieval/` | Depth-aware vector search over the tree |
| `router/` | Complexity scoring, depth mapping, module activation |
| `scripts/` | Corpus indexing and benchmark chunk-ID utilities |
| `tests/` | 500+ backend tests; offline by default, real-model integration tests when available |
| `tree_store/` | Compatibility re-export of `TreeNode` |
| `vector_store/` | Compatibility re-export of `ChromaVectorStore` |

Root files:

| File | Purpose |
|---|---|
| `config.py` | Typed settings loaded from `.env` / environment |
| `embeddings.py` | Shared embedder; real MiniLM or deterministic 16-dim stub |
| `logging_config.py` | One-time logging setup, `GRAFT_LOG_LEVEL` |
| `version.py` | `__version__` reported by `/health` |
| `requirements.txt` | Runtime dependencies installed by CI |
| `pyproject.toml` | Packaging, optional extras, pytest/ruff/mypy/coverage config |
| `.env.example` | Every setting with its default |
| `README.MD` | Project pitch and high-level status |
| `CONTRIBUTING.md` | Branching, ownership, review, and PR rules |

---

## Conventions used in this Library

- Commands assume you are at the repository root unless a `cd` is shown.
- `127.0.0.1:8000` is the default API address; `localhost:5173` is the default
  Vite address.
- "Collection" means a ChromaDB collection, not a Python collection.
- "Depth 0/1/2" means leaves / summaries / root, as defined by the router.
- File references are repository-relative, for example `api/main.py`.
- Known gaps are called out explicitly rather than hidden. If a command does
  not work in the current `dev` branch, this Library says so.

---

## Where the existing docs fit

`docs/` already contains deeper design material. The Library does not replace
it; it tells you when to read it.

| Existing document | Read it when... |
|---|---|
| `docs/architecture.md` | You want the design rationale and a stage-by-stage walkthrough |
| `docs/interfaces.md` | You are changing a function signature or data shape between modules |
| `docs/setup.md` | You want the short setup checklist (see the env-name correction in chapter 01) |
| `docs/scope.md` | You need the locked MVP scope and cut-line |
| `docs/evaluation.md` | You are working on benchmark metrics |
| `docs/adr/` | You want to know why trees, fixed thresholds, or one package per stage |
| `docs/references.md` | You want the RAPTOR / Adaptive-RAG / NLI reading list |

---

Next: [01 - Getting started](01-getting-started.md)
