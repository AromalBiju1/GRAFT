"""Tests for the batch paths added to indexing: batch embedding and batch persist.

Two optimisations are covered here:

* ``indexing.builder._embed_many`` — encodes a whole document in one embedder
  call when the embedder exposes ``embed_texts``, instead of one forward pass
  per chunk.
* ``ChromaVectorStore.insert_many`` and ``indexing.store.persist_tree_nodes`` —
  one Chroma round trip for a whole tree, with every record validated before
  anything is written.
"""

from __future__ import annotations

import tempfile
import unittest

from indexing.builder import _embed_many, build_tree_from_chunks
from indexing.store import persist_tree_nodes
from indexing.tree_node import TreeNode
from vector_store import ChromaVectorStore


def _chunk(index: int, text: str = "some passage text") -> dict:
    return {
        "chunk_id": f"doc_001_chunk_{index:04d}",
        "document_id": "doc_001",
        "text": text,
        "chunk_index": index,
    }


class EmbedManyTests(unittest.TestCase):
    """The batch path must be used when available and skipped when not."""

    def test_uses_the_batch_api_when_the_embedder_provides_one(self) -> None:
        calls: list[list[str]] = []

        def batch(texts: list[str]) -> list[list[float]]:
            calls.append(list(texts))
            return [[1.0, 0.0] for _ in texts]

        def single(text: str) -> list[float]:  # pragma: no cover - must not run
            raise AssertionError("per-text fallback used when a batch API exists")

        embedder = single
        embedder.embed_texts = batch  # type: ignore[attr-defined]

        vectors = _embed_many(embedder, ["a", "b", "c"])

        self.assertEqual(calls, [["a", "b", "c"]])
        self.assertEqual(len(vectors), 3)

    def test_falls_back_to_per_text_for_a_plain_callable(self) -> None:
        seen: list[str] = []

        def embed(text: str) -> list[float]:
            seen.append(text)
            return [float(len(text)), 0.0]

        vectors = _embed_many(embed, ["aa", "bbb"])

        self.assertEqual(seen, ["aa", "bbb"])
        self.assertEqual(vectors, [[2.0, 0.0], [3.0, 0.0]])

    def test_falls_back_when_the_batch_call_raises(self) -> None:
        def batch(_texts: list[str]) -> list[list[float]]:
            raise RuntimeError("batch backend unavailable")

        def single(text: str) -> list[float]:
            return [float(len(text)), 0.0]

        single.embed_texts = batch  # type: ignore[attr-defined]

        vectors = _embed_many(single, ["a", "bb"])

        self.assertEqual(vectors, [[1.0, 0.0], [2.0, 0.0]])

    def test_empty_input_returns_empty(self) -> None:
        self.assertEqual(_embed_many(lambda t: [1.0], []), [])


class BuilderBatchEmbeddingTests(unittest.TestCase):
    """The builder must embed every chunk through the batch path."""

    def test_build_tree_embeds_all_chunks_in_one_call(self) -> None:
        calls: list[int] = []

        def batch(texts: list[str]) -> list[list[float]]:
            calls.append(len(texts))
            return [[1.0, 0.0, 0.0] for _ in texts]

        def single(_text: str) -> list[float]:  # pragma: no cover
            raise AssertionError("builder used the per-chunk path")

        single.embed_texts = batch  # type: ignore[attr-defined]

        nodes = build_tree_from_chunks(
            [_chunk(i) for i in range(7)], embedding_fn=single, cluster_size=3
        )

        self.assertEqual(calls, [7], "expected exactly one batched encode call")
        leaves = [n for n in nodes if n.level == 0]
        self.assertEqual(len(leaves), 7)
        for leaf in leaves:
            self.assertEqual(leaf.embedding, [1.0, 0.0, 0.0])

    def test_build_tree_still_works_with_a_plain_callable(self) -> None:
        nodes = build_tree_from_chunks(
            [_chunk(i) for i in range(4)],
            embedding_fn=lambda t: [float(len(t)), 1.0, 0.0],
            cluster_size=2,
        )
        self.assertTrue(all(n.embedding for n in nodes))


