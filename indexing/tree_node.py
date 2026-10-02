"""Tree-node data structure for the RAPTOR-based indexing pipeline."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TreeNode:
    """Represent a chunk or summary in a strict tree with at most one parent."""

    node_id: str
    text: str
    level: int
    embedding: list[float] | None = None
    parent_id: str | None = None
    child_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_leaf(self) -> bool:
        """Return whether the node is at level zero or has no children."""
        return self.level == 0 or len(self.child_ids) == 0

    def to_dict(self) -> dict[str, Any]:
        """Return the tree-store representation, excluding the embedding."""
        return {
            "node_id": self.node_id,
            "text": self.text,
            "level": self.level,
            "parent_id": self.parent_id,
            "child_ids": self.child_ids,
            "metadata": self.metadata,
        }
