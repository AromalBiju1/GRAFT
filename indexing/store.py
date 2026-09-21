"""Persist embedded RAPTOR tree nodes through the vector-store wrapper."""

from typing import List

from indexing.tree_node import TreeNode
from indexing.vector_store import ChromaVectorStore


def persist_tree_nodes(nodes: List[TreeNode], vector_store: ChromaVectorStore) -> None:
    """Upsert nodes with hierarchy metadata and precomputed embeddings.

    Each node requires a non-empty ``metadata['document_id']`` as enforced
    by the vector store. Writes are sequential, not an atomic batch.
    """
    for node in nodes:
        if node.embedding is None:
            raise ValueError(f"Node {node.node_id} missing required embedding vector.")

        document_id = node.metadata.get("document_id", "")
        vector_store.insert(
            chunk_id=node.node_id,
            document_id=document_id,
            text=node.text,
            embedding=node.embedding,
            metadata={
                "node_id": node.node_id,
                "document_id": document_id,
                "level": node.level,
                "node_type": "leaf" if node.is_leaf else "summary",
                "parent_id": node.parent_id or "",
                "child_ids": ",".join(node.child_ids),
            },
        )
