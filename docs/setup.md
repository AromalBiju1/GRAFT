# Setup & Development

## Prerequisites

- Python 3.10+ (3.11 recommended)
- Node 18+ for the frontend (optional for backend-only work)
- Git

On Windows, install Python 3 from <https://www.python.org/downloads/> (the
`py -3` launcher is supported) and Node.js from <https://nodejs.org/>. Use
PowerShell or `cmd.exe` from the repository root.

## 1. Clone & Python env

```bash
git clone https://github.com/AromalBiju1/GRAFT
cd GRAFT
python -m venv .venv
# macOS/Linux:
source .venv/bin/activate
# Windows PowerShell:
# .\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"        # installs graft + dev deps from pyproject.toml
# or minimal:
pip install -r requirements.txt
```

For optional NLI contradiction detection, install `pip install -e ".[nli]"`.
This installs Transformers with its PyTorch backend and SentencePiece tokenizer.
The `microsoft/deberta-v3-mnli` model is initialized on the first eligible passage
pair, never on import. If dependencies or model loading are unavailable, the
original heuristic remains available. Set `HF_HUB_OFFLINE=1` to prevent Hub
network access; an uncached model then falls back to the heuristic.

## 2. Environment file

```bash
cp .env.example .env        # Windows PowerShell: Copy-Item .env.example .env
# Edit .env — set LLM_ENABLED=true and LLM_PROVIDER when you want real LLM calls.
# The stubs run without any key.
```

Key vars (see `config.py`):

- `LLM_ENABLED` — `false` by default; `/query` uses deterministic synthesis until
  this is `true`
- `LLM_PROVIDER` — `gemini` (default), `openai`, `groq`, `ollama`, `llamacpp`,
  or `local` (an injected callable)
- `LLM_TIMEOUT_SECONDS` — HTTP timeout for Groq/Ollama/llama.cpp (default `120`)
- `LLM_MAX_PROMPT_CHARS` — whole-prompt character budget (default `16000`;
  the dev launcher lowers it to `6000` for local providers)
- `CHROMA_PATH` — default `.graft/chroma`
- `DB_PATH` — default `db/graft_logs.db`
- `API_HOST / API_PORT` — default `127.0.0.1:8000`; the dev launcher and the
  checked-in Vite config use `8001` unless you override `API_PORT`
