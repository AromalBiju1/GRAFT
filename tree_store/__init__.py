"""Tree store — persisted hierarchical index (leaf chunks -> summaries -> root).

Contract: docs/interfaces.md section 2 (Indexing Pipeline -> Tree Store).

There is exactly one ``TreeNode`` type in GRAFT: the one defined and tested in
:mod:`indexing.tree_node`. This module re-exports it so the ``*``
namespace stays the single public surface without forking the data structure
into a second, subtly different dataclass (which is what previously made
``/index`` produce incompatible trees depending on the request's Content-Type).
"""

from __future__ import annotations

from indexing.tree_node import TreeNode

__all__ = ["TreeNode"]
