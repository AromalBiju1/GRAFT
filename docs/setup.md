# Setup & Development

## Prerequisites

- Python 3.10+ (3.11 recommended)
- Node 18+ for the frontend (optional for backend-only work)
- Git

## 1. Clone & Python env

```bash
git clone https://github.com/AromalBiju1/GRAFT
cd GRAFT
python -m venv .venv && source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"        # installs graft + dev deps from pyproject.toml
# or minimal:
pip install -r requirements.txt
```

## 2. Environment file

```bash
cp .env.example .env
# Edit .env — at minimum set GRAFT_LLM_PROVIDER and an API key if you want real LLM calls.
# The stubs run without any key.
```

Key vars (see `graft/config.py`):

- `GRAFT_CHROMA_PATH` — default `.graft/chroma`
- `GRAFT_DB_PATH` — default `db/graft_logs.db`
- `GRAFT_API_HOST / GRAFT_API_PORT` — default `127.0.0.1:8000`

## 3. Verify backend stubs

```bash
pytest -q
python -m db.logger            # SQLite logger smoke test
python -c "from graft.router import route; print(route('Compare the two RFCs'))"
python -c "from graft.indexing.pipeline import build_tree; print(len(build_tree('Hello world. '*200)))"
```

## 4. Run the API

```bash
uvicorn graft.api.main:app --reload --port 8000
# or
python -m graft.api.main
# health check
curl http://127.0.0.1:8000/health
curl -X POST http://127.0.0.1:8000/query -H 'Content-Type: application/json' -d '{"query":"What is TLS 1.3?"}'
```

Index a document via the API:

```bash
curl -X POST http://127.0.0.1:8000/index -H 'Content-Type: application/json' \
  -d '{"text":"Your document text...","document_id":"doc_001","source":"example.pdf"}'
```

Or via Python:

```python
from graft.indexing.pipeline import index_document
nodes = index_document("Your text...", document_id="doc_001", source="example.pdf")
```

## 5. Frontend

```bash
cd frontend
npm install
npm run dev    # Vite at http://localhost:5173, proxies /api -> 127.0.0.1:8000
```

Ensure the API is running on 8000. The UI calls `/api/query` when proxied,
otherwise falls back to `http://127.0.0.1:8000/query`.

## 6. Sample docs

`data/sample_docs/` contains RFCs, Transformer papers, and the NIST OSCAL
catalog with notes in `data/sample_docs/sample_docs.md`. Use them to seed the
index and test contradiction / numeric cases from `graft/benchmark/datasets.py`.

## 7. Lint / Typecheck

```bash
ruff check .
ruff format --check .
mypy graft
```

## Troubleshooting

- **Chroma lock / .graft/chroma open**: stop other processes holding the DB, or use a temp path in tests.
- **DB locked**: SQLite logger uses short connections; ensure no stale `graft_logs.db-journal`.
- **Frontend blank**: check `vite.config.js` proxy target matches your API host/port.
