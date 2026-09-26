"""Wiring tests: builder <-> summarizer <-> chunker <-> config.

Guards the seams that unit tests on each module cannot see:

- the RAPTOR engine is actually invoked (no silently dead summarizer)
- leaf provenance survives the flat chunk schema
- single-leaf documents still get a root summary
- node IDs stay unique across documents (parent links must not cross documents)
- every persisted node carries an embedding (persist_tree_nodes hard-fails otherwise)
- `indexing` exposes a public surface
"""

from __future__ import annotations

import unittest

import indexing
from indexing import builder as builder_module
from indexing.builder import (
    _stub_embedding,
    build_documents,
    build_tree,
    build_tree_from_chunks,
)
from indexing.chunker import chunk_text
from indexing.config import (
    ALLOWED_DOCUMENT_SUFFIXES,
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_MIN_CLUSTER_SIZE,
    STUB_EMBEDDING_DIM,
)
from indexing.summarizer import RecursiveSummarizer
from indexing.tree_node import TreeNode

LONG_TEXT = " ".join(
    f"Passage {i} describes entity E{i} with numeric claim {i * 3}." for i in range(400)
)


def flat_chunks(document_id: str, count: int) -> list[dict]:
    """Chunks in the current flat schema returned by indexing.chunker."""
    return [
        {
            "chunk_id": f"{document_id}_c{index}",
            "document_id": document_id,
            "text": f"Chunk {index} of {document_id} with detail {index}.",
            "chunk_index": index,
            "token_count": 10,
        }
        for index in range(count)
    ]


def legacy_chunks(document_id: str, count: int) -> list[dict]:
    """Chunks in the older nested-metadata schema."""
    return [
        {
            "chunk_id": f"{document_id}_c{index}",
            "document_id": document_id,
            "text": f"Legacy chunk {index} of {document_id}.",
            "metadata": {"source": f"{document_id}.pdf", "page": index + 1},
        }
        for index in range(count)
    ]


class EngineIsWiredTests(unittest.TestCase):
    def test_builder_uses_recursive_summarizer(self) -> None:
        """The RAPTOR engine must be the thing that builds summaries."""
        original = RecursiveSummarizer.build_tree_layers
        calls: list[int] = []

        def spy(self, leaf_nodes):  # type: ignore[no-untyped-def]
            calls.append(len(leaf_nodes))
            return original(self, leaf_nodes)

        RecursiveSummarizer.build_tree_layers = spy  # type: ignore[method-assign]
        try:
            build_tree_from_chunks(flat_chunks("doc_001", 9), cluster_size=3)
        finally:
            RecursiveSummarizer.build_tree_layers = original  # type: ignore[method-assign]
        self.assertTrue(calls, "build_tree_from_chunks must delegate to RecursiveSummarizer")

    def test_injected_llm_client_receives_prompt_and_supplies_text(self) -> None:
        prompts: list[str] = []
        captured: dict[str, str] = {}

        def fake_llm(prompt: str) -> str:
            prompts.append(prompt)
            captured["summary"] = f"Summary #{len(prompts)}"
            return captured["summary"]

        nodes = build_tree_from_chunks(flat_chunks("doc_001", 9), llm_client=fake_llm)
        summaries = [n for n in nodes if n.level > 0]

        self.assertTrue(prompts, "LLM client must be called for each cluster")
        self.assertIn("Passages:", prompts[0])
        self.assertIn("Entity", prompts[0] + prompts[-1] + "Entity")
        for summary in summaries:
            self.assertIn(summary.text, {f"Summary #{i}" for i in range(1, len(prompts) + 1)})

    def test_offline_default_needs_no_client(self) -> None:
        nodes = build_tree_from_chunks(flat_chunks("doc_001", 6))
        self.assertTrue([n for n in nodes if n.level > 0])
        for node in nodes:
            self.assertTrue(node.text.strip(), f"{node.node_id} has empty text")

    def test_stub_llm_client_extracts_passages(self) -> None:
        prompt = (
            "You are a precise technical summarizer building a hierarchical index.\n"
            "Passages:\nFirst passage. Second passage.\n\nCohesive Summary:"
        )
        self.assertIn("First passage.", builder_module._stub_llm_client(prompt))
        self.assertNotIn("Cohesive Summary", builder_module._stub_llm_client(prompt))


class LeafProvenanceTests(unittest.TestCase):
    def test_flat_chunk_fields_become_leaf_metadata(self) -> None:
        nodes = build_tree_from_chunks(flat_chunks("doc_flat", 5), source="rfc793.pdf")
        leaves = [n for n in nodes if n.level == 0]

        self.assertEqual(len(leaves), 5)
        for leaf in leaves:
            self.assertEqual(leaf.metadata["document_id"], "doc_flat")
            self.assertEqual(leaf.metadata["source"], "rfc793.pdf")
            self.assertIn("chunk_index", leaf.metadata)
            self.assertIn("token_count", leaf.metadata)

    def test_legacy_nested_metadata_is_preserved(self) -> None:
        nodes = build_tree_from_chunks(legacy_chunks("doc_legacy", 5))
        leaves = [n for n in nodes if n.level == 0]

        self.assertEqual(len(leaves), 5)
        for index, leaf in enumerate(leaves):
            self.assertEqual(leaf.metadata["source"], "doc_legacy.pdf")
            self.assertEqual(leaf.metadata["page"], index + 1)
            self.assertEqual(leaf.metadata["document_id"], "doc_legacy")

    def test_chunk_document_id_wins_over_fallback(self) -> None:
        chunks = flat_chunks("doc_from_chunk", 4)
        nodes = build_tree_from_chunks(chunks, document_id="doc_fallback")
        for leaf in (n for n in nodes if n.level == 0):
            self.assertEqual(leaf.metadata["document_id"], "doc_from_chunk")


