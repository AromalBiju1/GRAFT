"""Central configuration loaded from environment / .env.

All tunables live here so thresholds and paths are not buried as magic
numbers inside modules (per copilot-instructions: magic numbers must be
named constants / config values).
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed settings with env-file support."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # LLM
    llm_provider: str = Field(
        default="gemini",
        description="gemini | openai | groq | ollama | llamacpp | local",
    )
    llm_enabled: bool = Field(default=False, description="enable LLM answer synthesis in the API")
    #: Per-request timeout for the HTTP-based providers (Groq, Ollama,
    #: llama.cpp). Local servers may need to load a model on first use.
    llm_timeout_seconds: float = Field(default=120.0, gt=0)
    #: Whole-prompt character budget. Retrieval hits are added until this is
    #: reached. Lower it for small-context local models; scripts/dev.sh sets
    #: 6000 for llama.cpp (whose server here runs with 2048 tokens).
    llm_max_prompt_chars: int = Field(default=16000, gt=0)
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"

    # Groq (OpenAI-compatible chat-completions endpoint)
    groq_api_key: str | None = None
    groq_model: str = "openai/gpt-oss-120b"
    groq_base_url: str = "https://api.groq.com/openai/v1"

    # Local OpenAI-compatible servers. Start Ollama with `ollama serve` or
    # llama.cpp with `llama-server`; neither requires an API key. The 120B model
    # is a Groq-only default; local models are whatever fits on the machine.
    ollama_base_url: str = "http://127.0.0.1:11434/v1"
    ollama_model: str = "llama3.2:3b"
    llamacpp_base_url: str = "http://127.0.0.1:8081/v1"
    #: Model name sent in the request. llama.cpp accepts any name, but the dev
    #: launcher starts `llama-server --alias graft-local` so they match.
    llamacpp_model: str = "graft-local"
    #: Path to the local .gguf file. Read by scripts/dev.sh, not by the API
    #: (the llama.cpp server loads the model itself).
    llamacpp_model_path: str | None = None

    # Embeddings
    embedding_provider: str = Field(default="sentence-transformers")
    embedding_model: str = "all-MiniLM-L6-v2"

    # Vector store
    chroma_path: Path = Field(default=Path(".graft/chroma"))
    chroma_collection: str = "graft_tree_nodes"

    # SQLite logger
    db_path: Path = Field(default=Path("db/graft_logs.db"))

    # API
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    api_reload: bool = True
    #: Comma-separated allowed CORS origins, e.g. "http://localhost:5173,https://graft.example.org".
    #: Empty or "*" permits every origin (demo/dev behaviour).
    #: Per the CORS spec, a "*" origin cannot be combined with credentialed
    #: responses, so credentials are only negotiated for an explicit origin list.
    cors_origins: str = ""
    #: Load the embedder and open the Chroma collection at startup instead of
    #: on the first query. Loading MiniLM costs ~0.4 s warm and up to ~17 s on
    #: a cold model cache; left lazy that lands on a random request. Tests turn
    #: this off, since they construct a client per test and would reload the
    #: model every time.
    warmup_on_startup: bool = True

    # Router thresholds
    default_retrieval_depth: int = 1
    router_threshold_simple: float = 0.35
    router_threshold_complex: float = 0.65

    # LLM judge (benchmark semantic answer-quality metric)
    judge_provider: str = Field(default="gemini", description="gemini | openai | local")
    judge_llm_provider: str = Field(default="gemini", description="alias for judge_provider")
    llm_judge_provider: str = Field(default="gemini", description="alias for judge_provider")
    evaluation_provider: str = Field(default="gemini", description="alias for judge_provider")
    judge_model: str = "gemini-2.5-flash"
    judge_llm_model: str = "gemini-2.5-flash"
    llm_judge_model: str = "gemini-2.5-flash"
    evaluation_model: str = "gemini-2.5-flash"
    judge_model_name: str = "gemini-2.5-flash"


settings = Settings()
