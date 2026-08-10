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

    {
      "node_id": "node_001",
      "document_id": "doc_001",
      "level": 0,
      "text": "Chunk or summary text...",
      "parent_id": null,
      "metadata": {
        "source": "example.pdf",
        "page": 1
      }
    }

| Field | Type | Description |
|---|---|---|
| `node_id` | string | Unique tree node identifier |
| `document_id` | string | Source document identifier |
| `level` | integer | Tree depth/level |
| `text` | string | Text represented by the node |
| `parent_id` | string/null | Parent node identifier |
| `metadata` | object | Source and processing metadata |

---

## 3. Indexing Pipeline → Vector Store

The indexing pipeline creates embeddings for searchable document chunks.

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