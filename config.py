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
    llm_provider: str = Field(default="gemini", description="gemini | openai | local")
    gemini_api_key: str | None = None
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"

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


settings = Settings()