class InsertManyTests(unittest.TestCase):
    """Batch upsert must match single insert, and must be all-or-nothing."""

    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.store = ChromaVectorStore(persist_path=self.temp_directory.name)

    def tearDown(self) -> None:
        self.store.close()
        self.temp_directory.cleanup()

    @staticmethod
    def _record(index: int) -> dict:
        return {
            "chunk_id": f"chunk_{index:03d}",
            "document_id": "doc_001",
            "text": f"passage {index}",
            "embedding": [float(index), 0.0, 0.0],
            "metadata": {"level": 0},
        }

    def test_inserts_every_record(self) -> None:
        self.store.insert_many([self._record(i) for i in range(5)])

        results = self.store.query([0.0, 0.0, 0.0], n_results=10)

        self.assertEqual(len(results), 5)
        self.assertEqual(
            {r["chunk_id"] for r in results},
            {f"chunk_{i:03d}" for i in range(5)},
        )

    def test_document_id_lands_in_metadata(self) -> None:
        self.store.insert_many([self._record(0)])

        result = self.store.query([0.0, 0.0, 0.0], n_results=1)[0]

        self.assertEqual(result["metadata"]["document_id"], "doc_001")
        self.assertEqual(result["metadata"]["level"], 0)

    def test_upsert_replaces_an_existing_chunk(self) -> None:
        self.store.insert_many([self._record(0)])
        self.store.insert_many([self._record(0)])

        self.assertEqual(len(self.store.query([0.0, 0.0, 0.0], n_results=10)), 1)

    def test_empty_batch_is_a_no_op(self) -> None:
        self.store.insert_many([])
        self.assertEqual(self.store.query([0.0, 0.0, 0.0], n_results=5), [])

    def test_a_bad_record_aborts_the_whole_batch(self) -> None:
        """Nothing is written when any record is invalid."""
        good = self._record(0)
        bad = self._record(1)
        bad["embedding"] = []

        with self.assertRaises(ValueError):
            self.store.insert_many([good, bad])

        self.assertEqual(
            self.store.query([0.0, 0.0, 0.0], n_results=10),
            [],
            "a rejected batch must leave the collection untouched",
        )


class PersistTreeNodesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.store = ChromaVectorStore(persist_path=self.temp_directory.name)

    def tearDown(self) -> None:
        self.store.close()
        self.temp_directory.cleanup()

    @staticmethod
    def _nodes(count: int) -> list[TreeNode]:
        return [
            TreeNode(
                node_id=f"node_{i:03d}",
                text=f"text {i}",
                level=0,
                embedding=[float(i), 0.0, 0.0],
                metadata={"document_id": "doc_001"},
            )
            for i in range(count)
        ]

    def test_persists_the_whole_tree(self) -> None:
        persist_tree_nodes(self._nodes(6), self.store)

        results = self.store.query([0.0, 0.0, 0.0], n_results=10)

        self.assertEqual(len(results), 6)
        self.assertTrue(all(r["metadata"]["node_type"] == "leaf" for r in results))

    def test_missing_embedding_raises_and_writes_nothing(self) -> None:
        nodes = self._nodes(2)
        nodes[1].embedding = None

        with self.assertRaises(ValueError):
            persist_tree_nodes(nodes, self.store)

        self.assertEqual(self.store.query([0.0, 0.0, 0.0], n_results=10), [])

    def test_writes_the_whole_tree_in_one_batch(self) -> None:
        """persist_tree_nodes must make one vector-store call, not one per node."""
        calls: list[int] = []

        class CountingStore(ChromaVectorStore):
            def insert_many(self, records):  # type: ignore[override]
                calls.append(len(records))
                super().insert_many(records)

            def insert(self, *args, **kwargs):  # type: ignore[override]
                raise AssertionError("persist_tree_nodes wrote node-by-node")

        store = CountingStore(persist_path=self.temp_directory.name)
        try:
            persist_tree_nodes(self._nodes(5), store)
        finally:
            store.close()

        self.assertEqual(calls, [5], "expected exactly one batched write")

    def test_summary_nodes_are_labelled(self) -> None:
        parent = TreeNode(
            node_id="root",
            text="summary",
            level=2,
            embedding=[9.0, 0.0, 0.0],
            child_ids=["node_000"],
            metadata={"document_id": "doc_001"},
        )
        persist_tree_nodes(self._nodes(1) + [parent], self.store)

        results = self.store.query([0.0, 0.0, 0.0], n_results=10)
        summary = next(r for r in results if r["chunk_id"] == "root")

        self.assertEqual(summary["metadata"]["node_type"], "summary")
        self.assertEqual(summary["metadata"]["child_ids"], "node_000")


if __name__ == "__main__":
    unittest.main()
