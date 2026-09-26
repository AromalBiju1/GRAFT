"""Focused integration tests for the ChromaDB vector-store wrapper."""

import tempfile
import unittest

from indexing.vector_store import ChromaVectorStore


class ChromaVectorStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.store = ChromaVectorStore(persist_path=self.temp_directory.name)

    def tearDown(self) -> None:
        self.store.close()
        self.temp_directory.cleanup()

    def test_insert_and_query_returns_expected_node_and_metadata(self) -> None:
        self.store.insert(
            chunk_id="chunk_001",
            document_id="doc_001",
            text="Example leaf chunk",
            embedding=[1.0, 0.0, 0.0],
            metadata={
                "node_id": "node_001",
                "level": 0,
                "source": "example.pdf",
                "page": 1,
                "node_type": "leaf",
            },
        )

        results = self.store.query([1.0, 0.0, 0.0])

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["chunk_id"], "chunk_001")
        self.assertEqual(results[0]["text"], "Example leaf chunk")
        self.assertAlmostEqual(results[0]["distance"], 0.0)
        self.assertAlmostEqual(results[0]["score"], 1.0)
        self.assertEqual(results[0]["metadata"]["document_id"], "doc_001")
        self.assertEqual(results[0]["metadata"]["node_id"], "node_001")
        self.assertEqual(results[0]["metadata"]["source"], "example.pdf")
        self.assertEqual(results[0]["metadata"]["page"], 1)

    def test_query_applies_metadata_filter(self) -> None:
        self.store.insert(
            "chunk_leaf",
            "doc_001",
            "Leaf node",
            [1.0, 0.0, 0.0],
            {"node_id": "node_leaf", "level": 0},
        )
        self.store.insert(
            "chunk_summary",
            "doc_001",
            "Summary node",
            [0.9, 0.1, 0.0],
            {"node_id": "node_summary", "level": 1},
        )

        results = self.store.query(
            [1.0, 0.0, 0.0], n_results=5, filters={"level": 1}
        )

        self.assertEqual([result["chunk_id"] for result in results], ["chunk_summary"])
        self.assertTrue(all(result["metadata"]["level"] == 1 for result in results))

    def test_upsert_replaces_an_existing_chunk(self) -> None:
        self.store.insert("chunk_001", "doc_001", "Old", [1.0, 0.0], {"level": 0})
        self.store.insert("chunk_001", "doc_001", "New", [1.0, 0.0], {"level": 1})

        results = self.store.query([1.0, 0.0])

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["text"], "New")
        self.assertEqual(results[0]["metadata"]["level"], 1)

    def test_rejects_invalid_arguments(self) -> None:
        with self.assertRaises(ValueError):
            self.store.insert("", "doc_001", "Text", [1.0], {})
        with self.assertRaises(ValueError):
            self.store.insert("chunk_001", "doc_001", "Text", [], {})
        with self.assertRaises(ValueError):
            self.store.query([1.0], n_results=0)


if __name__ == "__main__":
    unittest.main()
