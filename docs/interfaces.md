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

---

## 5. Retrieval → Router

The retrieval component returns relevant document chunks to the router.

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

## 12. Contract Version

Initial contract version: **v1**

Changes to required fields, field types, or field meanings should be
communicated to dependent components before merging.

Optional fields may be added without breaking existing consumers.

---

## 13. Overall Data Flow

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