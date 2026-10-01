"""Text embeddings for GRAFT — real model with a deterministic stub fallback.

Both ends of the pipeline must agree on vector dimensionality or Chroma
rejects the insert / the distance maths is meaningless. The dimension is
therefore resolved once here (:func:`embedding_dim`) and used by *both* the
indexing side and the query side.

Providers (``settings.embedding_provider``):

``"sentence-transformers"``
    ``settings.embedding_model`` (default ``all-MiniLM-L6-v2``, 384-dim).
``"stub"``
    Deterministic SHA-256 hash embedding, so CI and air-gapped machines stay
    runnable. Semantically meaningless, but deterministic and dependency-free.

The stub is also used as a *fallback* when the real model cannot be loaded
(import error, no network, corrupt cache). :func:`active_provider` reports
which one is actually in use so callers and tests can assert on it.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from typing import Any

from config import settings

logger = logging.getLogger(__name__)

#: Dimension of the deterministic offline stub embedding.
STUB_EMBEDDING_DIM = 16


_model: Any | None = None
_model_failed = False
_lock = threading.Lock()


def _stub_embedding(text: str, dim: int = STUB_EMBEDDING_DIM) -> list[float]:
    """Deterministic hash-based embedding (unit-test friendly, no semantics)."""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    vals: list[float] = [(digest[i % len(digest)] / 127.5) - 1.0 for i in range(dim)]
    norm = sum(v * v for v in vals) ** 0.5 or 1.0
    return [v / norm for v in vals]


def _load_model() -> Any | None:
    """Load and cache the sentence-transformers model, or return None."""
    global _model, _model_failed
    if _model is not None:
        return _model
    if _model_failed:
        return None
    with _lock:
        if _model is not None:
            return _model
        if _model_failed:
            return None
        try:
            from sentence_transformers import SentenceTransformer

            _model = SentenceTransformer(settings.embedding_model)
        except Exception as exc:  # pragma: no cover - environment dependent
            logger.warning(
                "Falling back to stub embeddings: could not load %r (%s: %s)",
                settings.embedding_model,
                type(exc).__name__,
                exc,
            )
            _model_failed = True
            return None
    return _model


def reset_model_cache() -> None:
    """Drop the cached model. Used by tests to exercise the stub path."""
    global _model, _model_failed
    with _lock:
        _model = None
        _model_failed = False


def active_provider() -> str:
    """Return the provider actually in use: ``"stub"`` or ``"sentence-transformers"``."""
    if settings.embedding_provider != "sentence-transformers":
        return "stub"
    return "sentence-transformers" if _load_model() is not None else "stub"


def embedding_dim() -> int:
    """Resolve the vector dimension used by *both* indexing and querying."""
    if active_provider() == "stub":
        return STUB_EMBEDDING_DIM
    model = _load_model()
    if model is None:  # pragma: no cover - active_provider already handled this
        return STUB_EMBEDDING_DIM
    # Renamed in sentence-transformers 5.x; support both spellings.
    for attr in ("get_embedding_dimension", "get_sentence_embedding_dimension"):
        getter = getattr(model, attr, None)
        if getter is not None:
            try:
                return int(getter())
            except Exception:  # pragma: no cover - defensive
                continue
    return STUB_EMBEDDING_DIM  # pragma: no cover - defensive


def embed_text(text: str) -> list[float]:
    """Embed a single string, falling back to the stub on any model failure."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")

    if active_provider() == "sentence-transformers":
        model = _load_model()
        if model is not None:
            try:
                return [float(v) for v in model.encode(text).tolist()]
            except Exception as exc:  # pragma: no cover - environment dependent
                logger.warning(
                    "encode() failed, using stub embedding (%s: %s)", type(exc).__name__, exc
                )
    return _stub_embedding(text)


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of strings. Uses the model's batch API when available."""
    if not texts:
        return []
    if active_provider() == "sentence-transformers":
        model = _load_model()
        if model is not None:
            try:
                vectors = model.encode(texts)
                return [[float(v) for v in row] for row in vectors]
            except Exception as exc:  # pragma: no cover - environment dependent
                logger.warning(
                    "batch encode() failed, using stub embeddings (%s: %s)", type(exc).__name__, exc
                )
    return [embed_text(t) for t in texts]


def collection_name(base: str | None = None) -> str:
    """Chroma collection name qualified by the active embedder.

    Chroma fixes an embedding dimension per collection, so a collection built
    with 16-dim hash vectors cannot accept 384-dim MiniLM vectors -- writing to
    the same name after switching embedders raises
    ``InvalidArgumentError: Collection expecting embedding with dimension ...``.
    Qualifying the name means switching embedder (or model) starts a fresh
    collection instead of crashing, and indexing and querying always agree on
    which one they mean.

    An explicit ``collection_name=`` argument still wins wherever one is passed.
    """
    from config import settings

    base = base or settings.chroma_collection
    if active_provider() == "stub":
        return f"{base}_stub_{STUB_EMBEDDING_DIM}"
    slug = re.sub(r"[^a-z0-9]+", "-", settings.embedding_model.lower()).strip("-")[:40]
    return f"{base}_{slug}_{embedding_dim()}"


__all__ = [
    "STUB_EMBEDDING_DIM",
    "active_provider",
    "collection_name",
    "embed_text",
    "embed_texts",
    "embedding_dim",
    "reset_model_cache",
]
