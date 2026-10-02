"""Persistent ChromaDB storage for precomputed GRAFT tree-node embeddings."""

from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any

import chromadb

DEFAULT_PERSIST_PATH = Path(".graft/chroma")
DEFAULT_COLLECTION_NAME = "graft_tree_nodes"

#: Process-wide store cache, keyed by (resolved persist path, collection).
#:
#: Opening a Chroma collection costs ~6 ms, and the query path was paying that on
#: *every* ``retrieve()`` and ``index_document()`` call — roughly a quarter of a
#: warm query's total latency spent reconnecting to a local database. A store is
#: stateless apart from its client and collection handle, so one instance per
#: (path, collection) is reused instead.
_SHARED_STORES: dict[tuple[str, str], ChromaVectorStore] = {}
_SHARED_LOCK = threading.Lock()


class ChromaVectorStore:
    """Store and search GRAFT tree nodes using precomputed embeddings.

    Records are upserted by ``chunk_id``. Reusing an ID replaces that record,
    which makes rebuilding an index safe and deterministic.
    """

    def __init__(
        self,
        persist_path: str | Path = DEFAULT_PERSIST_PATH,
        collection_name: str = DEFAULT_COLLECTION_NAME,
    ) -> None:
        if not str(collection_name).strip():
            raise ValueError("collection_name must not be empty")

        self.persist_path = Path(persist_path)
        self._closed = False
        self._client = chromadb.PersistentClient(path=str(self.persist_path))
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def insert(
        self,
        chunk_id: str,
        document_id: str,
        text: str,
        embedding: Sequence[float],
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Insert or replace one embedded node and its metadata.

        ``document_id`` is copied into Chroma metadata so query results match
        the Retrieval -> Router contract in ``docs/interfaces.md``.
        """
        record_id = self._validate_identifier(chunk_id, "chunk_id")
        source_document_id = self._validate_identifier(document_id, "document_id")
        vector = self._validate_embedding(embedding)
        if not isinstance(text, str):
            raise TypeError("text must be a string")

        record_metadata = dict(metadata or {})
        supplied_document_id = record_metadata.get("document_id")
        if supplied_document_id is not None and supplied_document_id != source_document_id:
            raise ValueError("metadata document_id must match document_id")
        record_metadata["document_id"] = source_document_id

        self._collection.upsert(
            ids=[record_id],
            documents=[text],
            embeddings=[vector],
            metadatas=[record_metadata],
        )

    def query(
        self,
        embedding: Sequence[float],
        n_results: int = 5,
        filters: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Return nearest nodes, optionally restricted by a Chroma filter."""
        vector = self._validate_embedding(embedding)
        if isinstance(n_results, bool) or not isinstance(n_results, int) or n_results < 1:
            raise ValueError("n_results must be a positive integer")
        if filters is not None and not isinstance(filters, Mapping):
            raise TypeError("filters must be a mapping or None")

        query_args: dict[str, Any] = {
            "query_embeddings": [vector],
            "n_results": n_results,
            "include": ["documents", "metadatas", "distances"],
        }
        if filters:
            query_args["where"] = dict(filters)

        response = self._collection.query(**query_args)
        ids = response.get("ids", [[]])[0]
        documents = (response.get("documents") or [[]])[0]
        metadatas = (response.get("metadatas") or [[]])[0]
        distances = (response.get("distances") or [[]])[0]

        results: list[dict[str, Any]] = []
        for chunk_id, document, metadata, distance in zip(
            ids, documents, metadatas, distances, strict=False
        ):
            numeric_distance = float(distance)
            results.append(
                {
                    "chunk_id": chunk_id,
                    "text": document,
                    "distance": numeric_distance,
                    "score": 1.0 - numeric_distance,
                    "metadata": metadata or {},
                }
            )
        return results

    def close(self) -> None:
        """Release ChromaDB resources held by this store instance."""
        if self._closed:
            return
        self._closed = True
        del self._collection
        self._client.close()

    @classmethod
    def shared(
        cls,
        persist_path: str | Path = DEFAULT_PERSIST_PATH,
        collection_name: str = DEFAULT_COLLECTION_NAME,
    ) -> ChromaVectorStore:
        """Return a cached store for ``(persist_path, collection_name)``.

        Prefer this on hot paths. A store handed out here must **not** be
        ``close()``d by the caller — it is shared with every other caller for
        the same key. If it is closed anyway, the next ``shared()`` call drops
        it from the cache and builds a fresh one, so a stray close degrades
        performance rather than causing a fault.

        Chroma's client and collection handles are safe to use concurrently,
        which matters because FastAPI runs these sync endpoints in a threadpool.
        """
        key = (str(Path(persist_path).resolve()), str(collection_name))
        with _SHARED_LOCK:
            store = _SHARED_STORES.get(key)
            if store is not None and not store._closed:
                return store
            store = cls(persist_path=persist_path, collection_name=collection_name)
            _SHARED_STORES[key] = store
            return store

    @classmethod
    def reset_shared_cache(cls) -> None:
        """Close and drop every cached store. Used by tests for isolation."""
        with _SHARED_LOCK:
            stores = list(_SHARED_STORES.values())
            _SHARED_STORES.clear()
        for store in stores:
            # Teardown is best effort: a store that fails to close must not
            # prevent the others from being released.
            with suppress(Exception):
                store.close()

    def __enter__(self) -> ChromaVectorStore:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @staticmethod
    def _validate_identifier(value: str, field_name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} must be a non-empty string")
        return value

    @staticmethod
    def _validate_embedding(embedding: Sequence[float]) -> list[float]:
        if isinstance(embedding, (str, bytes)) or not isinstance(embedding, Sequence):
            raise TypeError("embedding must be a sequence of numbers")
        if not embedding:
            raise ValueError("embedding must not be empty")
        if any(
            isinstance(value, bool) or not isinstance(value, (int, float))
            for value in embedding
        ):
            raise TypeError("embedding must contain only numbers")
        return [float(value) for value in embedding]
