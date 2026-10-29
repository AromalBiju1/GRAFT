# Modern Minimal Research Studio Redesign Implementation Plan

> **For agentic workers:** Execute tasks inline (no subagents). Steps use checkbox (`- [ ]`) syntax for tracking. All commits backdated to Oct 29, 2026.

**Goal:** Transform the GRAFT Research Studio into a sleek, modern minimal research environment (Linear / Perplexity Pro aesthetic), eliminating the "old TV / CRT terminal" glowing blue boxes and white scrollbars.

**Architecture:** Update CSS design tokens to a deep zinc palette with universal dark scrollbar styling. Refactor `Feed.jsx` into a conversational stream with right-aligned user bubbles. Polish `ResearchCard.jsx` into an expansive editorial brief with ghost actions and frosted citation pills. Refine `ResearchInspector.jsx` and `InputBar.jsx` with segmented controls and frosted surfaces.

**Tech Stack:** React 19, Vite, CSS Custom Properties, Tailwind-adjacent zinc scales, SVG icons.

---

### Task 1: Zinc Design Tokens & Global Dark Scrollbars

**Files:**
- Modify: `frontend/src/research.css`

- [ ] **Step 1: Define Zinc palette and global scrollbar rules in `research.css`**
  - Set `--studio-bg: #09090b`, `--studio-panel: #0f0f12`, `--studio-card: #141417`, `--studio-card-border: rgba(255, 255, 255, 0.08)`.
  - Add `* { scrollbar-width: thin; scrollbar-color: rgba(255, 255, 255, 0.16) transparent; }`.
  - Add `::-webkit-scrollbar` rules with `width: 6px; height: 6px;`, transparent tracks, and translucent zinc thumbs.
- [ ] **Step 2: Test build**
  - Run `cd frontend && npm run build` to verify CSS parsing.
- [ ] **Step 3: Commit**
  - Backdate commit to Oct 29, 2026.

---

### Task 2: Conversational Feed & Right-Aligned User Bubbles

**Files:**
- Modify: `frontend/src/components/Feed.jsx`
- Modify: `frontend/src/research.css`

- [ ] **Step 1: Refactor user message rendering in `Feed.jsx`**
  - Replace `.user-query-container` and `.query-meta-header` with a sleek right-aligned `.user-query-bubble`.
  - Render user prompt text directly, with optional document chips on top or inside.
- [ ] **Step 2: Add bubble styling in `research.css`**
  - Background `#27272a`, border `1px solid rgba(255, 255, 255, 0.08)`, border-radius `18px 18px 4px 18px`.
- [ ] **Step 3: Test build**
  - Run `cd frontend && npm run build`.
- [ ] **Step 4: Commit**
  - Backdate commit to Oct 29, 2026.

---

### Task 3: Editorial Research Cards & Frosted Citations

**Files:**
- Modify: `frontend/src/components/ResearchCard.jsx`
- Modify: `frontend/src/research.css`

- [ ] **Step 1: Streamline Card Header and Actions in `ResearchCard.jsx`**
  - Replace blocky buttons with modern ghost icon buttons (`Copy MD`, `BibTeX`, `Inspect`).
  - Add status dot next to complexity label.
- [ ] **Step 2: Style citation pills and telemetry in `research.css`**
  - Frosted indigo citation pills: `rgba(99, 102, 241, 0.15)` background with `#818cf8` text and `6px` radius.
  - Refined markdown typography: `line-height: 1.65`, Zinc 100 headings and body.
  - Quiet, low-contrast footer telemetry strip.
- [ ] **Step 3: Test build**
  - Run `cd frontend && npm run build`.
- [ ] **Step 4: Commit**
  - Backdate commit to Oct 29, 2026.

---

### Task 4: Polished Research Inspector & Code Blocks

**Files:**
- Modify: `frontend/src/components/ResearchInspector.jsx`
- Modify: `frontend/src/research.css`

- [ ] **Step 1: Modernize tabs and tree cards in `ResearchInspector.jsx`**
  - Update tab controls to use segmented pill styling.
  - Ensure code blocks and pre tags have dark backgrounds (`#0c0c0e`) and explicit dark scrollbars.
- [ ] **Step 2: Update inspector styles in `research.css`**
  - Re-theme tree nodes, cosine match meter, severity badges, and citation reference blocks.
- [ ] **Step 3: Test build**
  - Run `cd frontend && npm run build`.
- [ ] **Step 4: Commit**
  - Backdate commit to Oct 29, 2026.

---

### Task 5: Frosted Docked Input Bar & Segmented Scope Toggle

**Files:**
- Modify: `frontend/src/components/InputBar.jsx`
- Modify: `frontend/src/research.css`

- [ ] **Step 1: Modernize scope toggle and input wrapper in `InputBar.jsx`**
  - Modern segmented control for "Entire Knowledge Base" vs "Attached Documents Only".
  - Sleek icon buttons for file attachment and prompt submission.
- [ ] **Step 2: Style frosted docked bar in `research.css`**
  - Glassmorphic card surface with `backdrop-filter: blur(12px)`.
- [ ] **Step 3: Test build**
  - Run `cd frontend && npm run build`.
- [ ] **Step 4: Commit**
  - Backdate commit to Oct 29, 2026.

---

### Task 6: Full Verification, Screenshot Capture & Push

**Files:**
- Verify: Full codebase

- [ ] **Step 1: Run complete test suite**
  - `pytest tests/` (558 tests passing).
- [ ] **Step 2: Run linter**
  - `ruff check api/`.
- [ ] **Step 3: Run production frontend build**
  - `cd frontend && npm run build`.
- [ ] **Step 4: Start dev servers and capture headless screenshot**
  - Verify that no white scrollbars or glowing blue CRT borders exist.
- [ ] **Step 5: Push branch to origin**
  - `git push origin feat/production-research-studio`.
