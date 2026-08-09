# Copilot Review Instructions for GRAFT

These instructions guide Copilot's automated code review on pull requests in this
repository. Follow the project's CONTRIBUTING.md rules as the source of truth;
this file just points Copilot at what matters most for this specific codebase.

## What to Check on Every PR

### 1. Tests
- Flag any new function, module, or class that has no accompanying test or smoke
  test. Even a single concrete assertion is required — shallow, untested code is
  not acceptable per project convention.
- Flag tests that only check "it runs without crashing" but assert nothing about
  correctness (e.g. missing `assert` on actual output values).

### 2. Interface Contracts
This project has multiple independently-owned modules that must interoperate:
`indexing/`, `router/`, `modules/fact_lookup/`, `modules/multi_hop/`,
`modules/numeric_reasoning/`, `modules/contradiction_detection/`, `generation/`,
and `baseline/`.

- If a PR changes a function signature, return type, or data shape (e.g. what the
  router passes to a specialist module, or what a module returns to generation),
  flag it explicitly as an **interface change** and check whether
  `docs/interfaces.md` was updated to match.
- If an interface change isn't flagged in the PR description, call this out —
  the PR template has a checkbox for this; make sure it's filled in accurately.

### 3. Secrets and Credentials
- Flag any hardcoded API keys, tokens, database URLs, or credentials in code.
- Flag `.env` files or config files containing real secrets being committed
  (should only ever commit `.env.example` with placeholder values).

### 4. Benchmarking Impact
- If a PR modifies logic in `router/` (complexity thresholds, gating scores),
  `indexing/` (tree construction, clustering, summarization), or any file in
  `modules/` that affects retrieval or reasoning behavior, flag that this may
  change benchmark results and the benchmarking owner should be notified/tagged.
- Changes to `benchmark/` itself should be checked for whether they alter how
  metrics are computed (accuracy, latency, modules-fired-per-query, retrieval
  precision) — if the metric definition changes, prior benchmark results may no
  longer be comparable, and this should be called out.

### 5. Scope Discipline
- This project has a locked MVP: tree construction + router/gating + 4 specialist
  modules + benchmarking against a flat RAG baseline. Stretch goals
  (cross-query pipelining, learned/trained gating thresholds, persistent
  write-back) are explicitly out of scope until the MVP is integrated and
  benchmarked.
- If a PR appears to implement a stretch-goal feature, note this so the team can
  confirm it's an intentional, timeline-aware decision rather than scope creep.

### 6. Code Quality Basics
- Flag lazy fallbacks (e.g. bare `except: pass`, silently returning empty/default
  values on failure instead of raising or logging) — this project's convention is
  to fail loudly and visibly during development, not hide errors.
- Flag magic numbers used for thresholds (e.g. gating cutoffs, cluster counts)
  that aren't named constants or config values — these should be easy to find and
  tune, not buried inline.
- Flag missing docstrings on public functions/classes in `indexing/`, `router/`,
  and `modules/` — these are the core architecture and need to be understandable
  by teammates outside the owning module.

## What Not to Flag

- Minor style preferences not enforced by the project's linter config.
- Frontend/UI polish issues unless they block functionality — this is a systems
  project, not a design showcase; don't nitpick CSS choices.
- Missing production-scale concerns (caching, horizontal scaling, load balancing)
  — this is explicitly a research/demo-scale MVP, not a production deployment.

## Tone

Keep review comments direct and specific — point to the exact line/function and
say what's missing or risky, rather than general praise or vague suggestions.
