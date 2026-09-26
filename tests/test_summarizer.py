"""Unit tests for the recursive RAPTOR summarization engine.

Uses mocked LLM calls — no network or model required.
"""

import unittest
from typing import List
from unittest.mock import MagicMock

from indexing.prompts import SUMMARIZATION_PROMPT, format_summarization_prompt
from indexing.summarizer import RecursiveSummarizer, build_raptor_tree
from indexing.tree_node import TreeNode


def make_llm(responses: List[str] | None = None):
    """Return a recording callable LLM returning canned summaries."""
    calls: List[str] = []
    canned = list(responses) if responses else []

    def _llm(prompt: str) -> str:
        calls.append(prompt)
        if canned:
            return canned.pop(0)
        # Default: echo cluster size for traceability.
        return f"Mock summary for prompt of {len(prompt)} chars."

    _llm.calls = calls  # type: ignore[attr-defined]
    return _llm


def make_leaves(n: int, dim: int = 4, with_embeddings: bool = True) -> List[TreeNode]:
    leaves: List[TreeNode] = []
    for i in range(n):
        embedding = [float(i + 1 + j * 0.1) for j in range(dim)] if with_embeddings else None
        leaves.append(
            TreeNode(
                node_id=f"leaf_{i:04d}",
                text=f"Leaf passage {i} with factual detail entity E{i} and value {i * 7}.",
                level=0,
                embedding=embedding,
                metadata={"document_id": "doc_001", "source": "sample.pdf"},
            )
        )
    return leaves


def make_separable_leaves() -> List[TreeNode]:
    """Six leaves in two well-separated embedding groups."""
    group_a = [[1.0, 0.0], [1.1, 0.1], [0.9, -0.1]]
    group_b = [[0.0, 1.0], [0.1, 1.1], [-0.1, 0.9]]
    leaves: List[TreeNode] = []
    for i, emb in enumerate(group_a + group_b):
        leaves.append(
            TreeNode(
                node_id=f"leaf_{i:04d}",
                text=f"Passage {i} group {'A' if i < 3 else 'B'}.",
                level=0,
                embedding=[float(v) for v in emb],
                metadata={"document_id": "doc_001"},
            )
        )
    return leaves


class PromptTemplateTests(unittest.TestCase):
    def test_prompt_contains_required_sections(self) -> None:
        self.assertIn("{concatenated_texts}", SUMMARIZATION_PROMPT)
        self.assertIn("precise technical summarizer", SUMMARIZATION_PROMPT)
        self.assertIn("Retain critical factual details", SUMMARIZATION_PROMPT)
        self.assertIn("Cohesive Summary:", SUMMARIZATION_PROMPT)

    def test_format_helper(self) -> None:
        rendered = format_summarization_prompt("Passage one.\n\nPassage two.")
        self.assertIn("Passage one.", rendered)
        self.assertIn("Passage two.", rendered)
        self.assertNotIn("{concatenated_texts}", rendered)


class SummarizerInitTests(unittest.TestCase):
    def test_rejects_invalid_args(self) -> None:
        llm = make_llm()
        with self.assertRaises(TypeError):
            RecursiveSummarizer(None)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            RecursiveSummarizer(llm, max_summary_tokens=0)
        with self.assertRaises(ValueError):
            RecursiveSummarizer(llm, min_cluster_size=1)
        with self.assertRaises(ValueError):
            RecursiveSummarizer(llm, cluster_method="dbscan")  # type: ignore[arg-type]

    def test_accepts_object_with_generate(self) -> None:
        class Client:
            def generate(self, prompt: str) -> str:
                return "summary"

        summarizer = RecursiveSummarizer(Client())
        self.assertEqual(summarizer.cluster_method, "gmm")


class SummarizeClusterTests(unittest.TestCase):
    def test_creates_summary_with_bidirectional_links(self) -> None:
        llm = make_llm(["Cohesive summary text."])
        summarizer = RecursiveSummarizer(llm)
        children = make_leaves(3)

        summary = summarizer.summarize_cluster(children, level=1)

        self.assertEqual(summary.level, 1)
        self.assertEqual(summary.text, "Cohesive summary text.")
        self.assertIsNone(summary.parent_id)
        self.assertEqual(summary.child_ids, [c.node_id for c in children])
        for child in children:
            self.assertEqual(child.parent_id, summary.node_id)
        self.assertEqual(summary.metadata["document_id"], "doc_001")
        self.assertEqual(summary.metadata["child_count"], 3)
        # Prompt must contain every child text.
        prompt = llm.calls[0]  # type: ignore[attr-defined]
        for child in children:
            self.assertIn(child.text, prompt)

    def test_mean_embedding_and_truncation(self) -> None:
        llm = make_llm(["word " * 500])
        summarizer = RecursiveSummarizer(llm, max_summary_tokens=10)
        children = [
            TreeNode(node_id="a", text="t1", level=0, embedding=[1.0, 0.0], metadata={"document_id": "d"}),
            TreeNode(node_id="b", text="t2", level=0, embedding=[3.0, 2.0], metadata={"document_id": "d"}),
        ]
        summary = summarizer.summarize_cluster(children, level=1)
        self.assertEqual(len(summary.text.split()), 10)
        self.assertEqual(summary.embedding, [2.0, 1.0])

    def test_rejects_empty_cluster_and_bad_level(self) -> None:
        summarizer = RecursiveSummarizer(make_llm())
        with self.assertRaises(ValueError):
            summarizer.summarize_cluster([], level=1)
        with self.assertRaises(ValueError):
            summarizer.summarize_cluster(make_leaves(2), level=0)

    def test_empty_llm_response_raises(self) -> None:
        summarizer = RecursiveSummarizer(make_llm(["   "]))
        with self.assertRaises(ValueError):
            summarizer.summarize_cluster(make_leaves(2), level=1)

    def test_non_string_llm_result_raises(self) -> None:
        summarizer = RecursiveSummarizer(lambda prompt: 12345)  # type: ignore[return-value]
        with self.assertRaises(TypeError):
            summarizer.summarize_cluster(make_leaves(2), level=1)

    def test_dict_llm_result_coerced(self) -> None:
        summarizer = RecursiveSummarizer(lambda prompt: {"summary": "Dict summary."})
        summary = summarizer.summarize_cluster(make_leaves(2), level=1)
        self.assertEqual(summary.text, "Dict summary.")


