# 07 - Testing and CI

GRAFT has a large backend suite (500+ tests), a small frontend test, and a CI
workflow that runs on every pull request to `dev`.

This chapter explains how to run each layer, what the fixtures do, and what CI
expects.

---

## 1. Quick commands

From the repository root, with the virtual environment active:

```bash
# Everything
python -m pytest tests/ -q

# Verbose, with skip reasons
python -m pytest tests/ -v -rs

# One file
python -m pytest tests/test_router.py -q

# One test
python -m pytest tests/test_router.py::TestNegationScoping::test_negated_comparison_stays_simple -q

# Stop at the first failure
python -m pytest tests/ -x

# Include integration tests (real model required)
python -m pytest tests/ -m integration -v
```

Frontend:

```bash
cd frontend
node --test tests/query.test.js
```

Lint and types:

```bash
ruff check .
mypy config.py embeddings.py version.py router retrieval modules generation baseline api indexing db tree_store vector_store
```

`ruff format --check .` is mentioned in `docs/setup.md` but is **not** run by
CI. Use it only if you have adopted the formatter locally.

---

## 2. What the suite expects

### Python

- Python 3.10+; CI uses 3.11.
- Runtime dependencies from `requirements.txt`.
- Dev tools: `pytest`, `pytest-asyncio`, `httpx`, `ruff`, `mypy`.
- A writable working directory. Tests create temp directories and temp Chroma
  collections; they should not touch your real `.graft/chroma` except for the
  benchmark dataset check described below.

### The embedder

The suite is designed to run in two modes:

- **Stub mode (default for most tests).** `EMBEDDING_PROVIDER=stub` or the
  `stub_provider` fixture forces the 16-dimensional hash embedding. Fast,
  deterministic, offline, and semantically meaningless. Most unit tests use
  this.
- **Real mode (integration tests).** `all-MiniLM-L6-v2` (384 dimensions). The
  `integration` marker covers tests that need real semantics. They skip if the
  model is unavailable, so offline CI is not blocked.

`tests/conftest.py` loads the real model once per session and preserves the
cache across tests that reset it. This is a performance optimization: without
it, alternating stub/real tests reloaded the model repeatedly. The fixture is
transparent; no test is skipped or changed because of it.

### The sample corpus

One test, `tests/test_benchmark_dataset.py::test_benchmark_dataset_chunk_ids_exist`,
verifies that every labelled chunk ID in `data/sample_docs/benchmark.jsonl`
resolves in the **default** Chroma collection. On a fresh clone that collection
is empty, so the test fails unless you index first:

```bash
python scripts/index_sample_corpus.py
```

CI does this before pytest for exactly this reason. Most other tests use
temporary collections and do not need the corpus.

### The SQLite database

Tests that exercise analytics or logging may create or modify
`db/graft_logs.db`. If you want to isolate them, point `DB_PATH` at a temp path
(but note that `db/logger.py` currently ignores `DB_PATH`; see chapter 03). In
practice, deleting `db/graft_logs.db` between runs is safe.

---

## 3. Test layout

