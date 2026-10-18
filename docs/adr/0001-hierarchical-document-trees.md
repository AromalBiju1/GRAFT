# ADR 0001: Use hierarchical document trees

- **Status:** Accepted

## Context

Conventional RAG treats every document as a flat list of chunks and retrieves the same
way for every question. Easy questions pay for work they don't need, and hard questions
(multi-hop, cross-document) get no higher-level view of the material.

GRAFT's goal (see [scope.md](../scope.md)) is to match retrieval effort to question
difficulty, and to beat a flat "retrieve everything" baseline on both accuracy for hard
questions and cost for easy ones.

## Decision

Each document is indexed as a RAPTOR-style tree:

- **Level 0 (leaves):** the raw text chunks.
- **Level 1 and up (summaries):** similar chunks are clustered and each cluster is
  summarized. This repeats recursively.
- **Top (root):** a single summary of the whole document.

Each node has parent and child links, and its level is stored as metadata next to its
embedding. The router's retrieval depth (0, 1 or 2) maps directly to these levels.

## Consequences

- Retrieval depth can be chosen per question by filtering on `level`, which is what
  makes adaptive routing possible.
- A root summary exists even for single-chunk documents. If a tree lacks the requested
  level, retrieval falls back to the leaves.
- Indexing costs more than a flat index because it needs clustering and summarization.
- The quality of upper levels depends on the summarizer.
- In the MVP, retrieval only filters by level. Full tree traversal is out of scope
  (see the cut line in [scope.md](../scope.md)).