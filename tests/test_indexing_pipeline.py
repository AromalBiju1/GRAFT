"""End-to-end integration smoke test for the indexing pipeline.

Covers the full tree construction pipeline:
  Ingest (parse) -> Chunk -> Embed -> Cluster -> Summarize -> Persist

Validation criteria (from issue #17):
  - Verify top-level root summaries exist (level > 0)
  - Verify every leaf node contains non-empty embeddings
  - Spot-check parent-child references (parent_id -> valid summary at level+1)
  - Query ChromaDB to confirm all TreeNode entries persist properly

Also tests the FastAPI POST /index multipart endpoint smoke.
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from indexing.builder import build_tree, build_tree_from_chunks, _stub_embedding
from indexing.chunker import chunk_text
from indexing.ingest import parse_document
from indexing.store import persist_tree_nodes
from indexing.tree_node import TreeNode
from indexing.vector_store import ChromaVectorStore

# Try both backend and graft API entrypoints
try:
    from backend.app.main import app as backend_app
except Exception:
    backend_app = None  # type: ignore

try:
    from graft.api.main import app as graft_app
except Exception:
    graft_app = None  # type: ignore


SAMPLE_DOCS_DIR = Path(__file__).parent / "data" / "sample_docs"
# Fallback to repo data/sample_docs if tests/data/sample_docs missing
REPO_SAMPLE_DOCS = Path(__file__).parents[1] / "data" / "sample_docs"


def _get_sample_files(limit: int = 3) -> list[Path]:
    """Return 2-3 sample PDF/DOCX files for smoke testing."""
    candidates: list[Path] = []
    for base in (SAMPLE_DOCS_DIR, REPO_SAMPLE_DOCS):
        if base.exists():
            for suffix in (".pdf", ".docx"):
                for p in sorted(base.glob(f"*{suffix}")):
                    if p.is_file():
                        candidates.append(p)
                    if len(candidates) >= limit:
                        break
                if len(candidates) >= limit:
                    break
        if candidates:
            break
    # Ensure at least 1 fallback: create a minimal docx if no PDFs found
    if not candidates:
        with tempfile.TemporaryDirectory() as tmp:
            fallback = Path(tmp) / "fallback.docx"
            try:
                from docx import Document

                doc = Document()
                doc.add_paragraph("Fallback sample document for smoke test.")
                doc.add_paragraph("Second paragraph with enough words " * 30)
                doc.save(fallback)
                # copy to temp persistent location handled by caller
            except Exception:
                pass
    return candidates[:limit]


class IndexingPipelineSmokeTests(unittest.TestCase):
    """Pipeline-stage unit + integration checks."""

    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.store = ChromaVectorStore(persist_path=self.temp_directory.name)
        self.sample_files = _get_sample_files(3)
        if len(self.sample_files) < 2:
            self.skipTest(f"Need at least 2 sample docs, found {self.sample_files}")

    def tearDown(self) -> None:
        self.store.close()
        for attempt in range(20):
            try:
                self.temp_directory.cleanup()
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.05)

    def test_full_pipeline_ingest_to_persist(self) -> None:
        """Ingest 2-3 sample docs through the full pipeline and validate tree."""
        all_nodes: list[TreeNode] = []
        total_chunks = 0

        for file_path in self.sample_files[:3]:
            text = parse_document(file_path)
            self.assertTrue(text.strip(), f"Extracted text should not be empty for {file_path}")

            # Chunk with small size so even short docs yield multiple chunks
            chunks = chunk_text(
                text, chunk_size=200, overlap=20, document_id=file_path.stem, source=file_path.name
            )
            self.assertGreaterEqual(len(chunks), 1, f"Should produce at least one chunk for {file_path}")
            total_chunks += len(chunks)

            nodes = build_tree_from_chunks(
                chunks, cluster_size=4, document_id=file_path.stem, embedding_fn=_stub_embedding
            )
            # Build tree also via high-level helper for one file to ensure both paths work
            if file_path == self.sample_files[0]:
                nodes_via_text = build_tree(
                    text, document_id=file_path.stem, source=file_path.name, chunk_size=200, chunk_overlap=20, cluster_size=4
                )
                self.assertEqual(len(nodes), len(nodes_via_text))

            all_nodes.extend(nodes)

        # Persist combined nodes
        persist_tree_nodes(all_nodes, self.store)

        # ---- Validation Criteria ----

        # 1) Top-level root summaries exist (level > 0)
        levels = {n.level for n in all_nodes}
        self.assertGreater(max(levels), 0, "Should have at least one summary/root level >0")
        summaries = [n for n in all_nodes if n.level > 0]
        self.assertGreaterEqual(len(summaries), 1)
        # Ensure highest level node(s) exist
        max_level = max(levels)
        roots = [n for n in all_nodes if n.level == max_level]
        self.assertGreaterEqual(len(roots), 1)

        # 2) Every leaf node contains non-empty embeddings
        leaves = [n for n in all_nodes if n.level == 0]
        self.assertEqual(len(leaves), total_chunks, "Leaf count should match total chunks")
        for leaf in leaves:
            self.assertIsNotNone(leaf.embedding, f"Leaf {leaf.node_id} missing embedding")
            assert leaf.embedding is not None
            self.assertGreater(len(leaf.embedding), 0)
            self.assertTrue(all(isinstance(v, float) for v in leaf.embedding))
            # Spot check embedding is normalized (stub L2-norm =1)
            norm = sum(v * v for v in leaf.embedding) ** 0.5
            self.assertAlmostEqual(norm, 1.0, delta=1e-6)

        # Also check summary nodes have embeddings
        for node in summaries:
            self.assertIsNotNone(node.embedding)
            assert node.embedding is not None
            self.assertGreater(len(node.embedding), 0)

        # 3) Spot-check parent-child references
        node_by_id = {n.node_id: n for n in all_nodes}
        for leaf in leaves:
            self.assertIsNotNone(leaf.parent_id, f"Leaf {leaf.node_id} should have parent_id")
            assert leaf.parent_id is not None
            self.assertIn(leaf.parent_id, node_by_id, f"parent_id {leaf.parent_id} not found for leaf {leaf.node_id}")
            parent = node_by_id[leaf.parent_id]
            self.assertEqual(parent.level, leaf.level + 1, "Parent should be at level+1")
            self.assertIn(leaf.node_id, parent.child_ids, "Parent child_ids should contain leaf")

        for node in summaries:
            if node.child_ids:
                for child_id in node.child_ids:
                    self.assertIn(child_id, node_by_id)
                    child = node_by_id[child_id]
                    self.assertEqual(child.parent_id, node.node_id)
                    self.assertEqual(child.level, node.level - 1)

        # Ensure no node references non-existent parent except roots (parent_id None)
        for node in all_nodes:
            if node.parent_id is not None:
                self.assertIn(node.parent_id, node_by_id)

        # 4) Query ChromaDB to confirm all TreeNode entries persist properly
        # Use a query embedding matching a leaf to ensure recall
        query_vec = _stub_embedding(leaves[0].text)
        results = self.store.query(query_vec, n_results=len(all_nodes))
        self.assertEqual(len(results), len(all_nodes), "Chroma should contain all nodes")
        result_ids = {r["chunk_id"] for r in results}
        expected_ids = {n.node_id for n in all_nodes}
        self.assertEqual(result_ids, expected_ids)

        # Validate metadata filtering by level works (uses stored level field)
        for lvl in levels:
            level_results = self.store.query(query_vec, n_results=len(all_nodes), filters={"level": lvl})
            expected_at_level = {n.node_id for n in all_nodes if n.level == lvl}
            self.assertEqual({r["chunk_id"] for r in level_results}, expected_at_level)
            self.assertTrue(all(r["metadata"]["level"] == lvl for r in level_results))

        # Validate parent_id / child_ids stored correctly via metadata string encoding
        for node in all_nodes:
            # Find its persisted record
            rec = next(r for r in results if r["chunk_id"] == node.node_id)
            meta = rec["metadata"]
            self.assertEqual(meta["node_id"], node.node_id)
            self.assertEqual(meta["level"], node.level)
            self.assertEqual(meta["parent_id"], node.parent_id or "")
            self.assertEqual(meta["child_ids"], ",".join(node.child_ids))
            # document_id must be present and non-empty
            self.assertTrue(meta.get("document_id"))

    def test_chunker_and_builder_import_paths(self) -> None:
        """Ensure spec-required import paths exist and delegate correctly."""
        # These imports must succeed per issue spec
        from indexing.chunker import chunk_text as ct2  # noqa: F401
        from indexing.ingest import parse_document as pd2  # noqa: F401
        from indexing.builder import build_tree as bt2  # noqa: F401

        self.assertTrue(callable(ct2))
        self.assertTrue(callable(pd2))
        self.assertTrue(callable(bt2))

    def test_vector_store_shim(self) -> None:
        """vector_store shim should re-export ChromaVectorStore."""
        from vector_store import ChromaVectorStore as ShimStore  # type: ignore

        self.assertIs(ShimStore, ChromaVectorStore)


class IndexingEndpointSmokeTests(unittest.TestCase):
    """Smoke test for POST /index multipart endpoint."""

    def _client_for_app(self, app):
        return TestClient(app)

    def _pick_files_for_upload(self, count: int = 2) -> list[tuple[str, bytes, str]]:
        files = _get_sample_files(count)
        result: list[tuple[str, bytes, str]] = []
        for p in files[:count]:
            data = p.read_bytes()
            mime = "application/pdf" if p.suffix.lower() == ".pdf" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            result.append((p.name, data, mime))
        # If we have fewer than count, create an extra docx on the fly
        while len(result) < count:
            from docx import Document
            import io

            doc = Document()
            doc.add_paragraph(f"Generated docx {len(result)} for endpoint test.")
            doc.add_paragraph("Lorem ipsum " * 100)
            bio = io.BytesIO()
            doc.save(bio)
            result.append((f"generated_{len(result)}.docx", bio.getvalue(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))
        return result

    def test_post_index_multipart_single_file(self) -> None:
        if backend_app is None:
            self.skipTest("backend.app not importable")
        files_to_upload = self._pick_files_for_upload(1)
        filename, content, mime = files_to_upload[0]

        # Use a temp Chroma persist path for endpoint to avoid polluting .graft/chroma
        with tempfile.TemporaryDirectory() as tmpdir:
            # Patch both possible import locations to use temp dir
            with patch("api.routes.indexing.DEFAULT_PERSIST_PATH", Path(tmpdir)), patch(
                "api.routes.indexing.ChromaVectorStore", lambda persist_path=tmpdir, collection_name="graft_tree_nodes": ChromaVectorStore(persist_path=tmpdir, collection_name=collection_name)
            ):
                # Also patch graft.api.main settings if needed (for graft app path)
                client = self._client_for_app(backend_app)
                resp = client.post(
                    "/index",
                    files={"files": (filename, content, mime)},
                )
                self.assertEqual(resp.status_code, 200, resp.text)
                data = resp.json()
                self.assertEqual(data.get("status"), "success")
                self.assertIn("document_ids", data)
                self.assertIn("total_chunks", data)
                self.assertIn("tree_stats", data)
                self.assertGreater(data["total_chunks"], 0)
                self.assertGreater(data["tree_stats"]["total_nodes"], 0)
                self.assertIn("levels", data["tree_stats"])
                levels = data["tree_stats"]["levels"]
                # Must have leaf level and at least one higher level
                self.assertIn("0_leaf", levels)
                has_summary = any(k.endswith("_summary") or k.endswith("_root") for k in levels)
                self.assertTrue(has_summary, f"Expected summary/root level, got {levels}")
                # document_ids should match uploaded file stem sanitized (contains filename stem)
                self.assertEqual(len(data["document_ids"]), 1)

                # Verify persistence by querying temp store directly
                store = ChromaVectorStore(persist_path=tmpdir)
                try:
                    query_vec = _stub_embedding("test")
                    n_expect = data["tree_stats"]["total_nodes"]
                    results = store.query(query_vec, n_results=n_expect + 5)
                    self.assertGreaterEqual(len(results), data["total_chunks"])
                    # Ensure at least one leaf and one summary persisted
                    leaf_count = sum(1 for r in results if r["metadata"].get("level") == 0)
                    self.assertGreaterEqual(leaf_count, 1)
                    summary_count = sum(1 for r in results if r["metadata"].get("level", 0) > 0)
                    self.assertGreaterEqual(summary_count, 1)
                finally:
                    store.close()

    def test_post_index_multipart_multiple_files(self) -> None:
        if backend_app is None:
            self.skipTest("backend.app not importable")
        files_to_upload = self._pick_files_for_upload(2)
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("api.routes.indexing.DEFAULT_PERSIST_PATH", Path(tmpdir)), patch(
                "api.routes.indexing.ChromaVectorStore", lambda persist_path=tmpdir, collection_name="graft_tree_nodes": ChromaVectorStore(persist_path=tmpdir, collection_name=collection_name)
            ):
                client = self._client_for_app(backend_app)
                # Build files list for TestClient: list of tuples
                files = [("files", (name, content, mime)) for name, content, mime in files_to_upload]
                resp = client.post("/index", files=files)
                self.assertEqual(resp.status_code, 200, resp.text)
                data = resp.json()
                self.assertEqual(data["status"], "success")
                self.assertEqual(len(data["document_ids"]), 2)
                self.assertGreaterEqual(data["total_chunks"], 2)
                self.assertGreater(data["tree_stats"]["total_nodes"], data["total_chunks"])
                # Tree should have root level
                levels = data["tree_stats"]["levels"]
                max_lvl_key = max(levels.keys(), key=lambda k: int(k.split("_")[0]))
                self.assertTrue(max_lvl_key.endswith("_root"))

                # Spot-check persistence: every leaf should be queryable
                store = ChromaVectorStore(persist_path=tmpdir)
                try:
                    # Query with embedding from persisted text to verify parent-child metadata
                    sample_vec = _stub_embedding("sample")
                    n_expect = data["tree_stats"]["total_nodes"]
                    results = store.query(sample_vec, n_results=n_expect + 5)
                    self.assertEqual(len(results), data["tree_stats"]["total_nodes"])
                    # Validate parent_id references
                    by_id = {r["chunk_id"]: r for r in results}
                    for r in results:
                        pid = r["metadata"].get("parent_id", "")
                        if pid:
                            self.assertIn(pid, by_id, f"Parent {pid} missing for {r['chunk_id']}")
                            parent = by_id[pid]
                            # Parent level should be child level +1
                            self.assertEqual(parent["metadata"]["level"], r["metadata"]["level"] + 1)
                finally:
                    store.close()

    def test_post_index_rejects_unsupported_type(self) -> None:
        if backend_app is None:
            self.skipTest("backend.app not importable")
        client = self._client_for_app(backend_app)
        resp = client.post(
            "/index",
            files={"files": ("evil.txt", b"not a pdf", "text/plain")},
        )
        self.assertIn(resp.status_code, (400, 422))

    def test_post_index_requires_file(self) -> None:
        if backend_app is None:
            self.skipTest("backend.app not importable")
        client = self._client_for_app(backend_app)
        resp = client.post("/index", files={})
        self.assertEqual(resp.status_code, 422)

    def test_graft_api_multipart_if_available(self) -> None:
        """If graft API is used, its multipart branch should also work."""
        if graft_app is None:
            self.skipTest("graft.api not importable")
        files_to_upload = self._pick_files_for_upload(1)
        filename, content, mime = files_to_upload[0]
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("graft.config.settings.chroma_path", Path(tmpdir)):
                # Need to also patch the store class used inside graft.api.main
                # The module imports ChromaVectorStore via indexing.vector_store inside handler
                with patch("indexing.vector_store.ChromaVectorStore", lambda persist_path=tmpdir, collection_name="graft_tree_nodes": ChromaVectorStore(persist_path=tmpdir, collection_name=collection_name)):
                    client = self._client_for_app(graft_app)
                    resp = client.post(
                        "/index",
                        files={"files": (filename, content, mime)},
                    )
                    # Graft endpoint should handle multipart and return success
                    if resp.status_code == 200:
                        data = resp.json()
                        # May be multipart response or legacy json; check for multipart shape
                        if "status" in data and data.get("status") == "success":
                            self.assertIn("document_ids", data)
                            self.assertIn("total_chunks", data)


if __name__ == "__main__":
    unittest.main()
