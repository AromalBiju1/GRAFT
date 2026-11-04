# Production-Ready Research Studio & Output Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Overhaul the GRAFT frontend and output layer into an interactive two-column Research Studio featuring structured Research Report Cards, a synchronized Evidence and Reasoning Inspector, citation linking and BibTeX export, and full specialist reasoning module passthrough.

**Architecture:** The backend query runner (`api/query_runner.py`) and streaming SSE chat (`api/uploads.py`) pass serialized specialist module results (`fact_lookup`, `multi_hop`, `numeric_reasoning`, `contradiction_detection`) and telemetry directly to the client. The frontend is refactored into a Two-Column Research Studio: the left pane streams structured Research Report Cards with executive summaries and interactive `[n]` citation pills, while a persistent collapsible right Inspector displays RAPTOR tree hierarchy, specialist module breakdowns, and bibliographic citations.

**Tech Stack:** Python 3.10+, FastAPI, ChromaDB, React 19, Vite, CSS Grid/Flexbox, Lucide-style SVG icons.

## Global Constraints

- Preserve all existing API schemas and query behavior (full backward compatibility with `POST /query`).
- No external network access in tests; use deterministic test fixtures and local ChromaDB.
- All backend tests (`pytest tests/`) and frontend build (`npm run build`) must pass without errors or warnings.
- Keep all existing comments and docstrings intact.

---

### Task 1: Enrich Backend Output Layer with Specialist Module Results

**Files:**
- Modify: `api/query_runner.py:80-138`
- Modify: `api/uploads.py:125-145`
- Modify: `api/schemas.py:27-34`
- Test: `tests/test_chat_endpoint.py`

**Interfaces:**
- Produces: `execute_query(...) -> dict` containing `"module_results": list[dict]`
- Produces: `POST /api/chat` SSE `meta` event containing `"module_results": list[dict]` and `"latency_ms": float`

- [ ] **Step 1: Write failing test in `tests/test_chat_endpoint.py` asserting `module_results` in `meta` event**

```python
def test_chat_meta_includes_module_results():
    r = client.post("/api/chat", json={"prompt": "What is GRAFT?", "file_ids": []})
    assert r.status_code == 200
    events = parse_sse_events(r.text)
    meta_event = next(e[1] for e in events if e[0] == "meta")
    assert "module_results" in meta_event
    assert isinstance(meta_event["module_results"], list)
    assert "latency_ms" in meta_event
```

- [ ] **Step 2: Update `api/query_runner.py` to serialize and return `module_results`**

```python
    module_results_dicts = [
        m.to_dict() if hasattr(m, "to_dict") else dict(m)
        for m in module_results
    ]

    evidence_raw = final.get("evidence") or retrieved
    return {
        "request_id": req_id,
        "answer": str(final.get("answer", "")),
        "evidence": to_evidence_dicts(evidence_raw),
        "routing": decision.to_dict(),
        "module_results": module_results_dicts,
        "latency_ms": round(latency_ms, 2),
    }
```

- [ ] **Step 3: Update `api/uploads.py` to forward `module_results` and `latency_ms` in `event: meta`**

```python
            yield _sse("meta", {
                "routing": result.get("routing"),
                "evidence": evidence,
                "module_results": result.get("module_results") or [],
                "latency_ms": result.get("latency_ms"),
            })
```

- [ ] **Step 4: Update `api/schemas.py` `QueryResponse`**

```python
class QueryResponse(BaseModel):
    request_id: str
    answer: str
    evidence: list[EvidenceItem] = Field(default_factory=list)
    routing: dict[str, Any] | None = None
    module_results: list[dict[str, Any]] = Field(default_factory=list)
    latency_ms: float | None = None
```

- [ ] **Step 5: Run tests to verify**

Run: `pytest tests/test_chat_endpoint.py -k test_chat_meta_includes_module_results -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add api/query_runner.py api/uploads.py api/schemas.py tests/test_chat_endpoint.py
git commit -m "feat(api): include module_results and latency in query and chat responses"
```

---

### Task 2: Build Citation and Export Helpers (`frontend/src/utils/research.js`)

**Files:**
- Create: `frontend/src/utils/research.js`

**Interfaces:**
- Produces: `formatBibTeX(evidence: list) -> string`
- Produces: `formatAPA(evidence: list) -> string`
- Produces: `exportMarkdownReport(query: string, message: object) -> string`

- [ ] **Step 1: Create `frontend/src/utils/research.js`**

Implement helper functions to:
1. Clean document identifiers for BibTeX keys.
2. Render formatted BibTeX entries (`@misc{doc_id, title={...}, ...}`).
3. Render formatted APA citations.
4. Render complete exportable Markdown reports with title, executive summary, metadata badges, and footnote references.

- [ ] **Step 2: Commit**

```bash
git add frontend/src/utils/research.js
git commit -m "feat(frontend): add citation and report export utilities"
```

---

### Task 3: Build Research Report Card Component (`frontend/src/components/ResearchCard.jsx`)

**Files:**
- Create: `frontend/src/components/ResearchCard.jsx`
- Modify: `frontend/src/components/Feed.jsx`

