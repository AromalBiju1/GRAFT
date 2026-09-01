# GRAFT — Scope & MVP Definition

## Goal

Build a complexity-aware RAG system that (a) indexes documents into a
RAPTOR-style tree and (b) routes each query to only the reasoning depth
and specialist modules it needs, beating a flat "retrieve everything"
baseline on both accuracy for hard queries and cost for easy ones.

## MVP — Must Ship

These are required for the 2026–27 final-year demo + report. Nothing in
"MVP" can be cut without guide approval.

### 1. Indexing Pipeline + Tree Store
- Chunking (fixed-size, token-aware fallback)
- Embedding (SentenceTransformers or Gemini/OpenAI; pluggable)
- Clustering (stub sequential groups initially, then embedding-based GMM/UMAP)
- Recursive summarisation (stub truncated concat → LLM summariser)
- Persisted tree: `TreeNode` with `node_id, document_id, level, text, parent_id, metadata, children`
- Vector store write: `vector_store.ChromaVectorStore` at `.graft/chroma`

Acceptance: `graft.indexing.pipeline.index_document` indexes the sample docs
and `retrieve` returns level-filtered hits.

### 2. Router / Activation Gating
- Complexity classifier: `simple | moderate | complex` with confidence
- Thresholds from `graft.config.Settings` (no magic numbers inline)
- Mapping: simple→depth 0 (leaf), moderate→1, complex→2
- Module activation per query: subset of
  `fact_lookup, multi_hop, numeric_reasoning, contradiction_detection`

Acceptance: `graft.router.route` is deterministic, tested, and its `route`
output drives both `retrieval` and the `activated_modules` list sent to generation.

### 3. Four Specialist Modules
All follow `docs/interfaces.md` §7 common I/O.

| Module | Owner stub | Minimal behaviour |
|---|---|---|
| Fact lookup | — | Return top-scoring passage |
| Multi-hop | — | Fuse top-3 passages |
| Numeric/table | — | Extract numbers, support sum/average/difference |
| Contradiction detection | — | Flag negation-mismatch overlaps; swap to NLI later |

Acceptance: each module has a direct unit test and a module-result shape
that `graft.generation.synthesize` consumes.

### 4. Generation
- Stub concatenation → LLM synthesis (Gemini/OpenAI/local)
- Output: `{request_id, answer, evidence}` per interfaces §9.

### 5. Baseline (Flat RAG)
- `graft.baseline.run_baseline` retrieves with no gating and fires all modules.
- Used for every benchmark run to produce an honest comparison.

### 6. Benchmark Harness
- Seed question set in `graft/benchmark/datasets.py`
- Metrics: accuracy (human or LLM-judged), latency_ms, modules_fired,
  retrieval precision@k
- Logging to `db/graft_logs.db` via `db.logger`

Acceptance: can run `graft` vs `baseline` on the seed set and emit a table.

### 7. API + Frontend
- FastAPI at `graft/api/main.py`: `GET /health`, `POST /query`, `POST /index`
- React frontend at `frontend/` that hits the API (Vite proxy)

### 8. Docs + Contracts
- `docs/interfaces.md` is the source of truth for cross-module shapes.
- `docs/setup.md` for local setup, `docs/references.md` for reading list.

## Cut Line — Explicitly Out of Scope for MVP

These are **not** in the MVP. They become stretch goals only after the MVP
is integrated and benchmarked:

- Cross-query pipelining / caching
- Learned / trained gating thresholds (currently rule-based + env-tunable)
- Persistent write-back of tree edits
- Horizontal scaling, Kubernetes deployment, production auth
- Fine-grained hierarchical retrieval beyond level filtering (e.g. tree traversal)

Any PR that implements a cut-line item must be flagged as stretch scope in
the PR description and approved before merging.

## Milestones

1. Week 2 — Indexing pipeline + vector store smoke test (sample docs indexed)
2. Week 4 — Router + retrieval wiring (gated depth works end-to-end)
3. Week 6 — 4 specialist modules stubbed + generation stub
4. Week 8 — Baseline + benchmark harness (first honest numbers)
5. Week 10 — API + frontend integration
6. Week 12 — Report + demo polish

## Definition of Done

A feature is done when: code runs locally without errors, has at least one
asserting test (not just "doesn't crash"), updates `docs/interfaces.md` if
it changes a contract, and does not break `pytest` or `GET /health`.
