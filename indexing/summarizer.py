"""Recursive RAPTOR summarization engine (indexing/summarizer.py).

Builds a hierarchical document tree by iteratively clustering child nodes
and generating higher-level summaries with an LLM until a single root or
the stopping criterion (``len(layer) <= 3``) is reached.

Pipeline (``build_tree_layers`` / ``build_raptor_tree``):

1. Clustering — group nodes at layer ``L`` with GMM (default), k-means,
   or UMAP+GMM. Falls back to sequential grouping when embeddings are
   missing, uniform, or the backend is unavailable.
2. Summarization — concatenate child texts, render
   ``indexing.prompts.SUMMARIZATION_PROMPT``, call ``llm_client``.
3. Linking — ``child.parent_id = summary.node_id`` and
   ``summary.child_ids = [child.node_id ...]`` (bidirectional).
4. Termination — stop when the current layer has 1 node (root) or when a
   layer has ``<= 3`` nodes (collapsed into a single final root).

``llm_client`` contract: a callable ``(prompt: str) -> str``. Objects
exposing ``generate`` / ``complete`` / ``summarize`` / ``invoke`` / ``chat``
are also accepted. The return value must be a non-empty string (or a dict /
object wrapping one under ``text`` / ``summary`` / ``content``).
"""

from __future__ import annotations

import math
from typing import Any, List

from indexing.config import (
    COLLAPSE_THRESHOLD,
    DEFAULT_CLUSTER_METHOD,
    DEFAULT_MAX_SUMMARY_TOKENS,
    DEFAULT_MIN_CLUSTER_SIZE,
    DEFAULT_RANDOM_STATE,
    MAX_TREE_DEPTH,
)
from indexing.prompts import SUMMARIZATION_PROMPT
from indexing.tree_node import TreeNode

_VALID_METHODS = ("gmm", "kmeans", "umap_gmm")


