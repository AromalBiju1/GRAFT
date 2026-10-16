"""Persist embedded RAPTOR tree nodes through the vector-store wrapper."""


from indexing.tree_node import TreeNode
from indexing.vector_store import ChromaVectorStore


def persist_tree_nodes(nodes: list[TreeNode], vector_store: ChromaVectorStore) -> None:
    """Upsert nodes with hierarchy metadata and precomputed embeddings.

    Each node requires a non-empty ``metadata['document_id']`` as enforced
    by the vector store.

    Nodes are written in one batch, so the whole tree costs a single Chroma
    round trip instead of one per node. Every node is validated before anything
    is written: a bad node anywhere in the list aborts the call with nothing
    persisted. This is stricter than a per-node loop, where earlier nodes would
    already be committed by the time a later one failed.
    """
    records = []
    for node in nodes:
        if node.embedding is None:
            raise ValueError(f"Node {node.node_id} missing required embedding vector.")

        document_id = node.metadata.get("document_id", "")
        records.append(
            {
                "chunk_id": node.node_id,
                "document_id": document_id,
                "text": node.text,
                "embedding": node.embedding,
                "metadata": {
                    "node_id": node.node_id,
                    "document_id": document_id,
                    "level": node.level,
                    "node_type": "leaf" if node.is_leaf else "summary",
                    "parent_id": node.parent_id or "",
                    "child_ids": ",".join(node.child_ids),
                },
            }
        )

    vector_store.insert_many(records)
