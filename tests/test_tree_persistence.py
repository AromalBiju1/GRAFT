"""Integration tests for persisting tree nodes in an isolated ChromaDB."""

import tempfile
import time
import unittest
from unittest.mock import Mock

from indexing.store import persist_tree_nodes
from indexing.tree_node import TreeNode
from indexing.vector_store import ChromaVectorStore


class TreePersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.store = ChromaVectorStore(persist_path=self.temp_directory.name)

    def tearDown(self) -> None:
        self.store.close()
        # Chroma may release SQLite handles asynchronously on Windows.
        for attempt in range(20):
            try:
                self.temp_directory.cleanup()
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.05)

    def make_tree(self) -> list[TreeNode]:
        return [
            TreeNode(
                node_id="leaf_1", text="First chunk", level=0,
                embedding=[1.0, 0.0], parent_id="summary",
                metadata={"document_id": "doc_001", "nested": {"page": 1}},
            ),
            TreeNode(
                node_id="leaf_2", text="Second chunk", level=0,
                embedding=[0.9, 0.1], parent_id="summary",
                metadata={"document_id": "doc_001"},
            ),
            TreeNode(
                node_id="summary", text="Summary text", level=1,
                embedding=[0.8, 0.2], child_ids=["leaf_1", "leaf_2"],
                metadata={"document_id": "doc_001"},
            ),
        ]

    def test_persists_leaf_and_summary_metadata(self) -> None:
        nodes = self.make_tree()
        persist_tree_nodes(nodes, self.store)

        results = self.store.query([1.0, 0.0], n_results=10)
        self.assertEqual(len(results), 3)
        records = {result["chunk_id"]: result for result in results}
        self.assertEqual(set(records), {"leaf_1", "leaf_2", "summary"})
        for node in nodes:
            record = records[node.node_id]
            self.assertEqual(record["text"], node.text)
            self.assertEqual(record["metadata"], {
                "node_id": node.node_id,
                "document_id": "doc_001",
                "level": node.level,
                "node_type": "summary" if node.node_id == "summary" else "leaf",
                "parent_id": "" if node.node_id == "summary" else "summary",
                "child_ids": "leaf_1,leaf_2" if node.node_id == "summary" else "",
            })
            self.assertIsInstance(record["metadata"]["level"], int)
        self.assertEqual(nodes[0].metadata["nested"], {"page": 1})

    def test_filters_by_level(self) -> None:
        persist_tree_nodes(self.make_tree(), self.store)

        for level, expected in [(0, {"leaf_1", "leaf_2"}), (1, {"summary"})]:
            with self.subTest(level=level):
                results = self.store.query([1.0, 0.0], filters={"level": level})
                self.assertEqual({result["chunk_id"] for result in results}, expected)
                self.assertTrue(all(result["metadata"]["level"] == level for result in results))

    def test_repeated_node_id_updates_without_duplicates(self) -> None:
        node = self.make_tree()[0]
        persist_tree_nodes([node], self.store)
        replacement = TreeNode(
            node_id=node.node_id, text="Updated summary", level=1,
            embedding=[0.0, 1.0], child_ids=["child"],
            metadata={"document_id": "doc_002"},
        )
        persist_tree_nodes([replacement], self.store)

        results = self.store.query([0.0, 1.0], n_results=10)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["chunk_id"], node.node_id)
        self.assertEqual(results[0]["text"], "Updated summary")
        self.assertAlmostEqual(results[0]["distance"], 0.0)
        self.assertEqual(results[0]["metadata"], {
            "node_id": node.node_id, "document_id": "doc_002", "level": 1,
            "node_type": "summary", "parent_id": "", "child_ids": "child",
        })

    def test_invalid_document_id_preserves_wrapper_validation(self) -> None:
        for metadata in [{}, {"document_id": ""}, {"document_id": "   "}]:
            with self.subTest(metadata=metadata):
                node = TreeNode(
                    node_id="leaf", text="Chunk", level=0,
                    embedding=[1.0, 0.0], metadata=metadata,
                )
                with self.assertRaisesRegex(ValueError, "document_id must be a non-empty string"):
                    persist_tree_nodes([node], self.store)

    def test_higher_level_node_without_children_uses_is_leaf(self) -> None:
        node = TreeNode(
            node_id="unlinked", text="Text", level=1, embedding=[1.0, 0.0],
            metadata={"document_id": "doc_001"},
        )
        persist_tree_nodes([node], self.store)

        result = self.store.query([1.0, 0.0])[0]
        self.assertEqual(result["metadata"]["node_type"], "leaf")
        self.assertEqual(result["metadata"]["level"], 1)


class TreePersistenceValidationTests(unittest.TestCase):
    def test_missing_embedding_raises_with_node_id(self) -> None:
        store = Mock(spec=ChromaVectorStore)
        node = TreeNode(node_id="missing_vector", text="Chunk", level=0)

        with self.assertRaisesRegex(
            ValueError, r"^Node missing_vector missing required embedding vector\.$"
        ):
            persist_tree_nodes([node], store)
        store.insert.assert_not_called()


if __name__ == "__main__":
    unittest.main()
