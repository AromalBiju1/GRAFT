#!/usr/bin/env bash
# One-command development startup for GRAFT.
#
# Starts:
#   1. a local OpenAI-compatible LLM (llama.cpp by default), unless the selected
#      provider is hosted (groq/gemini/openai)
#   2. the FastAPI backend
#   3. the Vite frontend
#
# Services already listening on the configured ports are reused. That makes it
# safe to run while an earlier copy is still up.
#
# Usage:
#   ./scripts/dev.sh
#   LLM_PROVIDER=groq GROQ_API_KEY=... ./scripts/dev.sh
#   LLAMACPP_MODEL_PATH=/path/to/model.gguf ./scripts/dev.sh
#
# Configuration is read from the environment and, when present, from .env.
# The script defaults to LLM_ENABLED=true and LLM_PROVIDER=llamacpp so the
# backend talks to the local server it starts on 127.0.0.1:8081.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
fi

# Load .env without overriding variables already present in the environment.
# Sourcing in a subshell and copying only new keys keeps inline overrides such
# as `LLM_PROVIDER=groq ./scripts/dev.sh` working.
if [[ -f "$ROOT/.env" ]]; then
  while IFS= read -r -d '' __env_entry; do
    __env_key="${__env_entry%%=*}"
    __env_value="${__env_entry#*=}"
    if [[ "$__env_key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ && -z "${!__env_key+x}" ]]; then
      export "$__env_key=$__env_value"
    fi
  done < <(
    set -a
    # shellcheck disable=SC1091
    source "$ROOT/.env"
    set +a
    env -0
  )
  unset __env_entry __env_key __env_value
fi

PYTHON_BIN="${PYTHON:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  if command -v python >/dev/null 2>&1; then
    PYTHON_BIN=python
  else
    PYTHON_BIN=python3
  fi
fi
command -v "$PYTHON_BIN" >/dev/null 2>&1 || {
  echo "[dev] error: python not found" >&2
  exit 1
}

API_HOST="${API_HOST:-127.0.0.1}"
# 8001 matches frontend/vite.config.js and avoids the unrelated app that often
# holds port 8000 on development machines.
API_PORT="${API_PORT:-8001}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
LLM_ENABLED="${LLM_ENABLED:-true}"
LLM_PROVIDER="${LLM_PROVIDER:-llamacpp}"
LLAMACPP_BASE_URL="${LLAMACPP_BASE_URL:-http://127.0.0.1:8081/v1}"
OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://127.0.0.1:11434/v1}"
LLAMACPP_MODEL="${LLAMACPP_MODEL:-graft-local}"
LLAMA_SERVER_BIN="${LLAMA_SERVER_BIN:-llama-server}"
LLAMACPP_ARGS="${LLAMACPP_ARGS:-}"

case "$LLM_PROVIDER" in
  llama.cpp | llama-cpp | llama_cpp) LLM_PROVIDER=llamacpp ;;
esac

# Local servers usually have a much smaller context than hosted APIs. Keep the
# prompt inside it; override if your llama.cpp/Ollama context is larger.
case "$LLM_PROVIDER" in
  llamacpp | ollama) LLM_MAX_PROMPT_CHARS="${LLM_MAX_PROMPT_CHARS:-6000}" ;;
esac
LLM_MAX_PROMPT_CHARS="${LLM_MAX_PROMPT_CHARS:-16000}"

export API_HOST API_PORT
export LLM_ENABLED LLM_PROVIDER LLM_MAX_PROMPT_CHARS
export LLAMACPP_BASE_URL LLAMACPP_MODEL OLLAMA_BASE_URL

PIDS=()

info() { printf '[dev] %s\n' "$*"; }
warn() { printf '[dev] warning: %s\n' "$*" >&2; }
die() {
  printf '[dev] error: %s\n' "$*" >&2
  exit 1
}