**Interfaces:**
- Consumes: `message` object with `text`, `routing`, `evidence`, `module_results`, `latency_ms`
- Consumes: `onSelectEvidence(index: number)`, `onSelectModule(name: string)`
- Produces: Structured report card with interactive `[n]` citation pills, complexity badges, export toolbar, and metadata footer.

- [ ] **Step 1: Implement `ResearchCard.jsx`**

Implement:
- Card header with query summary, timestamp, router complexity pill, retrieval depth indicator, and action buttons (`Copy Markdown`, `Export BibTeX`, `Inspect`).
- Markdown body with custom renderer mapping `[n]` bracket citations to clickable interactive citation badges.
- Execution metrics footer showing response latency, chunk count, and triggered modules.

- [ ] **Step 2: Update `Feed.jsx` to render `ResearchCard` for assistant turns**

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/ResearchCard.jsx frontend/src/components/Feed.jsx
git commit -m "feat(frontend): create ResearchCard component with interactive citations and export"
```

---

### Task 4: Build Persistent Research Inspector (`frontend/src/components/ResearchInspector.jsx`)

**Files:**
- Create: `frontend/src/components/ResearchInspector.jsx`

**Interfaces:**
- Consumes: `activeMessage` (the currently selected or latest assistant research card)
- Consumes: `activeTab` ("evidence" | "modules" | "citations")
- Consumes: `focusedEvidenceIndex: number | null`
- Produces: Tabbed inspection panel for RAPTOR tree levels, specialist reasoning details, and citations/audit.

- [ ] **Step 1: Implement `ResearchInspector.jsx`**

Implement 3 segmented tabs:
1. **Evidence & Tree View**: Lists retrieved ChromaDB chunks with RAPTOR level badges (`Level 0 Leaf`, `Level 1 Summary`, `Level 2 Root`), cosine similarity meters, source document badges, page numbers, and expandable passage readers.
2. **Specialist Reasoning View**: Dedicated visualizations for each active module:
   - `contradiction_detection`: Side-by-side conflicting statement cards.
   - `numeric_reasoning`: Calculated numerical answer and arithmetic expressions.
   - `multi_hop`: Multi-hop reasoning steps and entity chains.
   - `fact_lookup`: Extracted factual assertions and confidence scores.
3. **Citations & Audit View**: Formatted BibTeX and APA reference list with copy buttons, plus raw JSON debug trace.

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/ResearchInspector.jsx
git commit -m "feat(frontend): create ResearchInspector with RAPTOR tree and module reasoning tabs"
```

---

### Task 5: Integrate Two-Column Layout, Scope Switch, and Input Controls in `App.jsx`

**Files:**
- Modify: `frontend/src/App.jsx`
- Modify: `frontend/src/components/InputBar.jsx`

**Interfaces:**
- Consumes: `scope` ("all" | "files")
- Produces: Responsive Two-Column Studio Layout coordinating `ResearchFeed`, `ResearchInspector`, and `InputBar`.

- [ ] **Step 1: Add Retrieval Scope Switch and Inspector State in `App.jsx`**

- Add state `scope` ("all" | "files") defaulting to "all" (or "files" if files are present).
- Add state `focusedCardId` and `inspectorOpen` (boolean).
- If `scope === "all"`, pass `fileIds: []` to `sendChat` so the entire corpus is queried regardless of session attachments. If `scope === "files"`, filter strictly to active session files.

- [ ] **Step 2: Update `InputBar.jsx` with Scope Toggle Pill and Expansive Textarea**

- [ ] **Step 3: Commit**

```bash
git add frontend/src/App.jsx frontend/src/components/InputBar.jsx
git commit -m "feat(frontend): wire two-column studio layout and explicit retrieval scope switch"
```

---

### Task 6: Research Studio CSS Design System (`frontend/src/research.css`)

**Files:**
- Create: `frontend/src/research.css`
- Modify: `frontend/src/main.jsx`

- [ ] **Step 1: Write `frontend/src/research.css`**

Implement a comprehensive design system:
- High-readability typography with clean contrast for tables, blockquotes, and code blocks.
- Two-column responsive workbench layout (`.studio-container`, `.studio-main`, `.studio-inspector`).
- Visual score bars, RAPTOR tree level badges (`leaf`, `summary`, `root`), citation pills, and clean segmented tabs.
- Smooth transitions and collapsible drawer support for responsive viewports.

- [ ] **Step 2: Import `research.css` in `main.jsx`**

- [ ] **Step 3: Commit**

```bash
git add frontend/src/research.css frontend/src/main.jsx
git commit -m "style(frontend): implement comprehensive Research Studio CSS design system"
```

---

### Task 7: Verification and End-to-End Smoke Test

**Files:**
- Test: `tests/test_chat_endpoint.py`
- Test: `tests/test_query_endpoint.py`
- Test: `npm run build`

- [ ] **Step 1: Run full backend test suite**

Run: `pytest tests/`
Expected: 529+ passed

- [ ] **Step 2: Run frontend production build**

Run: `cd frontend && npm run build`
Expected: Build succeeds with 0 errors

- [ ] **Step 3: Verify linter**

Run: `ruff check api/ tests/`
Expected: All checks passed
