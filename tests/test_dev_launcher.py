"""Pure-function tests for the cross-platform dev launcher.

The launcher itself is tested manually against live services; these tests cover
the parsing and URL helpers that are easy to regress while keeping Windows and
Unix behaviour aligned.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("graft_dev", ROOT / "scripts" / "dev.py")
assert _SPEC is not None and _SPEC.loader is not None
dev = importlib.util.module_from_spec(_SPEC)
sys.modules["graft_dev"] = dev
_SPEC.loader.exec_module(dev)


def test_parse_env_fallback_keeps_windows_paths_and_quotes(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "# comment",
                r"LLAMACPP_MODEL_PATH=C:\models\model.gguf",
                'LLAMACPP_ARGS="-ngl 99 --jinja --ctx-size 2048"',
                "export API_PORT=8001",
            ]
        ),
        encoding="utf-8",
    )

    values = dev.parse_env_file_fallback(env_file)

    assert values["LLAMACPP_MODEL_PATH"] == r"C:\models\model.gguf"
    assert values["LLAMACPP_ARGS"] == "-ngl 99 --jinja --ctx-size 2048"
    assert values["API_PORT"] == "8001"


def test_load_dotenv_keeps_inline_values(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("LLM_PROVIDER=llamacpp\nGROQ_API_KEY=from-file\n", encoding="utf-8")
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    dev.load_dotenv(env_file)

    assert dev.os.environ["LLM_PROVIDER"] == "groq"
    assert dev.os.environ["GROQ_API_KEY"] == "from-file"


def test_config_defaults_for_local_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "API_HOST",
        "API_PORT",
        "FRONTEND_PORT",
        "LLM_ENABLED",
        "LLM_PROVIDER",
        "LLM_MAX_PROMPT_CHARS",
        "API_RELOAD",
        "LLAMACPP_BASE_URL",
        "LLAMACPP_MODEL",
        "LLAMA_SERVER_BIN",
        "LLAMACPP_ARGS",
        "OLLAMA_BASE_URL",
        "OLLAMA_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "llama.cpp")

    config = dev.Config.from_env()

    assert config.llm_provider == "llamacpp"
    assert config.llm_enabled is True
    assert config.api_port == 8001
    assert config.frontend_port == 5173
    assert config.max_prompt_chars == 4000
    assert config.api_reload is True


def test_config_hosted_provider_gets_larger_prompt_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("LLM_PROVIDER", "LLM_MAX_PROMPT_CHARS", "LLM_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "groq")

    config = dev.Config.from_env()

    assert config.llm_provider == "groq"
    assert config.max_prompt_chars == 16000


def test_split_args_handles_windows_style_quotes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dev, "IS_WINDOWS", True)

    assert dev.split_args(r'-ngl 99 --foo "C:\a b\c"') == [
        "-ngl",
        "99",
        "--foo",
        r"C:\a b\c",
    ]


def test_npm_command_wraps_a_windows_cmd_shim(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dev, "IS_WINDOWS", True)
    monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")

    assert dev.npm_command(r"C:\Program Files\nodejs\npm.cmd", ["install"]) == [
        r"C:\Windows\System32\cmd.exe",
        "/c",
        r"C:\Program Files\nodejs\npm.cmd",
        "install",
    ]


def test_find_python_uses_an_activated_venv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_python = tmp_path / "Scripts" / "python.exe"
    fake_python.parent.mkdir()
    fake_python.write_text("", encoding="utf-8")
    monkeypatch.setattr(dev, "IS_WINDOWS", True)
    monkeypatch.delenv("PYTHON", raising=False)
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path))

    assert dev.find_python() == str(fake_python)


def test_http_base_maps_wildcard_and_ipv6() -> None:
    assert dev.http_base("0.0.0.0", 8001) == "http://127.0.0.1:8001"
    assert dev.http_base("::", 8001) == "http://[::1]:8001"
    assert dev.http_base("::1", 8001) == "http://[::1]:8001"
    assert dev.http_base("127.0.0.1", 8001) == "http://127.0.0.1:8001"


def test_is_graft_health_needs_more_than_status() -> None:
    assert dev.is_graft_health({"status": "ok"}) is False
    assert dev.is_graft_health({"status": "ok", "embedding_dim": 384}) is True
    assert dev.is_graft_health(None) is False


def test_resolve_model_path_accepts_an_explicit_file(tmp_path: Path) -> None:
    model = tmp_path / "model.gguf"
    model.write_bytes(b"not a real model")

    assert dev.resolve_model_path(str(model)) == model


def test_env_int_rejects_a_non_integer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_PORT", "not-a-port")

    with pytest.raises(SystemExit):
        dev.env_int("API_PORT", 8001)
