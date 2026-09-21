"""Tree-node data structure for the RAPTOR-based indexing pipeline."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class TreeNode:
    """Represent a chunk or summary in a strict tree with at most one parent."""

    node_id: str
    text: str
    level: int
    embedding: Optional[List[float]] = None
    parent_id: Optional[str] = None
    child_ids: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_leaf(self) -> bool:
        """Return whether the node is at level zero or has no children."""
        return self.level == 0 or len(self.child_ids) == 0

    def to_dict(self) -> Dict[str, Any]:
        """Return the tree-store representation, excluding the embedding."""
        return {
            "node_id": self.node_id,
            "text": self.text,
            "level": self.level,
            "parent_id": self.parent_id,
            "child_ids": self.child_ids,
            "metadata": self.metadata,
        }
