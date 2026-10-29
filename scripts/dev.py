#!/usr/bin/env python3
"""One-command development startup for GRAFT (Windows, macOS, Linux).

Starts:
  1. a local OpenAI-compatible LLM (llama.cpp by default), unless the selected
     provider is hosted (groq/gemini/openai)
  2. the FastAPI backend
  3. the Vite frontend

Services already listening on the configured ports are reused. That makes it
safe to run while an earlier copy is still up.

Usage:
  python scripts/dev.py
  ./scripts/dev.sh                         # macOS/Linux wrapper
  .\\scripts\\dev.ps1                        # Windows PowerShell wrapper
  scripts\\dev.bat                           # Windows cmd wrapper

Configuration is read from the environment and, when present, from .env.
The launcher defaults to LLM_ENABLED=true and LLM_PROVIDER=llamacpp so the
backend talks to the local server it starts on 127.0.0.1:8081.

Inline environment values win over .env, for example:
  LLM_PROVIDER=groq GROQ_API_KEY=... ./scripts/dev.sh
  LLAMACPP_MODEL_PATH=/path/to/model.gguf ./scripts/dev.sh
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = ROOT / "frontend"
IS_WINDOWS = os.name == "nt"


def info(message: str) -> None:
    print(f"[dev] {message}", flush=True)


def warn(message: str) -> None:
    print(f"[dev] warning: {message}", file=sys.stderr, flush=True)


def die(message: str) -> NoReturn:
    print(f"[dev] error: {message}", file=sys.stderr, flush=True)
    raise SystemExit(1)


def env(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value


def env_int(name: str, default: int) -> int:
    raw = env(name, str(default))
    try:
        return int(raw)
    except ValueError:
        die(f"{name} must be an integer, got {raw!r}")


def parse_bool(name: str, default: bool) -> bool:
    raw = env(name, "true" if default else "false").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    die(f"{name} must be true or false, got {raw!r}")


def parse_env_file_fallback(path: Path) -> dict[str, str]:
    """Parse a simple .env file when python-dotenv is unavailable."""
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def read_env_file(path: Path) -> dict[str, str]:
    try:
        from dotenv import dotenv_values
    except ImportError:
        return parse_env_file_fallback(path)

    values: dict[str, str] = {}
    for key, value in dotenv_values(path).items():
        if key and value is not None:
            values[key] = value
    return values


def load_dotenv(path: Path) -> None:
    """Load .env without overriding values already in the environment."""
    if not path.is_file():
        return
    for key, value in read_env_file(path).items():
        os.environ.setdefault(key, value)


def is_in_venv() -> bool:
    return sys.prefix != getattr(sys, "base_prefix", sys.prefix)


def find_python() -> str:
    explicit = os.environ.get("PYTHON")
    if explicit:
        candidate = Path(explicit).expanduser()
        if candidate.is_file():
            return str(candidate)
        found = shutil.which(explicit)
        if found:
            return found
        die(f"PYTHON does not point at an executable: {explicit}")

    virtual_env = os.environ.get("VIRTUAL_ENV")
    if virtual_env:
        candidate = Path(virtual_env) / (
            "Scripts/python.exe" if IS_WINDOWS else "bin/python"
        )
        if candidate.is_file():
            return str(candidate)

    if is_in_venv():
        return sys.executable

    candidates = [
        ROOT / "venv" / ("Scripts/python.exe" if IS_WINDOWS else "bin/python"),
        ROOT / ".venv" / ("Scripts/python.exe" if IS_WINDOWS else "bin/python"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)

    return sys.executable


def find_node() -> str:
    node = shutil.which("node")
    if not node:
        die("Node.js not found; install Node.js 18+ and reopen the terminal")
    return node


def find_npm() -> str:
    npm = shutil.which("npm")
    if not npm:
        die("npm not found; install Node.js and reopen the terminal")
    return npm


def npm_command(npm: str, args: list[str]) -> list[str]:
    if IS_WINDOWS:
        return [os.environ.get("COMSPEC", "cmd.exe"), "/c", npm, *args]
    return [npm, *args]


def connect_host(host: str) -> str:
    if host in {"", "0.0.0.0"}:
        return "127.0.0.1"
    if host == "::":
        return "::1"
    return host


def http_base(host: str, port: int) -> str:
    connect = connect_host(host)
    if ":" in connect and not connect.startswith("["):
        connect = f"[{connect}]"
    return f"http://{connect}:{port}"


def http_json(url: str, timeout: float = 2.0) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            data = json.load(response)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def url_host_port(url: str) -> tuple[str, int]:
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return host, port


def llm_ready(base_url: str) -> bool:
    data = http_json(f"{base_url.rstrip('/')}/models", timeout=1.5)
    if not isinstance(data, dict):
        return False
    # llama.cpp returns {"models": [...]} while Ollama and the OpenAI schema
    # return {"object": "list", "data": [...]}. Accept both.
    return isinstance(data.get("data"), list) or isinstance(data.get("models"), list)


def is_graft_health(data: dict | None) -> bool:
    # status alone is not enough: an unrelated app on the same machine may also
    # expose /health. embedding_dim is part of GRAFT's health payload.
    return bool(data and data.get("status") == "ok" and "embedding_dim" in data)


def graft_ready_on(host: str, port: int) -> bool:
    data = http_json(f"{http_base(host, port)}/health", timeout=1.5)
    return is_graft_health(data)


def frontend_ready(port: int) -> bool:
    for host in ("127.0.0.1", "[::1]", "localhost"):
        try:
            with urllib.request.urlopen(f"http://{host}:{port}/", timeout=1.5) as response:
                page = response.read(8192).decode("utf-8", "ignore")
        except Exception:
            continue
        if "GRAFT" in page:
            return True
    return False


def frontend_proxied(port: int) -> bool:
    for host in ("127.0.0.1", "localhost", "[::1]"):
        data = http_json(f"http://{host}:{port}/health", timeout=2.0)
        if is_graft_health(data):
            return True
    return False


def frontend_port_open(port: int) -> bool:
    return port_open("127.0.0.1", port) or port_open("::1", port)


def wait_until(
    predicate: Callable[[], bool],
    timeout_seconds: int,
    proc: subprocess.Popen | None = None,
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        if proc is not None and proc.poll() is not None:
            return False
        time.sleep(1)
    return False


@dataclass
class Running:
    name: str
    proc: subprocess.Popen


LAUNCHED: list[Running] = []


def start_process(
    name: str,
    cmd: list[str],
    cwd: Path,
    env_vars: dict[str, str] | None = None,
) -> Running:
    kwargs: dict = {}
    if IS_WINDOWS:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(cmd, cwd=str(cwd), env=env_vars, **kwargs)
    running = Running(name=name, proc=proc)
    LAUNCHED.append(running)
    return running


def stop_process(running: Running) -> None:
    proc = running.proc
    if proc.poll() is not None:
        return

    if IS_WINDOWS:
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=5)
        return

    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=5)
        return
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=2)


def cleanup() -> None:
    if not LAUNCHED:
        return
    info("stopping services started by this script")
    for running in reversed(LAUNCHED):
        try:
            stop_process(running)
        except Exception as exc:
            warn(f"could not stop {running.name}: {exc}")
    LAUNCHED.clear()


def raise_keyboard_interrupt(signum: int, frame: object) -> None:
    raise KeyboardInterrupt


def split_args(value: str) -> list[str]:
    if not value.strip():
        return []
    try:
        if IS_WINDOWS:
            tokens = shlex.split(value, posix=False)
            return [
                token[1:-1]
                if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'"
                else token
                for token in tokens
            ]
        return shlex.split(value)
    except ValueError as exc:
        die(f"could not parse LLAMACPP_ARGS: {exc}")


def resolve_binary(value: str) -> str:
    if not value:
        die("LLAMA_SERVER_BIN is empty")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    if path.is_file():
        return str(path)
    found = shutil.which(value)
    if found:
        return found
    die(f"llama-server binary not found: {value} (set LLAMA_SERVER_BIN)")


def resolve_model_path(value: str) -> Path:
    if value:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = ROOT / path
        if path.is_file():
            return path
        die(f"model file not found: {path}")

    models_dir = ROOT / "models"
    candidates = sorted(models_dir.glob("*.gguf")) if models_dir.is_dir() else []
    if not candidates:
        die(f"set LLAMACPP_MODEL_PATH to a .gguf file, or put one in {models_dir}")
    return candidates[0]


@dataclass
class Config:
    api_host: str
    api_port: int
    frontend_port: int
    llm_enabled: bool
    llm_provider: str
    llamacpp_base_url: str
    ollama_base_url: str
    ollama_model: str
    llamacpp_model: str
    llama_server_bin: str
    llamacpp_args: str
    api_reload: bool
    max_prompt_chars: int

    @classmethod
    def from_env(cls) -> Config:
        provider = env("LLM_PROVIDER", "llamacpp").strip().lower()
        provider = {
            "llama.cpp": "llamacpp",
            "llama-cpp": "llamacpp",
            "llama_cpp": "llamacpp",
        }.get(provider, provider)
        local_provider = provider in {"llamacpp", "ollama"}
        # A 2048-token llama.cpp context needs headroom for the answer. PDF text
        # can tokenize at roughly 2.5-3 characters per token, so 4000 characters
        # leaves several hundred tokens for generation.
        default_prompt_chars = 4000 if local_provider else 16000

        return cls(
            api_host=env("API_HOST", "127.0.0.1"),
            api_port=env_int("API_PORT", 8001),
            frontend_port=env_int("FRONTEND_PORT", 5173),
            llm_enabled=parse_bool("LLM_ENABLED", True),
            llm_provider=provider,
            llamacpp_base_url=env("LLAMACPP_BASE_URL", "http://127.0.0.1:8081/v1"),
            ollama_base_url=env("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1"),
            ollama_model=env("OLLAMA_MODEL", "llama3.2:3b"),
            llamacpp_model=env("LLAMACPP_MODEL", "graft-local"),
            llama_server_bin=env("LLAMA_SERVER_BIN", "llama-server"),
            llamacpp_args=env("LLAMACPP_ARGS", ""),
            api_reload=parse_bool("API_RELOAD", True),
            max_prompt_chars=env_int("LLM_MAX_PROMPT_CHARS", default_prompt_chars),
        )


def apply_env(config: Config) -> None:
    os.environ["API_HOST"] = config.api_host
    os.environ["API_PORT"] = str(config.api_port)
    os.environ["LLM_ENABLED"] = "true" if config.llm_enabled else "false"
    os.environ["LLM_PROVIDER"] = config.llm_provider
    os.environ["LLM_MAX_PROMPT_CHARS"] = str(config.max_prompt_chars)
    os.environ["LLAMACPP_BASE_URL"] = config.llamacpp_base_url
    os.environ["LLAMACPP_MODEL"] = config.llamacpp_model
    os.environ["OLLAMA_BASE_URL"] = config.ollama_base_url
    os.environ["OLLAMA_MODEL"] = config.ollama_model


def choose_api_port(config: Config) -> None:
    if graft_ready_on(config.api_host, config.api_port):
        return
    if not port_open(connect_host(config.api_host), config.api_port):
        return

    warn(f"port {config.api_port} is occupied by a service that is not GRAFT")
    for candidate in range(8001, 8006):
        if candidate == config.api_port:
            continue
        if graft_ready_on(config.api_host, candidate):
            info(f"found GRAFT API on port {candidate}; reusing it")
            config.api_port = candidate
            os.environ["API_PORT"] = str(candidate)
            return
        if not port_open(connect_host(config.api_host), candidate):
            info(f"using port {candidate} for the API instead")
            config.api_port = candidate
            os.environ["API_PORT"] = str(candidate)
            return
    die(f"no free API port found (tried {config.api_port} and 8001-8005)")


def check_ollama_model(ollama: str | None, config: Config) -> None:
    if not ollama:
        return
    try:
        result = subprocess.run(
            [ollama, "list"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception:
        return
    if result.returncode != 0:
        return
    installed = {
        line.split()[0]
        for line in result.stdout.splitlines()[1:]
        if line.split()
    }
    if config.ollama_model not in installed:
        warn(
            f"model {config.ollama_model} is not pulled; "
            f"run: ollama pull {config.ollama_model}"
        )


def start_llamacpp(config: Config) -> None:
    if llm_ready(config.llamacpp_base_url):
        info(f"llama.cpp already running at {config.llamacpp_base_url}")
        return

    host, port = url_host_port(config.llamacpp_base_url)
    if port_open(connect_host(host), port):
        die(
            f"port {port} is in use but "
            f"{config.llamacpp_base_url.rstrip('/')}/models is not a llama.cpp server"
        )

    binary = resolve_binary(config.llama_server_bin)
    model_path = resolve_model_path(env("LLAMACPP_MODEL_PATH", ""))
    cmd = [
        binary,
        "--model",
        str(model_path),
        "--host",
        host,
        "--port",
        str(port),
        "--alias",
        config.llamacpp_model,
    ]
    cmd.extend(split_args(config.llamacpp_args))

    info(f"starting llama.cpp ({model_path.name}) with {binary}")
    running = start_process("llama.cpp", cmd, ROOT)
    if wait_until(lambda: llm_ready(config.llamacpp_base_url), 90, running.proc):
        info("llama.cpp is ready")
    elif running.proc.poll() is not None:
        die("llama-server exited before becoming ready; check the log above")
    else:
        warn("llama.cpp did not answer within 90s; the first query may wait for the model")


def start_ollama(config: Config) -> None:
    ollama = shutil.which("ollama")
    if llm_ready(config.ollama_base_url):
        info(f"Ollama already running at {config.ollama_base_url}")
        check_ollama_model(ollama, config)
        return

    if not ollama:
        die("ollama not found; install Ollama or use LLM_PROVIDER=llamacpp")
    host, port = url_host_port(config.ollama_base_url)
    if port_open(connect_host(host), port):
        die(
            f"port {port} is in use but "
            f"{config.ollama_base_url.rstrip('/')}/models is not an Ollama server"
        )

    info(f"starting Ollama at {config.ollama_base_url}")
    running = start_process("Ollama", [ollama, "serve"], ROOT)
    if wait_until(lambda: llm_ready(config.ollama_base_url), 30, running.proc):
        info("Ollama is ready")
    elif running.proc.poll() is not None:
        warn("ollama serve exited before becoming ready; check the log above")
    else:
        warn("Ollama did not answer within 30s; run 'ollama serve' manually to debug")
    check_ollama_model(ollama, config)


def check_hosted_key(config: Config) -> None:
    key_names = {
        "groq": "GROQ_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "openai": "OPENAI_API_KEY",
    }
    key_name = key_names[config.llm_provider]
    if not os.environ.get(key_name, "").strip():
        warn(f"{key_name} is empty; /query will fall back to deterministic answers")


def start_api(config: Config, python_exe: str) -> None:
    if graft_ready_on(config.api_host, config.api_port):
        info(f"API already running at {http_base(config.api_host, config.api_port)}")
        warn("restart the API separately to pick up LLM_ENABLED/LLM_PROVIDER changes")
        return
    if port_open(connect_host(config.api_host), config.api_port):
        die(
            f"port {config.api_port} is occupied by a service that is not GRAFT "
            "(no /health)"
        )

    info(f"starting API at {http_base(config.api_host, config.api_port)}")
    cmd = [
        python_exe,
        "-m",
        "uvicorn",
        "api.main:app",
        "--host",
        config.api_host,
        "--port",
        str(config.api_port),
    ]
    if config.api_reload:
        cmd.append("--reload")
    running = start_process("API", cmd, ROOT)
    if wait_until(
        lambda: graft_ready_on(config.api_host, config.api_port),
        90,
        running.proc,
    ):
        info("API is ready")
    elif running.proc.poll() is not None:
        die("API exited before passing /health; check the log above")
    else:
        warn("API did not pass /health within 90s; check the log above")


def start_frontend(config: Config) -> None:
    if frontend_ready(config.frontend_port):
        if frontend_proxied(config.frontend_port):
            info(
                f"frontend already running at http://localhost:{config.frontend_port} "
                "(wired to the API)"
            )
            return
        die(
            f"frontend on port {config.frontend_port} is not proxying /health to the API; "
            "stop it and rerun"
        )
    if frontend_port_open(config.frontend_port):
        die(f"port {config.frontend_port} is occupied by an app that is not GRAFT")

    node = find_node()
    vite_js = FRONTEND_DIR / "node_modules" / "vite" / "bin" / "vite.js"
    if not vite_js.is_file():
        npm = find_npm()
        info("installing frontend dependencies (npm install)")
        try:
            subprocess.run(
                npm_command(npm, ["install"]),
                cwd=str(FRONTEND_DIR),
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            die(f"npm install failed with exit code {exc.returncode}")
        if not vite_js.is_file():
            die("frontend dependencies are missing after npm install")

    info(f"starting frontend at http://localhost:{config.frontend_port}")
    frontend_env = os.environ.copy()
    frontend_env["GRAFT_API"] = http_base(config.api_host, config.api_port)
    running = start_process(
        "frontend",
        [
            node,
            str(vite_js),
            "--port",
            str(config.frontend_port),
            "--strictPort",
        ],
        FRONTEND_DIR,
        env_vars=frontend_env,
    )
    if wait_until(
        lambda: frontend_proxied(config.frontend_port),
        30,
        running.proc,
    ):
        info("frontend is ready and proxying to the API")
    elif running.proc.poll() is not None:
        die("frontend exited before proxying /health; check the log above")
    else:
        warn("frontend started but /health is not proxying to the API within 30s")


def parse_args(argv: list[str]) -> int | None:
    if not argv:
        return None
    if argv[0] in {"-h", "--help"}:
        print((__doc__ or "").strip())
        return 0
    die(f"unknown argument: {argv[0]}")


def main() -> int:
    help_status = parse_args(sys.argv[1:])
    if help_status is not None:
        return help_status

    os.chdir(ROOT)
    load_dotenv(ROOT / ".env")
    config = Config.from_env()
    apply_env(config)
    python_exe = find_python()

    if IS_WINDOWS:
        for signal_name in ("SIGTERM", "SIGBREAK"):
            if hasattr(signal, signal_name):
                signal.signal(getattr(signal, signal_name), raise_keyboard_interrupt)
    else:
        for signal_name in ("SIGTERM", "SIGHUP"):
            if hasattr(signal, signal_name):
                signal.signal(getattr(signal, signal_name), raise_keyboard_interrupt)

    try:
        info(
            "GRAFT dev launcher "
            f"(LLM_ENABLED={'true' if config.llm_enabled else 'false'}, "
            f"LLM_PROVIDER={config.llm_provider})"
        )

        if config.llm_enabled:
            if config.llm_provider == "llamacpp":
                start_llamacpp(config)
            elif config.llm_provider == "ollama":
                start_ollama(config)
            elif config.llm_provider in {"groq", "gemini", "openai"}:
                check_hosted_key(config)
            elif config.llm_provider == "local":
                warn(
                    "LLM_PROVIDER=local needs an injected callable; "
                    "no local server was started"
                )
            else:
                die(f"unknown LLM_PROVIDER: {config.llm_provider}")
        else:
            info("LLM_ENABLED is false; skipping the local LLM")

        choose_api_port(config)
        start_api(config, python_exe)
        start_frontend(config)

        info(f"frontend: http://localhost:{config.frontend_port}")
        info(f"API:      {http_base(config.api_host, config.api_port)}/health")
        if config.llm_enabled:
            if config.llm_provider == "llamacpp":
                info(f"LLM:      llama.cpp at {config.llamacpp_base_url}")
            elif config.llm_provider == "ollama":
                info(f"LLM:      Ollama at {config.ollama_base_url}")
            else:
                info(f"LLM:      {config.llm_provider} (hosted)")

        if not LAUNCHED:
            info("everything is already running")
            return 0

        info("press Ctrl-C to stop services started by this script")
        while True:
            for running in LAUNCHED:
                code = running.proc.poll()
                if code is not None:
                    warn(f"{running.name} exited with code {code}")
                    return code or 1
            time.sleep(0.5)
    except KeyboardInterrupt:
        return 0
    finally:
        cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
