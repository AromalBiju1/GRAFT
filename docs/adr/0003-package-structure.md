# ADR 0003: One top-level package per pipeline stage

- **Status:** Accepted

## Context

GRAFT is built by four people working on separate pieces that must connect. The
project rules ([CONTRIBUTING.md](../../CONTRIBUTING.md)) ask the team to agree on
input/output formats first and to give each area a clear owner.

## Decision

Each stage of the pipeline is its own package at the repository root:

- `indexing/`: tree construction
- `tree_store/` and `vector_store/`: tree contract and vector storage
- `router/`: complexity classification and activation gating
- `retrieval/`: depth-aware search
- `modules/`: the four specialist modules, each in its own subpackage
- `generation/`: answer synthesis
- `baseline/`: flat RAG used for comparison
- `benchmark/`: metrics and datasets
- `api/`: the FastAPI app that wires everything together

Shared settings live in `config.py` and the shared embedder in `embeddings.py`. The
cross-package input/output shapes are documented in
[interfaces.md](../interfaces.md).

## Consequences

- The folder layout mirrors the pipeline, so a newcomer can find each stage quickly.
- Owners can work in their own package with fewer merge conflicts.
- Each stage can be tested on its own and swapped without touching the others, for
  example replacing the heuristic router with a trained one.
- The baseline and benchmark sit beside GRAFT, so comparisons use the same code paths.
- Contracts must be kept in sync with [interfaces.md](../interfaces.md) when a shape
  changes.
- `backend/` is a small legacy app. `api.main:app` is the one to use.