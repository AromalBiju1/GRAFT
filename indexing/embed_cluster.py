"""Embedding generation and vector clustering for GRAFT's RAPTOR-style tree.

Takes leaf ``TreeNode`` chunks, embeds them with SentenceTransformers, and
groups semantically similar chunks so each group can be summarized
recursively (Issue #15).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np

from indexing.tree_node import TreeNode

logger = logging.getLogger(__name__)


@dataclass
class ClusterGroup:
    cluster_id: int
    node_ids: List[str]  # References to TreeNode.node_id


@dataclass
class ClusterResult:
    clusters: List[ClusterGroup]
    embeddings: Dict[str, List[float]] = field(default_factory=dict)  # node_id -> vector


class EmbedClusterManager:
    """Embeds TreeNodes and clusters them by semantic similarity.

    Args:
        model_name: SentenceTransformers model name or local path.
        min_cluster_size: Target minimum chunks per cluster. The cluster count
            is chosen as ``K = max(1, N // min_cluster_size)``.
        algorithm: ``"gmm"`` (Gaussian Mixture, RAPTOR default) or ``"kmeans"``.
            GMM falls back to K-Means if it fails to fit.
        random_state: Seed for reproducible clustering.
        batch_size: Encoding batch size.
        encoder: Optional object exposing ``encode(texts, ...)`` -> array.
            Mainly for tests / custom backends; skips loading the model.
    """

    def __init__(
        self,
        model_name: str = "all-MiniLM-L6-v2",
        min_cluster_size: int = 4,
        algorithm: str = "gmm",
        random_state: int = 42,
        batch_size: int = 32,
        encoder: Optional[Any] = None,
    ):
        if min_cluster_size < 1:
            raise ValueError("min_cluster_size must be >= 1")
        if algorithm not in ("gmm", "kmeans"):
            raise ValueError("algorithm must be 'gmm' or 'kmeans'")
        self.model_name = model_name
        self.min_cluster_size = min_cluster_size
        self.algorithm = algorithm
        self.random_state = random_state
        self.batch_size = batch_size
        self._encoder = encoder

    # ------------------------------------------------------------------ #
    # Embeddings
    # ------------------------------------------------------------------ #
    @property
    def encoder(self) -> Any:
        """Lazily load the SentenceTransformer model on first use."""
        if self._encoder is None:
            from sentence_transformers import SentenceTransformer

            self._encoder = SentenceTransformer(self.model_name)
        return self._encoder

    @staticmethod
    def _validate(nodes: List[TreeNode]) -> None:
        for i, node in enumerate(nodes):
            if not getattr(node, "node_id", None):
                raise ValueError(f"Node at index {i} is missing node_id")
            if getattr(node, "text", None) is None:
                raise ValueError(f"Node {node.node_id!r} is missing text")

    def generate_embeddings(self, nodes: List[TreeNode]) -> List[TreeNode]:
        """Computes embeddings for each node and populates the node.embedding field."""
        if not nodes:
            return nodes
        self._validate(nodes)

        vectors = self.encoder.encode(
            [n.text for n in nodes],
            batch_size=self.batch_size,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        vectors = np.asarray(vectors, dtype=np.float32)
        for node, vec in zip(nodes, vectors):
            node.embedding = vec.tolist()
        return nodes

    # ------------------------------------------------------------------ #
    # Clustering
    # ------------------------------------------------------------------ #
    def _num_clusters(self, n: int) -> int:
        return max(1, n // self.min_cluster_size)

    def _fit_labels(self, X: np.ndarray, k: int) -> np.ndarray:
        from sklearn.cluster import KMeans
        from sklearn.mixture import GaussianMixture

        if self.algorithm == "gmm":
            try:
                # Diagonal covariance: full covariance is ill-conditioned when
                # dims (384) far exceed the number of samples.
                gmm = GaussianMixture(
                    n_components=k,
                    covariance_type="diag",
                    reg_covar=1e-4,
                    random_state=self.random_state,
                )
                return gmm.fit(X).predict(X)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("GMM failed (%s); falling back to KMeans", exc)

        return KMeans(n_clusters=k, n_init=10, random_state=self.random_state).fit_predict(X)

    def cluster_nodes(self, nodes: List[TreeNode]) -> List[ClusterGroup]:
        """Groups nodes into semantic clusters based on their embedding vectors.

        Every input node lands in exactly one cluster. If there are fewer than
        ``min_cluster_size`` nodes (K == 1), a single cluster is returned.
        Nodes without an embedding are embedded first.
        """
        if not nodes:
            return []
        self._validate(nodes)

        if any(getattr(n, "embedding", None) is None for n in nodes):
            self.generate_embeddings(nodes)

        k = self._num_clusters(len(nodes))
        if k == 1:
            return [ClusterGroup(cluster_id=0, node_ids=[n.node_id for n in nodes])]

        X = np.asarray([n.embedding for n in nodes], dtype=np.float64)
        try:
            labels = self._fit_labels(X, k)
        except Exception as exc:
            logger.warning("Clustering failed (%s); using a single cluster", exc)
            return [ClusterGroup(cluster_id=0, node_ids=[n.node_id for n in nodes])]

        buckets: Dict[int, List[str]] = {}
        for node, label in zip(nodes, labels):
            buckets.setdefault(int(label), []).append(node.node_id)

        # Drop empty components and renumber ids contiguously from 0.
        return [
            ClusterGroup(cluster_id=i, node_ids=buckets[label])
            for i, label in enumerate(sorted(buckets))
        ]

    def run(self, nodes: List[TreeNode]) -> ClusterResult:
        """Executes embedding generation and clustering in a single pass."""
        if not nodes:
            return ClusterResult(clusters=[], embeddings={})
        self.generate_embeddings(nodes)
        clusters = self.cluster_nodes(nodes)
        embeddings = {n.node_id: list(n.embedding) for n in nodes}
        return ClusterResult(clusters=clusters, embeddings=embeddings)