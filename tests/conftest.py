"""Shared pytest fixtures — embedding-model reuse (issue #69).

The sentence-transformers model costs several seconds to load. Several test
modules previously called :func:`embeddings.reset_model_cache` in stub-mode
fixtures, which evicted the cached model and forced the *next* real-model
test to pay a full reload — once per stub/real alternation.

This module instead:

- Loads the model **once per session** (``_warm_embedding_model_once``), so
  repeated :func:`embeddings._load_model` calls are cache hits.
- Transparently **preserves the cache** across tests that reset it
  (``_preserve_embedding_model_cache``): the reset still takes effect *within*
  the test, but a healthy cached model is restored afterwards so the eviction
  does not leak into unrelated tests.
- Provides a shared **``stub_provider``** fixture that forces the stub
  embedder *without* resetting the model cache.

No test is removed or skipped; behaviour under test is unchanged.
"""

from __future__ import annotations

import pytest

import embeddings as _emb_module
from config import settings
from embeddings import _load_model


@pytest.fixture(scope="session", autouse=True)
def _warm_embedding_model_once():
    """Load the sentence-transformers model once per test session.

    If the model is unavailable (offline CI) the load records the failure and
    returns ``None`` immediately on later calls, so this stays cheap there
    too. Real-model tests skip cleanly via their existing markers.
    """
    try:
        _load_model()
    except Exception:
        pass
    yield


@pytest.fixture(autouse=True)
def _preserve_embedding_model_cache():
    """Stop per-test ``reset_model_cache`` calls from evicting the session model.

    Snapshot the cache before each test; if the test cleared a healthy cached
    model, restore it afterwards. A test that never had a cached model (e.g.
    offline CI) is unaffected, and in-test reset semantics are preserved.
    """
    cached_model = _emb_module._model
    failed_flag = _emb_module._model_failed
    yield
    if cached_model is not None and _emb_module._model is None:
        _emb_module._model = cached_model
        _emb_module._model_failed = failed_flag


@pytest.fixture
def stub_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the deterministic stub embedder without touching the model cache.

    Setting ``settings.embedding_provider = "stub"`` is sufficient: the stub
    path never consults the cached model, so unlike the old per-module
    fixtures this does not evict it and does not force a reload later.
    """
    monkeypatch.setattr(settings, "embedding_provider", "stub")
