# ADR 0004: One collection per embedder, with documents separated by metadata

- **Status:** Accepted

## Context

Tree nodes from every indexed document need to be stored with their embeddings and
searched by level. Vectors from different embedding models can have different
dimensions, and mixing them in one collection causes dimension-mismatch errors.

## Decision

All tree nodes are stored in a single Chroma collection at `.graft/chroma`. The
collection name starts from the base name `graft_tree_nodes` and is qualified by the
active embedder and its dimension, for example `graft_tree_nodes_all-minilm-l6-v2_384`.

Documents and tree levels are told apart by metadata on each record (`document_id`,
`level`, `node_type`), and searches pass a metadata filter to Chroma. A single
`embeddings.py` is shared by indexing and querying, so stored vectors and query
vectors always use the same dimension.

## Consequences

- Switching between the real embedder and the offline stub selects a different
  collection instead of failing with a dimension mismatch.
- Changing the embedding model means re-indexing into a new collection.
- Different file types (PDF, DOCX) are parsed into the same tree shape, so they share
  the same collection.
- Level and document filters are done by metadata, which keeps the store simple.
- Inserting an existing `chunk_id` replaces that record (upsert behaviour).