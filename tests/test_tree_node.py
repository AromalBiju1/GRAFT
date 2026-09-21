"""Tests for the strict-tree indexing node contract."""

import unittest

from indexing.tree_node import TreeNode


class TreeNodeTests(unittest.TestCase):
    def test_level_zero_is_leaf(self) -> None:
        node = TreeNode(node_id="leaf", text="Chunk", level=0)

        self.assertTrue(node.is_leaf)
        node.child_ids.append("child")
        self.assertTrue(node.is_leaf)

    def test_node_without_children_is_leaf(self) -> None:
        node = TreeNode(node_id="summary", text="Summary", level=1)

        self.assertTrue(node.is_leaf)

    def test_parent_child_linking(self) -> None:
        parent = TreeNode(
            node_id="parent", text="Summary", level=1,
            child_ids=["child_1", "child_2"],
        )
        children = [
            TreeNode(node_id=node_id, text="Chunk", level=0, parent_id=parent.node_id)
            for node_id in parent.child_ids
        ]

        self.assertIsNone(parent.parent_id)
        self.assertFalse(parent.is_leaf)
        self.assertEqual(parent.child_ids, [child.node_id for child in children])
        for child in children:
            self.assertEqual(child.parent_id, parent.node_id)
            self.assertTrue(child.is_leaf)

    def test_to_dict_matches_serialization_contract(self) -> None:
        node = TreeNode(
            node_id="summary", text="Summary", level=1,
            embedding=[0.1, 0.2], parent_id="root", child_ids=["leaf"],
            metadata={"document_id": "doc_001", "page": 1},
        )

        self.assertEqual(node.to_dict(), {
            "node_id": "summary",
            "text": "Summary",
            "level": 1,
            "parent_id": "root",
            "child_ids": ["leaf"],
            "metadata": {"document_id": "doc_001", "page": 1},
        })
        self.assertNotIn("embedding", node.to_dict())

    def test_defaults_are_independent_between_nodes(self) -> None:
        first = TreeNode(node_id="first", text="First", level=0)
        second = TreeNode(node_id="second", text="Second", level=0)

        self.assertIsNone(first.embedding)
        self.assertIsNone(first.parent_id)
        first.child_ids.append("child")
        first.metadata["source"] = "example.pdf"

        self.assertEqual(second.child_ids, [])
        self.assertEqual(second.metadata, {})


if __name__ == "__main__":
    unittest.main()
