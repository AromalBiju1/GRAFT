"""Pydantic schemas for the API — aligned with docs/interfaces.md sections 9–10."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1, description="User query")
    request_id: str | None = Field(default=None, description="Optional client-supplied request_id")
    retrieval_depth: int | None = Field(default=None, ge=0, description="Override router depth")
    filters: dict[str, Any] | None = None


class EvidenceItem(BaseModel):
    source: str | None = None
    page: int | None = None
    chunk_id: str | None = None
    node_id: str | None = None
    level: int | None = None
    score: float | None = None
    text: str | None = None


class QueryResponse(BaseModel):
    request_id: str
    answer: str
    evidence: list[EvidenceItem] = Field(default_factory=list)
    routing: dict[str, Any] | None = None
    latency_ms: float | None = None


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
    chroma_path: str
    collection: str
    #: Which embedder is actually in use, and its dimension. A ``stub`` here
    #: means the real model failed to load and retrieval is running on hash
    #: vectors, so answers are not semantically meaningful.
    embedding_provider: str = "stub"
    embedding_dim: int = 0


class ErrorResponse(BaseModel):
    request_id: str | None = None
    error: dict[str, str]