class BuildTreeLayersTests(unittest.TestCase):
    def test_empty_and_singleton(self) -> None:
        summarizer = RecursiveSummarizer(make_llm())
        self.assertEqual(summarizer.build_tree_layers([]), [])
        single = make_leaves(1)
        result = summarizer.build_tree_layers(single)
        self.assertEqual(result, single)
        self.assertIsNone(result[0].parent_id)

    def test_small_layer_collapses_to_single_root(self) -> None:
        summarizer = RecursiveSummarizer(make_llm(), min_cluster_size=3)
        leaves = make_leaves(3)
        nodes = summarizer.build_tree_layers(leaves)
        # 3 leaves + 1 root.
        self.assertEqual(len(nodes), 4)
        roots = [n for n in nodes if n.parent_id is None]
        self.assertEqual(len(roots), 1)
        root = roots[0]
        self.assertEqual(root.level, 1)
        self.assertEqual(set(root.child_ids), {leaf.node_id for leaf in leaves})

    def test_recursive_build_with_sequential_fallback(self) -> None:
        # No embeddings -> sequential clustering path.
        summarizer = RecursiveSummarizer(make_llm(), min_cluster_size=3)
        leaves = make_leaves(10, with_embeddings=False)
        nodes = summarizer.build_tree_layers(leaves)

        by_id = {n.node_id: n for n in nodes}
        # Leaves preserved.
        self.assertEqual(len([n for n in nodes if n.level == 0]), 10)
        # Root exists with no parent.
        max_level = max(n.level for n in nodes)
        self.assertGreater(max_level, 1)
        roots = [n for n in nodes if n.level == max_level]
        self.assertEqual(len(roots), 1)
        self.assertIsNone(roots[0].parent_id)
        # Bidirectional links hold everywhere.
        for node in nodes:
            for child_id in node.child_ids:
                self.assertIn(child_id, by_id)
                self.assertEqual(by_id[child_id].parent_id, node.node_id)
                self.assertEqual(by_id[child_id].level, node.level - 1)
            if node.parent_id is not None:
                self.assertIn(node.parent_id, by_id)

    def test_gmm_and_kmeans_paths(self) -> None:
        for method in ("gmm", "kmeans"):
            with self.subTest(method=method):
                summarizer = RecursiveSummarizer(
                    make_llm(), min_cluster_size=3, cluster_method=method  # type: ignore[arg-type]
                )
                leaves = make_separable_leaves()
                nodes = summarizer.build_tree_layers(leaves)
                roots = [n for n in nodes if n.parent_id is None]
                self.assertEqual(len(roots), 1)
                # 6 leaves -> 2 clusters -> 2 summaries -> 1 root = 9 nodes.
                self.assertEqual(len([n for n in nodes if n.level == 0]), 6)
                self.assertEqual(len([n for n in nodes if n.level == 1]), 2)
                self.assertEqual(len([n for n in nodes if n.level == 2]), 1)

    def test_raptor_alias_and_module_function(self) -> None:
        summarizer = RecursiveSummarizer(make_llm(), min_cluster_size=3)
        leaves = make_leaves(4)
        via_layers = summarizer.build_tree_layers(list(leaves))
        leaves2 = make_leaves(4)
        summarizer2 = RecursiveSummarizer(make_llm(), min_cluster_size=3)
        via_alias = summarizer2.build_raptor_tree(leaves2)
        self.assertEqual(len(via_layers), len(via_alias))

        leaves3 = make_leaves(4)
        nodes = build_raptor_tree(leaves3, make_llm(), min_cluster_size=3)
        self.assertEqual(len([n for n in nodes if n.parent_id is None]), 1)

    def test_llm_called_once_per_cluster(self) -> None:
        llm = make_llm()
        summarizer = RecursiveSummarizer(llm, min_cluster_size=3)
        leaves = make_leaves(7, with_embeddings=False)
        nodes = summarizer.build_tree_layers(leaves)
        summaries = [n for n in nodes if n.level > 0]
        self.assertEqual(len(llm.calls), len(summaries))  # type: ignore[attr-defined]

    def test_mocked_llm_content_used(self) -> None:
        llm = make_llm(["ROOT SUMMARY"])
        summarizer = RecursiveSummarizer(llm, min_cluster_size=3)
        leaves = make_leaves(2, with_embeddings=False)
        nodes = summarizer.build_tree_layers(leaves)
        root = next(n for n in nodes if n.parent_id is None and n.level > 0)
        self.assertEqual(root.text, "ROOT SUMMARY")


if __name__ == "__main__":
    unittest.main()
