#!/usr/bin/env python3
"""Index every sample document under data/sample_docs into the default Chroma collection.

The benchmark assumes these documents have been indexed with their PDF/DOCX
stem as the document_id (the IDs the chunker hashes into chunk IDs).
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DIR = REPO_ROOT / "data" / "sample_docs"
ALLOWED = {".pdf", ".docx"}


def main() -> int:
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from indexing.ingest import parse_document
    from indexing.pipeline import index_document

    files = sorted(
        p for p in SAMPLE_DIR.iterdir() if p.suffix.lower() in ALLOWED and p.name != "sample.docx"
    )
    if not files:
        print(f"No sample documents found in {SAMPLE_DIR}", file=sys.stderr)
        return 1
    for path in files:
        print(f"Indexing {path.name} ...", flush=True)
        text = parse_document(path)
        nodes = index_document(text, document_id=path.stem, source=path.name)
        leaves = sum(1 for node in nodes if node.level == 0)
        print(f"  {len(nodes)} nodes ({leaves} leaves)", flush=True)
    print(f"Indexed {len(files)} documents.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