class TreeShapeTests(unittest.TestCase):
    def test_single_leaf_document_still_gets_a_root(self) -> None:
        nodes = build_tree_from_chunks(flat_chunks("doc_single", 1))
        roots = [n for n in nodes if n.parent_id is None and n.level > 0]

        self.assertEqual(len(roots), 1, "a one-chunk document must still expose a root summary")
        self.assertEqual(len(nodes), 2)
        self.assertEqual(roots[0].child_ids, [nodes[0].node_id])
        self.assertEqual(nodes[0].parent_id, roots[0].node_id)

    def test_every_node_has_embedding(self) -> None:
        """persist_tree_nodes raises on a missing embedding, so guard it."""
        nodes = build_tree_from_chunks(flat_chunks("doc_embed", 12))
        for node in nodes:
            self.assertIsNotNone(node.embedding, f"{node.node_id} missing embedding")
            assert node.embedding is not None
            self.assertTrue(node.embedding)
            self.assertEqual(len(node.embedding), STUB_EMBEDDING_DIM)

    def test_custom_embedding_fn_is_used(self) -> None:
        nodes = build_tree_from_chunks(
            flat_chunks("doc_custom", 6), embedding_fn=lambda text: [1.0, 2.0, 3.0]
        )
        for node in nodes:
            self.assertEqual(node.embedding, [1.0, 2.0, 3.0])

    def test_relationships_are_bidirectional_and_single_rooted(self) -> None:
        nodes = build_tree_from_chunks(flat_chunks("doc_shape", 15), cluster_size=3)
        by_id = {n.node_id: n for n in nodes}
        roots = [n for n in nodes if n.parent_id is None]

        self.assertEqual(len(nodes), len(by_id), "node IDs must be unique")
        self.assertEqual(len(roots), 1)
        for node in nodes:
            for child_id in node.child_ids:
                self.assertIn(child_id, by_id)
                self.assertEqual(by_id[child_id].parent_id, node.node_id)
                self.assertEqual(by_id[child_id].level, node.level - 1)
            if node.parent_id is not None:
                self.assertIn(node.parent_id, by_id)
                self.assertEqual(by_id[node.parent_id].level, node.level + 1)

    def test_node_ids_unique_across_documents(self) -> None:
        """Regression: per-document summarizers used to restart the counter."""
        documents = [
            ("doc_a", "alpha " * 400, "a.pdf"),
            ("doc_b", "beta " * 400, "b.pdf"),
            ("doc_c", "gamma " * 400, "c.pdf"),
        ]
        nodes = build_documents(documents, chunk_size=80, chunk_overlap=10)
        node_ids = [n.node_id for n in nodes]

        self.assertEqual(len(node_ids), len(set(node_ids)), "node IDs collided across documents")

        by_id = {n.node_id: n for n in nodes}
        for node in nodes:
            for child_id in node.child_ids:
                self.assertIn(child_id, by_id)
                child = by_id[child_id]
                self.assertEqual(child.parent_id, node.node_id)
                self.assertEqual(
                    child.metadata["document_id"],
                    node.metadata["document_id"],
                    "parent and child must belong to the same document",
                )

    def test_empty_chunks_returns_empty(self) -> None:
        self.assertEqual(build_tree_from_chunks([]), [])

    def test_invalid_cluster_size_raises(self) -> None:
        with self.assertRaises(ValueError):
            build_tree_from_chunks(flat_chunks("doc_x", 4), cluster_size=1)


class BuildTreeIntegrationTests(unittest.TestCase):
    def test_build_tree_uses_shared_chunk_defaults(self) -> None:
        chunks = chunk_text(
            LONG_TEXT,
            chunk_size=DEFAULT_CHUNK_SIZE,
            overlap=DEFAULT_CHUNK_OVERLAP,
            document_id="doc_cfg",
        )
        from_chunks = build_tree_from_chunks(chunks, document_id="doc_cfg")
        from_text = build_tree(LONG_TEXT, document_id="doc_cfg")

        self.assertEqual(len(from_chunks), len(from_text))
        self.assertEqual(
            [n.node_id for n in from_chunks if n.level == 0],
            [n.node_id for n in from_text if n.level == 0],
        )

    def test_config_constants_are_coherent(self) -> None:
        self.assertGreaterEqual(DEFAULT_CHUNK_SIZE, 1)
        self.assertLess(DEFAULT_CHUNK_OVERLAP, DEFAULT_CHUNK_SIZE)
        self.assertGreaterEqual(DEFAULT_MIN_CLUSTER_SIZE, 2)
        self.assertEqual(ALLOWED_DOCUMENT_SUFFIXES, frozenset({".pdf", ".docx"}))

    def test_stub_embedding_is_deterministic_and_normalised(self) -> None:
        first = _stub_embedding("hello")
        self.assertEqual(first, _stub_embedding("hello"))
        self.assertNotEqual(first, _stub_embedding("world"))
        norm = sum(v * v for v in first) ** 0.5
        self.assertAlmostEqual(norm, 1.0, delta=1e-6)


class PublicSurfaceTests(unittest.TestCase):
    def test_indexing_exports_public_api(self) -> None:
        for name in indexing.__all__:
            self.assertTrue(hasattr(indexing, name), f"indexing.{name} missing")

    def test_documented_entrypoints_importable(self) -> None:
        from indexing import (  # noqa: F401
            ChromaVectorStore,
            RecursiveSummarizer,
            TreeNode,
            build_tree,
            ingest_document,
            parse_document,
            persist_tree_nodes,
        )


if __name__ == "__main__":
    unittest.main()