- `CORS_ORIGINS` — comma-separated allowed browser origins. Empty or `*`
  allows every origin (dev/demo behaviour). Per the CORS spec a wildcard
  origin cannot serve credentialed requests, so wildcard mode automatically
  forces credentials off; an explicit list such as
  `CORS_ORIGINS=http://localhost:5173,https://graft.example.org` keeps
  credentialed cross-origin requests working. Set a real list before
  containerised or publicly-reachable deployments (see Docker setup, #67).

### One-command dev stack

The launcher starts all three processes and stops the ones it started when you
press Ctrl-C:

- the FastAPI backend (starts at `http://127.0.0.1:8001`; if that port is taken
  by another app, it moves to the next free port)
- the Vite frontend (default `http://localhost:5173`)
- a local llama.cpp server (default `http://127.0.0.1:8081/v1`), unless you
  select a hosted provider

All platforms run the same Python implementation, `scripts/dev.py`; the
`dev.sh` / `dev.ps1` / `dev.bat` files are thin wrappers.

```bash
# macOS / Linux
./scripts/dev.sh
```

```powershell
# Windows PowerShell
.\scripts\dev.ps1
```

```bat
rem Windows cmd
scripts\dev.bat
```

If PowerShell blocks local scripts because of its execution policy, use
`scripts\dev.bat` or run
`powershell -ExecutionPolicy Bypass -File .\scripts\dev.ps1`.

Services already listening on those ports are reused, so the launcher is safe
to run twice. `API_PORT` pins the backend port if you need a specific one. The
launcher reads `.env`; `LLM_ENABLED` and `LLM_PROVIDER` default to `true` and
`llamacpp` for this launcher. Values already in the environment override
`.env`: use `VAR=value ./scripts/dev.sh` on macOS/Linux, or set `$env:VAR` /
`set VAR=...` before the Windows wrapper.

The launcher looks for `PYTHON`, then an activated venv, then a `venv/` or
`.venv/` Python, then the interpreter that started it. Node.js and npm must be
on `PATH` for the frontend. On Windows, services are stopped with `taskkill
/T`, so the Vite and uvicorn child processes are cleaned up with the launcher.

```bash
cp .env.example .env        # Windows PowerShell: Copy-Item .env.example .env
# Either set a model path:
#   LLAMACPP_MODEL_PATH=/absolute/path/to/your-model.gguf
# or drop a .gguf into models/ and leave the path unset.
./scripts/dev.sh
```

Windows `.env` example:

```env
LLAMACPP_MODEL_PATH=C:\models\your-model.gguf
LLAMA_SERVER_BIN=C:\tools\llama.cpp\llama-server.exe
LLAMACPP_ARGS="-ngl 99 --jinja --ctx-size 2048"
```

To use Groq instead (no local model):

```bash
LLM_ENABLED=true LLM_PROVIDER=groq GROQ_API_KEY=... ./scripts/dev.sh
```

```powershell
$env:LLM_ENABLED = "true"
$env:LLM_PROVIDER = "groq"
$env:GROQ_API_KEY = "..."
.\scripts\dev.ps1
```

### LLM backends

Select one with `LLM_ENABLED=true` and `LLM_PROVIDER`. Gemini and OpenAI need
their optional SDKs (`pip install -e ".[llm]"`). Groq, Ollama, and llama.cpp use
the OpenAI-compatible `/chat/completions` API through `httpx`, which is already a
core dependency.

GPT-OSS 120B is the default only for Groq. Local models are whatever GGUF or
Ollama model fits the machine.

Groq:

```bash
# .env
LLM_ENABLED=true
LLM_PROVIDER=groq
GROQ_API_KEY=your-key
GROQ_MODEL=openai/gpt-oss-120b
```

llama.cpp with a local GGUF:

```bash
# .env — the file can live anywhere; absolute paths work too.
LLM_ENABLED=true
LLM_PROVIDER=llamacpp
LLAMACPP_MODEL_PATH=/path/to/your-model.gguf
# Optional, for non-standard quantisation or a custom build:
LLAMA_SERVER_BIN=/path/to/llama-server
LLAMACPP_ARGS="-ngl 99 --jinja --ctx-size 2048"
```

On Windows, use normal Windows paths (forward slashes also work):

```env
LLM_ENABLED=true
LLM_PROVIDER=llamacpp
LLAMACPP_MODEL_PATH=C:\models\your-model.gguf
LLAMA_SERVER_BIN=C:\tools\llama.cpp\llama-server.exe
LLAMACPP_ARGS="-ngl 99 --jinja --ctx-size 2048"
```

The server is expected at `http://127.0.0.1:8081/v1`. The dev launcher starts it
with `--alias graft-local`, so `LLAMACPP_MODEL=graft-local` matches. If you run
`llama-server` yourself on 8081, the launcher reuses it. If your GGUF only loads
with a custom llama.cpp build, point `LLAMA_SERVER_BIN` at that binary and put
the build's flags in `LLAMACPP_ARGS`. Quote `LLAMACPP_ARGS` because it contains
spaces; the launcher splits it into separate arguments. Quote any individual
path inside it that also contains spaces.

Ollama:

```bash
ollama pull llama3.2:3b
ollama serve
# .env
LLM_ENABLED=true
LLM_PROVIDER=ollama
OLLAMA_MODEL=llama3.2:3b
```

`llama.cpp`, `llama-cpp`, and `llama_cpp` are accepted as aliases for `llamacpp`.
Override `LLAMACPP_BASE_URL` or `OLLAMA_BASE_URL` if a server listens elsewhere.

## 3. Verify backend stubs

```bash
pytest -q
python -m db.logger            # SQLite logger smoke test
python -c "from router import route; print(route('Compare the two RFCs'))"
python -c "from indexing.pipeline import build_tree; print(len(build_tree('Hello world. '*200)))"
```

## 4. Run the API

```bash
uvicorn api.main:app --reload --port 8001
# or
API_PORT=8001 python -m api.main
# health check
curl http://127.0.0.1:8001/health
curl -X POST http://127.0.0.1:8001/query -H 'Content-Type: application/json' -d '{"query":"What is TLS 1.3?"}'
```

Index a document via the API:

```bash
curl -X POST http://127.0.0.1:8001/index -H 'Content-Type: application/json' \
  -d '{"text":"Your document text...","document_id":"doc_001","source":"example.pdf"}'
```

Or via Python:

```python
from indexing.pipeline import index_document
nodes = index_document("Your text...", document_id="doc_001", source="example.pdf")
```

## 5. Frontend

```bash
cd frontend
npm install
npm run dev    # Vite at http://localhost:5173, proxies /query -> 127.0.0.1:8001
```

Ensure the API (`api.main:app`) is running on 8001. The UI calls `POST /query`
through the Vite development proxy to `http://127.0.0.1:8001`.

## 6. Sample docs

`data/sample_docs/` contains RFCs, Transformer papers, and the NIST OSCAL
catalog with notes in `data/sample_docs/sample_docs.md`. Use them to seed the
index and test contradiction / numeric cases from `benchmark/datasets.py`.

Index the whole sample corpus before running the benchmark (document IDs are
the file stems, which is what the chunker hashes into chunk IDs):

```bash
python scripts/index_sample_corpus.py
# regenerate the labelled chunk IDs afterwards if needed:
python scripts/regenerate_benchmark_chunk_ids.py --verify
```

Then run:

```bash
python3 -m benchmark.runner --dataset data/sample_docs/benchmark.jsonl --output results.json
```

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