The full file table is in
[03 - Backend reference](03-backend-reference.md#tests-at-a-glance). Grouped by
purpose:

### Core contracts

| File | Pins |
|---|---|
| `test_router.py` | Scores, thresholds, depth mapping, activation, negation window |
| `test_retrieval.py` | Depth means "deepest level", filters merge correctly, score ordering |
| `test_tree_node.py` | Node invariants, serialization |
| `test_embeddings.py` | Provider selection, dimension agreement, fallback behavior |
| `test_generation.py` | Prompt, evidence dedup, deterministic priority, LLM injection |
| `test_logging_config.py` | Logger setup, levels, idempotence |

### Indexing

| File | Pins |
|---|---|
| `test_parser.py` | PDF/DOCX extraction and error cases |
| `test_indexing.py` | Token chunking, overlap, chunk IDs |
| `test_summarizer.py` | Recursive clustering/summarization with a mock LLM |
| `test_builder_wiring.py` | Builder <-> summarizer <-> chunker seams, provenance, unique IDs |
| `test_batch_indexing.py` | Batch embedding and one-round-trip persistence |
| `test_tree_persistence.py` | Chroma persistence in isolation |
| `test_chroma_store.py` | Insert/query/upsert/filter/edge cases |
| `test_indexing_pipeline.py` | Full parse -> chunk -> embed -> cluster -> summarize -> persist, plus multipart `/index` |

### Modules

`tests/test_modules/` has one file per specialist module. They use fake clients
and hand-built context, so no model or network is needed. They pin the
`ModuleResult` shape and each module's decision rules.

### API and integration

| File | Pins |
|---|---|
| `test_health.py` | `/health` shape and values |
| `test_query_endpoint.py` | `/query` gating, response contract, error handling |
| `test_cors.py` | Parsing, preflight headers, wildcard/credentials invariant |
| `test_upload.py` | `/api/files` type/size/sniffing |
| `test_chat_endpoint.py` | `/api/chat` SSE, file filters, failure paths |
| `test_analytics.py` | Analytics endpoints and live-row visibility |
| `test_integration.py` | Real-model index -> query -> answer and baseline comparison |

### Benchmark

| File | Pins |
|---|---|
| `test_benchmark_dataset.py` | Dataset labels, chunk-ID existence, answer regressions |
| `test_benchmark_runner.py` | Runner logic with injected embedder/retriever, fully offline |

### Baseline

`test_baseline.py` pins the ungated contract: no routing, all four modules,
same generation function. If the baseline silently gains gating, the comparison
becomes meaningless and this test should fail.

---

## 4. How to write a test

Follow the style already in the repo.

### Naming

- File: `test_<subject>.py`.
- Class: `Test<Subject>` when grouping.
- Function: `test_<behavior>`.
- Use a docstring when the behavior is not obvious from the name.

### Structure

```python
def test_negated_comparison_stays_simple() -> None:
    decision = route("This is not a comparison, just tell me the capital.")
    assert decision.complexity == "simple"
    assert decision.activated_modules == ["fact_lookup"]
```

Prefer one behavior per test and concrete assertions over "it did not crash."

### Fixtures

- `tmp_path` for files and Chroma directories.
- `monkeypatch` for settings and environment.
- `stub_provider` (from `conftest.py`) to force the stub embedder without
  evicting the cached real model.
- `pytest.mark.parametrize` for tables of inputs.

### Async

`pyproject.toml` sets `asyncio_mode = "auto"`, so `async def test_...` works
without an explicit marker. `pytest-asyncio` is a dev dependency.

### Markers

```python
@pytest.mark.integration
def test_real_model_retrieval() -> None:
    ...
```

Use `integration` only for tests that need the real sentence-transformers
model. CI verifies the provider before running, so integration tests are not
silently skipped in the main job.

### No network

Unit tests should not call Gemini, OpenAI, or the Hugging Face Hub. Inject a
fake client; the existing module tests show the pattern. The NLI client is
lazy and falls back when unavailable, so contradiction tests do not need the
model.

### Frontend tests

`frontend/tests/query.test.js` uses Node's built-in test runner:

```js
import assert from "node:assert/strict";
import { test } from "node:test";

test("does the thing", async (t) => {
  t.mock.method(globalThis, "fetch", async () => Response.json({ ok: true }));
  // ...
  t.mock.restoreAll();
});
```

There is no DOM test environment. Test pure functions (validation, parsing,
error mapping) rather than rendered components unless you add a DOM library.

---

## 5. CI walkthrough

Workflow: `.github/workflows/ci.yml`. Trigger: `pull_request` targeting `dev`.

| Step | What it does | Failure behavior |
|---|---|---|
| Checkout | Gets the PR branch | Blocks |
| Set up Python 3.11 + pip cache | Reproducible environment | Blocks |
| Install dependencies | `pip install -r requirements.txt` plus pytest/pytest-asyncio/ruff/mypy | Blocks |
| Report embedder | Imports `active_provider()` and **fails if it is not `sentence-transformers`** | Blocks |
| Index sample corpus | Runs `python3 scripts/index_sample_corpus.py` so benchmark chunk IDs resolve | Blocks |
| Run tests | `python3 -m pytest tests/ -v --tb=short -rs` | Blocks |
| Lint | `ruff check .` (`if: always()`) | Blocks |
| Type check | `mypy ...` (`if: always()`, `continue-on-error: true`) | Advisory only |

Why the embedder check exists: without it, a CI machine that failed to install
sentence-transformers would fall back to the stub, the integration tests would
skip, and the job would pass without ever exercising semantic retrieval. The
check turns a silent degradation into a red build.

Why the corpus step exists: `.graft/chroma` is gitignored, so a fresh runner
has no indexed documents. The benchmark dataset test needs them.

Why lint and mypy use `if: always()`: if tests fail, you still get the lint and
type signals in the same run.

### What CI does not do

- It does not run frontend tests.
- It does not build the frontend.
- It does not enforce coverage.
- It does not make mypy blocking.
- It does not run `ruff format --check`.

If you add any of these, update this chapter and the workflow together.

---

## 6. Coverage

`pyproject.toml` configures coverage sources:

```toml
[tool.coverage.run]
source = ["api", "baseline", "db", "embeddings.py", "generation",
          "indexing", "modules", "retrieval", "router", "tree_store",
          "vector_store"]
omit = ["tests/*"]
```

`coverage` is not in the dev extras by default. If you install it:

```bash
pip install coverage
coverage run -m pytest tests/
coverage report -m
coverage html
```

Coverage is a diagnostic, not a gate. A high percentage with weak assertions is
worse than a lower percentage with behavioral tests.

---

## 7. Common test failures

| Failure | Cause | Fix |
|---|---|---|
| `test_benchmark_dataset_chunk_ids_exist` fails | Default Chroma collection is empty | `python scripts/index_sample_corpus.py` |
| `test_integration.py` skipped | Real model unavailable | Install sentence-transformers / allow the model download; check `/health` |
| Dimension mismatch (`expected 16, got 384`) | Indexed with stub, queried with real, or vice versa | Clear `.graft/chroma` and re-index with one provider |
| `ImportError: sentence_transformers` | Runtime deps not installed | `pip install -r requirements.txt` |
| Chroma lock / timeout | Another process holds `.graft/chroma` | Stop the API or use a temp path |
| SQLite locked | Stale journal or concurrent writers | Remove `db/graft_logs.db-journal`, retry |
| `ruff` import-order error | New imports un-sorted | `ruff check --fix <file>` |
| Frontend test not found | Wrong working directory | `cd frontend` first |
| `node --test` fails on `crypto.randomUUID` | Old Node or non-secure context | Use Node `^20.19.0` or `>=22.12.0`; run on localhost/HTTPS |

---

## 8. Contributing workflow

This is covered in full in `CONTRIBUTING.md`; the essentials:

1. Branch from `dev`, not `main`. Use `feature/<area>-<description>` or
   `fix/<area>-<description>`.
2. Keep the change focused. Add or update a test.
3. Run the relevant tests and `ruff check .` locally.
4. Pull latest `dev` and resolve conflicts locally.
5. Open a PR into `dev` using `.github/PULL_REQUEST_TEMPLATE.md`.
6. Fill every section: Summary, Module checkboxes, What Changed, Why, How to
   Test, Interface Changes, Checklist, Benchmarking Impact, Related Issues.
7. Get one approval from someone other than the author. If you touched another
   owner's module, that owner reviews.

Never push directly to `dev` or `main`. If a PR changes router, indexing, or
module behavior, flag the benchmarking owner; prior results may no longer be
comparable.

### Commit messages

`<module>: <what changed>`, for example:

```text
router: add negation window for hint matching
api: implement chat query orchestration
docs: add Library onboarding guide
```

---

## 9. Benchmark impact checklist

Before merging a change, ask:

- Does it change how documents are chunked, clustered, or summarized?
- Does it change the router's score, thresholds, depth, or activation?
- Does it change a module's output or confidence?
- Does it change generation priority or prompting?
- Does it change a metric definition in `benchmark/runner.py`?

If yes to any, the benchmark owner should review, and prior results should be
marked as pre-change. The PR template has a dedicated section for this.

---

Next: [08 - Troubleshooting](08-troubleshooting.md)
