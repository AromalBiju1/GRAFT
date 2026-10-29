# Production-Ready Research Studio & Output Layer Design

## Overview

This specification defines the transformation of GRAFT's user interface and query output layer from an informal messaging chat prototype into a publication-ready, two-column interactive Research Studio. It enriches the backend output layer with full specialist reasoning module results and telemetry, restructures the frontend into structured Research Report Cards, adds an interactive right-hand Inspector pane for ChromaDB RAPTOR tree hierarchy and specialist module breakdowns, and equips researchers with citation linking, BibTeX/APA export, and explicit retrieval scope controls.

---

## 1. Architecture & Backend Output Layer

### 1.1 Enriched Query Execution Engine (`api/query_runner.py`)
`execute_query()` currently discards `module_results` after passing them to `synthesize()`. It is updated to serialize and retain these results:

```python
module_results_dicts = [
    m.to_dict() if hasattr(m, "to_dict") else dict(m)
    for m in module_results
]
```

The returned payload structure:
```python
{
    "request_id": req_id,
    "answer": str(final.get("answer", "")),
    "evidence": to_evidence_dicts(evidence_raw),
    "routing": decision.to_dict(),
    "module_results": module_results_dicts,
    "latency_ms": round(latency_ms, 2),
}
```

### 1.2 Specialist Module Data Contracts
Each specialist module's output is structured for direct UI inspection:
- `fact_lookup`: `{"module": "fact_lookup", "result": "...", "confidence": float, "evidence": [...]}`
- `multi_hop`: `{"module": "multi_hop", "result": {"hops": [...], "answer": "..."}, "confidence": float, "evidence": [...]}`
- `numeric_reasoning`: `{"module": "numeric_reasoning", "result": {"calculation": "...", "answer": "..."}, "confidence": float, "evidence": [...]}`
- `contradiction_detection`: `{"module": "contradiction_detection", "result": {"conflicts": [...], "summary": "..."}, "confidence": float, "evidence": [...]}`

### 1.3 Streaming SSE Protocol (`POST /api/chat` in `api/uploads.py`)
The `event: meta` payload is enriched to include `module_results` and `latency_ms`:
```json
{
  "routing": {
    "complexity": "moderate",
    "confidence": 0.88,
    "retrieval_depth": 1,
    "activated_modules": ["fact_lookup", "multi_hop"]
  },
  "evidence": [ ... ],
  "module_results": [ ... ],
  "latency_ms": 234.1
}
```

### 1.4 API Schema Extension (`api/schemas.py`)
`QueryResponse` receives an optional `module_results` field for backward compatibility:
```python
class QueryResponse(BaseModel):
    request_id: str
    answer: str
    evidence: list[EvidenceItem] = Field(default_factory=list)
    routing: dict[str, Any] | None = None
    module_results: list[dict[str, Any]] = Field(default_factory=list)
    latency_ms: float | None = None
```

---

## 2. Frontend Research Studio Layout

### 2.1 Two-Column Studio Structure
- **Left Navigation Sidebar (`Sidebar.jsx`)**:
  - Chat & research sessions with rename/delete and export actions.
  - Active document index with ChromaDB status and chunk indicators.
  - Global corpus indicator (collection name and vector dimension).
- **Main Research Workspace (`ResearchFeed.jsx`)**:
  - Top Research Bar: Scope Switch (`Global Knowledge Base` vs `Attached Documents Only`) and active corpus stats.
  - Feed of **Research Report Cards** (`ResearchCard.jsx`):
    - **Header**: Query title, timestamp, complexity pill (`Simple` / `Moderate` / `Complex`), confidence meter, retrieval depth (`Level 0 Leaf`, `Level 1 Summary`, `Level 2 Root`), and quick actions (`Copy Markdown`, `Export Citations`, `Inspect`).
    - **Executive Summary / Answer**: High-contrast, clean typography, tables, math formatting, and clickable inline citations (`[1]`, `[2]`).
    - **Footer**: Latency badge, tokens/chunks evaluated, activated specialist modules.
- **Right Collapsible Research Inspector (`ResearchInspector.jsx`)**:
  - Synchronized with the focused Research Report Card.
  - Tabs:
    1. **Evidence & RAPTOR Tree**:
       - Tree cards indicating node level (`0_leaf`, `1_summary`, `2_root`).
       - Cosine similarity score bar.
       - Source provenance (document filename, page number).
       - Passage excerpt with modal/drawer to expand full chunk text.
    2. **Specialist Reasoning**:
       - Interactive module cards showing structured deductions for each active module.
       - Side-by-side view for contradictions; step-by-step arithmetic for numeric reasoning; graph/chain for multi-hop.
    3. **Citations & Audit**:
       - Formatted reference list in BibTeX and APA format.
       - Raw engine execution JSON audit trace.

---

## 3. Citation & Scope Management

- **Inline Citations**: Markdown renderer transforms `[1]`, `[2]` patterns into interactive `<CitationBadge index={n} />`. Clicking a badge highlights the corresponding evidence node in the Inspector pane.
- **Scope Selector**: A segmented control above the input bar (`All Knowledge Base` | `Attached Documents`). When set to `All Knowledge Base`, chat queries the entire pre-indexed ChromaDB collection without filtering by document ID; when set to `Attached Documents`, queries are filtered strictly to the uploaded document IDs.
- **Exporting**: Researchers can export any research card as a publication-ready Markdown report with footnotes, or export BibTeX bibliography entries.

---

## 4. Verification & Testing

1. **Backend Integration**:
   - `pytest tests/test_chat_endpoint.py tests/test_query_endpoint.py -v`: Verify `module_results` and `latency_ms` pass through cleanly without schema breakage.
2. **Frontend Build & Responsiveness**:
   - `cd frontend && npm run build`: Verify clean Vite compilation.
   - Test two-column layout on desktop viewports and collapsible behavior on responsive viewports.