class RecursiveSummarizer:
    """Iteratively cluster nodes and summarize each cluster with an LLM."""

    def __init__(
        self,
        llm_client: Any,
        max_summary_tokens: int = DEFAULT_MAX_SUMMARY_TOKENS,
        min_cluster_size: int = DEFAULT_MIN_CLUSTER_SIZE,
        cluster_method: str = DEFAULT_CLUSTER_METHOD,
        random_state: int = DEFAULT_RANDOM_STATE,
        node_id_prefix: str = "summary",
    ) -> None:
        """Create a summarizer.

        Args:
            llm_client: Callable ``(prompt) -> summary`` or an object with a
                ``generate`` / ``complete`` / ``summarize`` / ``invoke`` /
                ``chat`` / ``__call__`` entry point.
            max_summary_tokens: Truncate summaries to this many whitespace
                separated tokens (words). Must be >= 1.
            min_cluster_size: Target minimum cluster size; also drives the
                automatic cluster count (``n // min_cluster_size``) and the
                sequential fallback group size. Must be >= 2.
            cluster_method: ``"gmm"`` (default), ``"kmeans"``, or
                ``"umap_gmm"`` (UMAP reduction + GMM; falls back to GMM when
                UMAP is not installed).
            random_state: Seed for deterministic clustering.
            node_id_prefix: Prefix for generated summary IDs
                (``{prefix}_L{level}_{counter}``). Callers that build one tree
                per document must pass a document-scoped prefix, otherwise IDs
                collide across documents and parent links break.

        Raises:
            ValueError: On invalid ``max_summary_tokens``,
                ``min_cluster_size``, or ``cluster_method``.
            TypeError: If ``llm_client`` has no usable call entry point.
        """
        if llm_client is None or not (
            callable(llm_client)
            or any(
                callable(getattr(llm_client, name, None))
                for name in ("generate", "complete", "summarize", "invoke", "chat", "__call__")
            )
        ):
            raise TypeError("llm_client must be callable or expose generate/complete/summarize/invoke/chat")
        if not isinstance(max_summary_tokens, int) or isinstance(max_summary_tokens, bool):
            raise TypeError("max_summary_tokens must be an integer")
        if max_summary_tokens < 1:
            raise ValueError("max_summary_tokens must be >= 1")
        if not isinstance(min_cluster_size, int) or isinstance(min_cluster_size, bool):
            raise TypeError("min_cluster_size must be an integer")
        if min_cluster_size < 2:
            raise ValueError("min_cluster_size must be >= 2")
        if cluster_method not in _VALID_METHODS:
            raise ValueError(f"cluster_method must be one of {_VALID_METHODS}, got {cluster_method!r}")

        self.llm_client = llm_client
        self.max_summary_tokens = max_summary_tokens
        self.min_cluster_size = min_cluster_size
        self.cluster_method = cluster_method
        self.random_state = random_state
        if not isinstance(node_id_prefix, str) or not node_id_prefix.strip():
            raise ValueError("node_id_prefix must be a non-empty string")
        self.node_id_prefix = node_id_prefix
        self._node_counter = 0

    # ------------------------------------------------------------------
    # Public API (spec interface)
    # ------------------------------------------------------------------

    def summarize_cluster(self, child_nodes: List[TreeNode], level: int) -> TreeNode:
        """Generate a summary node for a cluster of child nodes.

        Concatenates child texts, prompts the LLM via
        ``SUMMARIZATION_PROMPT``, truncates to ``max_summary_tokens``,
        creates the parent node, and links children bidirectionally.

        Args:
            child_nodes: Non-empty cluster; all members should share
                ``level - 1`` (not strictly enforced).
            level: Level for the new summary node; must be >= 1.

        Returns:
            The new summary ``TreeNode`` with ``child_ids`` populated;
            each child's ``parent_id`` is set to the new node's ID
            (input list is mutated).

        Raises:
            ValueError: If ``child_nodes`` is empty or ``level`` < 1, or the
                LLM returns an empty summary.
            TypeError: If the LLM return value cannot be coerced to text.
        """
        if not child_nodes:
            raise ValueError("child_nodes must be a non-empty list")
        if not isinstance(level, int) or isinstance(level, bool) or level < 1:
            raise ValueError("level must be an integer >= 1")
        for child in child_nodes:
            if not isinstance(child, TreeNode):
                raise TypeError(f"child_nodes must contain TreeNode objects, got {type(child).__name__}")

        concatenated = "\n\n".join(child.text for child in child_nodes)
        prompt = SUMMARIZATION_PROMPT.format(concatenated_texts=concatenated)
        summary_text = self._call_llm(prompt)

        node_id = self._next_node_id(level)
        document_id = self._infer_document_id(child_nodes)
        embedding = self._mean_embedding(child_nodes)
        metadata: dict[str, Any] = {
            "document_id": document_id,
            "child_count": len(child_nodes),
        }
        # Preserve source when all children agree (traceability).
        sources = {str(c.metadata.get("source")) for c in child_nodes if c.metadata.get("source")}
        if len(sources) == 1:
            metadata["source"] = next(iter(sources))

        summary_node = TreeNode(
            node_id=node_id,
            text=summary_text,
            level=level,
            embedding=embedding,
            parent_id=None,
            child_ids=[child.node_id for child in child_nodes],
            metadata=metadata,
        )
        for child in child_nodes:
            child.parent_id = node_id
        return summary_node

    def build_tree_layers(self, leaf_nodes: List[TreeNode]) -> List[TreeNode]:
        """Recursively cluster and summarize until the root is generated.

        Args:
            leaf_nodes: Level-0 nodes. Empty input returns ``[]``; a single
                node is returned as-is (already the root).

        Returns:
            Flat list of every node across layers 0 through root. Input
            leaves are mutated (``parent_id`` set) as links are created.
            The final root has ``parent_id is None``.

        Termination: stops when the current layer has 1 node, or when a
        layer has ``<= 3`` nodes (collapsed into one final root summary).
        """
        if not leaf_nodes:
            return []
        for node in leaf_nodes:
            if not isinstance(node, TreeNode):
                raise TypeError(f"leaf_nodes must contain TreeNode objects, got {type(node).__name__}")

        all_nodes: List[TreeNode] = list(leaf_nodes)
        current: List[TreeNode] = list(leaf_nodes)
        level = 0

        if len(current) == 1:
            current[0].parent_id = None
            return all_nodes

        guard = 0
        while len(current) > 1:
            guard += 1
            if guard > MAX_TREE_DEPTH:
                break
            clusters = self._cluster_nodes(current)
            next_layer: List[TreeNode] = []
            for cluster in clusters:
                summary = self.summarize_cluster(cluster, level=level + 1)
                next_layer.append(summary)
                all_nodes.append(summary)
            current = next_layer
            level += 1
            if len(current) == 1:
                break
            if len(current) <= COLLAPSE_THRESHOLD:
                # Collapse the small layer into a single final root.
                root = self.summarize_cluster(current, level=level + 1)
                all_nodes.append(root)
                current = [root]
                level += 1
                break

        # Invariant: single root with no parent.
        if len(current) == 1:
            current[0].parent_id = None
        return all_nodes

    def build_raptor_tree(self, leaf_nodes: List[TreeNode]) -> List[TreeNode]:
        """Alias for :meth:`build_tree_layers` (spec pipeline name)."""
        return self.build_tree_layers(leaf_nodes)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _next_node_id(self, level: int) -> str:
        node_id = f"{self.node_id_prefix}_L{level}_{self._node_counter:04d}"
        self._node_counter += 1
        return node_id

    @staticmethod
    def _infer_document_id(child_nodes: List[TreeNode]) -> str:
        for child in child_nodes:
            document_id = child.metadata.get("document_id")
            if isinstance(document_id, str) and document_id.strip():
                return document_id
        return "doc_001"

    @staticmethod
    def _mean_embedding(child_nodes: List[TreeNode]) -> list[float] | None:
        vectors = [c.embedding for c in child_nodes if c.embedding]
        if not vectors or any(not v for v in vectors):
            return None
        dim = len(vectors[0])
        if any(len(v) != dim for v in vectors):
            return None
        try:
            return [sum(col) / len(vectors) for col in zip(*vectors)]
        except (TypeError, ValueError):
            return None

    def _call_llm(self, prompt: str) -> str:
        """Invoke the LLM client and return a truncated non-empty summary."""
        client = self.llm_client
        result: Any = None
        if callable(client):
            try:
                result = client(prompt)
            except TypeError:
                # Callable object whose __call__ has an incompatible
                # signature — fall through to method probing.
                result = None
        if result is None:
            for method_name in ("generate", "complete", "summarize", "invoke", "chat"):
                method = getattr(client, method_name, None)
                if callable(method):
                    result = method(prompt)
                    break
        if result is None:
            raise TypeError("llm_client produced no result for the summarization prompt")

        text = self._coerce_text(result)
        if not text.strip():
            raise ValueError("LLM returned an empty summary")
        return self._truncate_tokens(text)

    @staticmethod
    def _coerce_text(result: Any) -> str:
        if isinstance(result, str):
            return result
        if isinstance(result, dict):
            for key in ("text", "summary", "content", "output"):
                value = result.get(key)
                if isinstance(value, str) and value.strip():
                    return value
            raise TypeError(f"LLM dict result has no text field (keys={sorted(result)})")
        for attr in ("text", "content", "summary", "output"):
            value = getattr(result, attr, None)
            if isinstance(value, str) and value.strip():
                return value
        raise TypeError(f"LLM result of type {type(result).__name__} cannot be coerced to text")

    def _truncate_tokens(self, text: str) -> str:
        """Cap *text* at ``max_summary_tokens`` whitespace-separated tokens."""
        words = text.split()
        if len(words) > self.max_summary_tokens:
            return " ".join(words[: self.max_summary_tokens])
        return text

    # -- clustering ----------------------------------------------------

    def _cluster_nodes(self, nodes: List[TreeNode]) -> List[List[TreeNode]]:
        """Group ``nodes`` into clusters.

        Uses ``cluster_method`` when embeddings carry signal; otherwise falls
        back to sequential groups of ``min_cluster_size``.
        """
        if len(nodes) <= self.min_cluster_size:
            return [list(nodes)]
        matrix = self._embeddings_matrix(nodes)
        if matrix is None:
            return self._sequential_clusters(nodes)
        n_clusters = max(1, len(nodes) // self.min_cluster_size)
        if n_clusters <= 1:
            return [list(nodes)]
        n_clusters = min(n_clusters, len(nodes) - 1)
        # Duplicate vectors cannot be separated: GMM/KMeans emit
        # ConvergenceWarning and collapse to a single component, so cap the
        # cluster count at the number of distinct points and bail out when
        # that leaves nothing to discover.
        n_clusters = min(n_clusters, int(self._distinct_rows(matrix)))
        if n_clusters < 2:
            return self._sequential_clusters(nodes)
        try:
            if self.cluster_method == "kmeans":
                labels = self._kmeans_labels(matrix, n_clusters)
            elif self.cluster_method == "umap_gmm":
                labels = self._umap_gmm_labels(matrix, n_clusters)
            else:
                labels = self._gmm_labels(matrix, n_clusters)
        except Exception:
            return self._sequential_clusters(nodes)
        groups: dict[int, List[TreeNode]] = {}
        for node, label in zip(nodes, labels):
            groups.setdefault(int(label), []).append(node)
        clusters = [groups[key] for key in sorted(groups)]
        # Merge degenerate singletons produced by GMM edge cases back into
        # the largest cluster to respect min_cluster_size intent.
        singletons = [c for c in clusters if len(c) < 2]
        if singletons and len(clusters) > 1:
            bulky = [c for c in clusters if len(c) >= 2]
            if bulky:
                largest = max(bulky, key=len)
                for singleton in singletons:
                    if singleton is not largest:
                        largest.extend(singleton)
                clusters = [c for c in clusters if len(c) >= 2 or c is largest]
                # Rebuild in case largest absorbed everything.
                if len(clusters) == 1 and sum(len(c) for c in clusters) != len(nodes):
                    return self._sequential_clusters(nodes)
        if sum(len(c) for c in clusters) != len(nodes) or not clusters:
            return self._sequential_clusters(nodes)
        return clusters

    def _sequential_clusters(self, nodes: List[TreeNode]) -> List[List[TreeNode]]:
        size = max(2, self.min_cluster_size)
        return [nodes[i : i + size] for i in range(0, len(nodes), size)]

    @staticmethod
    def _embeddings_matrix(nodes: List[TreeNode]) -> Any | None:
        try:
            import numpy as np
        except ImportError:
            return None
        vectors: list[list[float]] = []
        for node in nodes:
            emb = node.embedding
            if not emb:
                return None
            try:
                vectors.append([float(v) for v in emb])
            except (TypeError, ValueError):
                return None
        if not vectors:
            return None
        dim = len(vectors[0])
        if dim == 0 or any(len(v) != dim for v in vectors):
            return None
        matrix = np.asarray(vectors, dtype=float)
        if matrix.shape != (len(nodes), dim):
            return None
        if not all(math.isfinite(float(v)) for v in matrix.ravel()):
            return None
        return matrix

    @staticmethod
    def _distinct_rows(matrix: Any) -> int:
        """Number of unique embedding rows.

        ``ndarray.var()`` flattens the matrix, so it reports non-zero variance
        for identical rows whose dimensions differ (e.g. ``[1, 2, 3]``
        repeated). Compare whole rows instead: a single distinct row means the
        embeddings carry no clustering signal.
        """
        try:
            import numpy as np
        except ImportError:  # pragma: no cover - numpy ships with scikit-learn
            return 0
        return int(np.unique(matrix, axis=0).shape[0])

    def _gmm_labels(self, matrix: Any, n_clusters: int) -> list[int]:
        from sklearn.mixture import GaussianMixture

        model = GaussianMixture(
            n_components=n_clusters,
            covariance_type="diag",
            random_state=self.random_state,
            n_init=3,
        )
        labels = model.fit_predict(matrix)
        return [int(v) for v in labels]

    def _kmeans_labels(self, matrix: Any, n_clusters: int) -> list[int]:
        from sklearn.cluster import KMeans

        model = KMeans(
            n_clusters=n_clusters,
            random_state=self.random_state,
            n_init=10,
        )
        labels = model.fit_predict(matrix)
        return [int(v) for v in labels]

    def _umap_gmm_labels(self, matrix: Any, n_clusters: int) -> list[int]:
        try:
            import umap  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("umap not installed; falling back to GMM") from exc
        n_samples = matrix.shape[0]
        n_components = max(2, min(5, matrix.shape[1], n_samples - 1))
        reducer = umap.UMAP(n_components=n_components, random_state=self.random_state)
        reduced = reducer.fit_transform(matrix)
        return self._gmm_labels(reduced, n_clusters)


def build_raptor_tree(
    leaf_nodes: List[TreeNode],
    llm_client: Any,
    max_summary_tokens: int = DEFAULT_MAX_SUMMARY_TOKENS,
    min_cluster_size: int = DEFAULT_MIN_CLUSTER_SIZE,
    cluster_method: str = DEFAULT_CLUSTER_METHOD,
) -> List[TreeNode]:
    """Build a RAPTOR tree from ``leaf_nodes`` with a one-shot summarizer.

    Convenience wrapper around :class:`RecursiveSummarizer` for callers
    that do not need to reuse the instance.
    """
    summarizer = RecursiveSummarizer(
        llm_client,
        max_summary_tokens=max_summary_tokens,
        min_cluster_size=min_cluster_size,
        cluster_method=cluster_method,
    )
    return summarizer.build_tree_layers(leaf_nodes)


__all__ = ["RecursiveSummarizer", "build_raptor_tree"]
