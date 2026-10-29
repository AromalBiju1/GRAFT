# Modern Minimal Research Studio Redesign Spec

**Date:** 2026-10-29  
**Status:** Approved  
**Author:** Pair Programming Agent & User  
**Target:** Eliminate retro CRT/terminal "old TV" aesthetic; deliver a Linear / Perplexity Pro modern minimal research workbench.

---

## 1. Problem Statement & Motivation
The previous UI iteration introduced structured research cards, but used a dark-navy/pitch-black palette with high-contrast electric blue borders around every query and response, uppercase retro badges (`@ RESEARCHER QUERY`, `RESEARCH SYNTHESIS`), and un-styled OS scrollbars that rendered as glaring white sliders inside dark code boxes.

The user feedback:
> *"can't u have a better ui than this old tv like ui"*

This spec establishes a modern, premium research studio design inspired by **Linear**, **Perplexity Pro**, and **Raycast**, focusing on quiet hairline borders, a deep zinc palette, seamless dark scrollbars, a conversational stream with right-aligned user prompts, and expansive editorial research report cards.

---

## 2. Visual Architecture & Design Tokens

### A. Surface Palette (Zinc Scale)
- Canvas Background (`--studio-bg`): `#09090b` (Deep neutral zinc, eliminates murky blue-black)
- Workspace & Feed Panel (`--studio-panel`): `#0f0f12`
- Card Surfaces (`--studio-card`): `#141417`
- Card Hover (`--studio-card-hover`): `#1a1a1e`
- Subtle Hairline Borders (`--studio-border`): `rgba(255, 255, 255, 0.08)` (Replaces glowing neon cyan/blue)
- Elevated Popovers & Dropdowns: `#18181b` with `box-shadow: 0 10px 30px -10px rgba(0, 0, 0, 0.5)`

### B. Typography & Text Hierarchy
- System typography: `-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Inter, sans-serif`
- Heading font-weight: `600`, letter-spacing `-0.015em`
- Body line-height: `1.65` for optimal long-form reading comfort
- Text colors:
  - Primary: `#f4f4f5` (Zinc 100)
  - Secondary: `#a1a1aa` (Zinc 400)
  - Muted: `#71717a` (Zinc 500)

### C. Universal Dark Scrollbar System
Fixes the white default browser scrollbars across Firefox, Chrome, and WebKit:
- `scrollbar-width: thin; scrollbar-color: rgba(255, 255, 255, 0.15) transparent;`
- Webkit track transparent, thumb `rgba(255, 255, 255, 0.15)` with `border-radius: 9999px`
- Hover state: `rgba(255, 255, 255, 0.28)`
- Applied globally (`*`, `html`, `body`) and on all `<pre>`, `<code>`, `.trace-viewer-block`, and `.inspector-content` containers.

---

## 3. Component Specifications

### 1. Conversational Feed (`Feed.jsx`)
- **User Message (`.user-query-bubble`)**:
  - Compact right-aligned bubble (`align-self: flex-end`, max width 75%).
  - Background: `#27272a`, border: `1px solid rgba(255, 255, 255, 0.08)`.
  - Border radius: `18px 18px 4px 18px`.
  - Removes `@ RESEARCHER QUERY` uppercase terminal header.
  - Attached document chips render as sleek minimal tags above or within the prompt bubble.

### 2. Editorial Research Card (`ResearchCard.jsx`)
- **Container**:
  - Expansive card aligned left (`align-self: flex-start`, full width of feed column).
  - Background: `#141417`, border: `1px solid rgba(255, 255, 255, 0.08)`.
  - Border radius: `12px`.
  - Subtle top highlight line (`linear-gradient(90deg, rgba(99, 102, 241, 0.3) 0%, transparent 60%)`).
- **Card Header**:
  - Left: Clean status dot + `Complexity: Simple/Moderate/Complex` badge, subtle `Depth: L0/L1/L2` tag.
  - Right: Ghost action buttons with crisp SVG icons (`Copy Markdown`, `BibTeX`, `Inspect`).
- **Body & Citations**:
  - Clean markdown prose.
  - Numbered citation pills `[1]`, `[2]`: Frosted indigo buttons (`background: rgba(99, 102, 241, 0.12); color: #818cf8; border-radius: 6px; font-weight: 600; padding: 1px 6px`).
  - Hover: subtle glow; Click: scrolls inspector directly to matching node.
- **Card Telemetry Footer**:
  - Quiet, low-contrast footer (`color: #71717a`):
  - `2 source nodes` • `⚡ Fact Lookup` • `38.8ms`.

### 3. Inspector Drawer (`ResearchInspector.jsx`)
- Background: `#0f0f12`, border-left: `1px solid rgba(255, 255, 255, 0.08)`.
- Segmented tabs: Clean pill switch (Linear style).
- Tree node cards: `#18181b` with subtle depth level tags (`L0 Leaf`, `L1 Summary`, `L2 Root`).
- Code blocks: Dark background `#0c0c0e`, border `rgba(255, 255, 255, 0.06)`, custom dark scrollbars, 1-click copy button.

### 4. Input Area & Scope Switcher (`InputBar.jsx`)
- Segmented Scope Switch: Clean macOS/Linear-style pill toggle (`Entire Knowledge Base` vs `Attached Files`).
- Textarea: Frosted glass docked card (`background: rgba(20, 20, 23, 0.85); backdrop-filter: blur(12px); border: 1px solid rgba(255, 255, 255, 0.1)`).
- Circular send button with SVG icon.

---

## 4. Verification & Testing Criteria
- `pytest tests/`: All 558 unit/integration tests must pass.
- `ruff check api/`: Clean with 0 errors.
- `npm run build`: Vite build succeeds with 0 errors.
- Visual headless screenshot verification: Headless browser captures verify that no white scrollbars or glowing blue CRT borders remain.