cleanup() {
  local status=$?
  trap - INT TERM EXIT
  if (( ${#PIDS[@]} > 0 )); then
    info "stopping services started by this script"
    for pid in "${PIDS[@]}"; do
      kill "$pid" 2>/dev/null || true
    done
    wait 2>/dev/null || true
  fi
  exit "$status"
}
trap cleanup INT TERM EXIT

http_ok() {
  "$PYTHON_BIN" - "$1" <<'PY'
import sys
import urllib.request

try:
    with urllib.request.urlopen(sys.argv[1], timeout=1.5) as response:
        ok = response.status < 500
except Exception:
    ok = False
sys.exit(0 if ok else 1)
PY
}

port_open() {
  "$PYTHON_BIN" - "$1" "$2" <<'PY'
import socket
import sys

sock = socket.socket()
sock.settimeout(0.5)
try:
    sock.connect((sys.argv[1], int(sys.argv[2])))
except OSError:
    sys.exit(1)
else:
    sys.exit(0)
finally:
    sock.close()
PY
}

graft_ready_on() {
  "$PYTHON_BIN" - "$1" "$2" <<'PY'
import json
import sys
import urllib.request

url = f"http://{sys.argv[1]}:{sys.argv[2]}/health"
try:
    with urllib.request.urlopen(url, timeout=1.5) as response:
        data = json.load(response)
except Exception:
    sys.exit(1)
sys.exit(0 if data.get("status") == "ok" else 1)
PY
}

graft_ready() {
  graft_ready_on "$API_HOST" "$API_PORT"
}

frontend_ready() {
  "$PYTHON_BIN" - "$FRONTEND_PORT" <<'PY'
import sys
import urllib.request

port = sys.argv[1]
for host in ("127.0.0.1", "[::1]"):
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/", timeout=1.5) as response:
            page = response.read(8192).decode("utf-8", "ignore")
    except Exception:
        continue
    if "GRAFT" in page:
        sys.exit(0)
sys.exit(1)
PY
}

frontend_port_open() {
  port_open "127.0.0.1" "$FRONTEND_PORT" || port_open "::1" "$FRONTEND_PORT"
}

choose_api_port() {
  if graft_ready; then
    return 0
  fi
  if ! port_open "$API_HOST" "$API_PORT"; then
    return 0
  fi

  warn "port ${API_PORT} is occupied by a service that is not GRAFT"
  local candidate
  for candidate in 8001 8002 8003 8004 8005; do
    if [[ "$candidate" == "$API_PORT" ]]; then
      continue
    fi
    if graft_ready_on "$API_HOST" "$candidate"; then
      info "found GRAFT API on port ${candidate}; reusing it"
      API_PORT="$candidate"
      export API_PORT
      return 0
    fi
    if ! port_open "$API_HOST" "$candidate"; then
      info "using port ${candidate} for the API instead"
      API_PORT="$candidate"
      export API_PORT
      return 0
    fi
  done
  die "no free API port found (tried ${API_PORT} and 8001-8005)"
}

url_host_port() {
  "$PYTHON_BIN" - "$1" <<'PY'
import sys
import urllib.parse

url = urllib.parse.urlsplit(sys.argv[1])
print(url.hostname or "127.0.0.1")
print(url.port or (443 if url.scheme == "https" else 80))
PY
}

wait_for_pid() {
  local pid="$1"
  local attempts="$2"
  shift 2
  local i=0
  while (( i < attempts )); do
    if "$@"; then
      return 0
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
      return 2
    fi
    sleep 1
    i=$((i + 1))
  done
  return 1
}

llm_ready() {
  http_ok "${LLAMACPP_BASE_URL%/}/models"
}

start_llamacpp() {
  local models_url="${LLAMACPP_BASE_URL%/}/models"
  if http_ok "$models_url"; then
    info "llama.cpp already running at ${LLAMACPP_BASE_URL}"
    return
  fi
  if port_open "$LLAMACPP_HOST" "$LLAMACPP_PORT"; then
    die "port ${LLAMACPP_PORT} is in use but ${models_url} is not a llama.cpp server"
  fi
  command -v "$LLAMA_SERVER_BIN" >/dev/null 2>&1 || die \
    "llama-server binary not found: ${LLAMA_SERVER_BIN} (set LLAMA_SERVER_BIN)"

  local model_path="${LLAMACPP_MODEL_PATH:-}"
  if [[ -z "$model_path" ]]; then
    model_path="$(find "$ROOT/models" -maxdepth 1 -type f -name '*.gguf' -print -quit 2>/dev/null || true)"
  fi
  [[ -n "$model_path" ]] || die \
    "set LLAMACPP_MODEL_PATH to a .gguf file, or put one in $ROOT/models/"
  if [[ "$model_path" != /* ]]; then
    model_path="$ROOT/$model_path"
  fi
  [[ -f "$model_path" ]] || die "model file not found: $model_path"

  local -a llm_cmd=(
    "$LLAMA_SERVER_BIN"
    --model "$model_path"
    --host "$LLAMACPP_HOST"
    --port "$LLAMACPP_PORT"
    --alias "$LLAMACPP_MODEL"
  )
  if [[ -n "$LLAMACPP_ARGS" ]]; then
    local -a extra_args
    read -r -a extra_args <<< "$LLAMACPP_ARGS"
    llm_cmd+=("${extra_args[@]}")
  fi

  info "starting llama.cpp ($(basename "$model_path")) with ${LLAMA_SERVER_BIN}"
  "${llm_cmd[@]}" &
  local llm_pid=$!
  PIDS+=("$llm_pid")

  local wait_status=0
  if wait_for_pid "$llm_pid" 90 llm_ready; then
    info "llama.cpp is ready"
  else
    wait_status=$?
    if (( wait_status == 2 )); then
      die "llama-server exited before becoming ready; check the log above"
    fi
    warn "llama.cpp did not answer within 90s; the first query may wait for the model"
  fi
}

start_ollama() {
  if ollama list >/dev/null 2>&1; then
    info "Ollama already running at ${OLLAMA_BASE_URL}"
    return
  fi
  command -v ollama >/dev/null 2>&1 || die "ollama not found"
  info "starting Ollama at ${OLLAMA_BASE_URL}"
  ollama serve &
  local ollama_pid=$!
  PIDS+=("$ollama_pid")
  local wait_status=0
  if wait_for_pid "$ollama_pid" 30 ollama list >/dev/null 2>&1; then
    info "Ollama is ready"
  else
    wait_status=$?
    if (( wait_status == 2 )); then
      warn "ollama serve exited before becoming ready; check the log above"
    else
      warn "Ollama did not answer within 30s; run 'ollama serve' manually to debug"
    fi
  fi
  if ! ollama list 2>/dev/null | awk '{print $1}' | grep -qx "$OLLAMA_MODEL"; then
    warn "model ${OLLAMA_MODEL} is not pulled; run: ollama pull ${OLLAMA_MODEL}"
  fi
}

check_hosted_key() {
  case "$LLM_PROVIDER" in
    groq)
      [[ -n "${GROQ_API_KEY:-}" ]] || warn \
        "GROQ_API_KEY is empty; /query will fall back to deterministic answers"
      ;;
    gemini)
      [[ -n "${GEMINI_API_KEY:-}" ]] || warn \
        "GEMINI_API_KEY is empty; /query will fall back to deterministic answers"
      ;;
    openai)
      [[ -n "${OPENAI_API_KEY:-}" ]] || warn \
        "OPENAI_API_KEY is empty; /query will fall back to deterministic answers"
      ;;
  esac
}

info "GRAFT dev launcher (LLM_ENABLED=${LLM_ENABLED}, LLM_PROVIDER=${LLM_PROVIDER})"

if [[ "$LLM_ENABLED" == "true" ]]; then
  case "$LLM_PROVIDER" in
    llamacpp)
      { read -r LLAMACPP_HOST; read -r LLAMACPP_PORT; } <<< "$(url_host_port "$LLAMACPP_BASE_URL")"
      [[ -n "$LLAMACPP_HOST" && -n "$LLAMACPP_PORT" ]] || die \
        "could not read host/port from LLAMACPP_BASE_URL=${LLAMACPP_BASE_URL}"
      start_llamacpp
      ;;
    ollama)
      start_ollama
      ;;
    groq | gemini | openai)
      check_hosted_key
      ;;
    local)
      warn "LLM_PROVIDER=local needs an injected callable; no local server was started"
      ;;
    *)
      die "unknown LLM_PROVIDER: ${LLM_PROVIDER}"
      ;;
  esac
else
  info "LLM_ENABLED is false; skipping the local LLM"
fi

choose_api_port

if graft_ready; then
  info "API already running at http://${API_HOST}:${API_PORT}"
  warn "restart the API separately to pick up LLM_ENABLED/LLM_PROVIDER changes"
elif port_open "$API_HOST" "$API_PORT"; then
  die "port ${API_PORT} is occupied by a service that is not GRAFT (no /health)"
else
  info "starting API at http://${API_HOST}:${API_PORT}"
  RELOAD_ARGS=()
  if [[ "${API_RELOAD:-true}" == "true" ]]; then
    RELOAD_ARGS+=(--reload)
  fi
  "$PYTHON_BIN" -m uvicorn api.main:app \
    --host "$API_HOST" \
    --port "$API_PORT" \
    "${RELOAD_ARGS[@]}" &
  api_pid=$!
  PIDS+=("$api_pid")
  wait_status=0
  if wait_for_pid "$api_pid" 90 graft_ready; then
    info "API is ready"
  else
    wait_status=$?
    if (( wait_status == 2 )); then
      die "API exited before passing /health; check the log above"
    fi
    warn "API did not pass /health within 90s; check the log above"
  fi
fi

if frontend_ready; then
  info "frontend already running at http://localhost:${FRONTEND_PORT}"
elif frontend_port_open; then
  die "port ${FRONTEND_PORT} is occupied by an app that is not GRAFT"
else
  command -v npm >/dev/null 2>&1 || die "npm not found; install Node.js first"
  if [[ ! -x "$ROOT/frontend/node_modules/.bin/vite" ]]; then
    info "installing frontend dependencies (npm install)"
    (cd "$ROOT/frontend" && npm install)
  fi
  info "starting frontend at http://localhost:${FRONTEND_PORT}"
  (
    cd "$ROOT/frontend"
    GRAFT_API="http://${API_HOST}:${API_PORT}" \
      "$ROOT/frontend/node_modules/.bin/vite" --port "$FRONTEND_PORT"
  ) &
  PIDS+=("$!")
fi

info "frontend: http://localhost:${FRONTEND_PORT}"
info "API:      http://${API_HOST}:${API_PORT}/health"
if [[ "$LLM_ENABLED" == "true" ]]; then
  if [[ "$LLM_PROVIDER" == "llamacpp" ]]; then
    info "LLM:      llama.cpp at ${LLAMACPP_BASE_URL}"
  elif [[ "$LLM_PROVIDER" == "ollama" ]]; then
    info "LLM:      Ollama at ${OLLAMA_BASE_URL}"
  else
    info "LLM:      ${LLM_PROVIDER} (hosted)"
  fi
fi

if (( ${#PIDS[@]} == 0 )); then
  info "everything is already running"
  exit 0
fi

info "press Ctrl-C to stop services started by this script"
if (( BASH_VERSINFO[0] > 4 || (BASH_VERSINFO[0] == 4 && BASH_VERSINFO[1] >= 3) )); then
  wait -n "${PIDS[@]}" || true
else
  wait "${PIDS[0]}" || true
fi
