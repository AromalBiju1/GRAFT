#!/usr/bin/env python3
"""Regenerate the relevant_chunk_ids in data/sample_docs/benchmark.jsonl.

Chunk IDs are SHA-256 of ``f"{document_id}:{chunk_index}"`` where
document_id is the sample document's stem. The evidence coordinates below are
the labelled (document, chunk_index) pairs; re-running this script rewrites
the IDs from them instead of leaving hand-written hashes in the dataset.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BENCHMARK_PATH = REPO_ROOT / "data" / "sample_docs" / "benchmark.jsonl"

# query-prefix -> list of (filename, chunk_index) evidence coordinates.
EVIDENCE = {
    "What kind of neural network": [("1705.pdf", 0)],
    "How does the ConvS2S paper": [("1705.pdf", 0)],
    "What mechanism does the Transformer": [("1706.pdf", 3)],
    "What BLEU score": [("1706.pdf", 18)],
    "In what year": [("rfc-editor.org_rfc_rfc793.txt.pdf", 0)],
    "Which earlier TCP": [("rfc9293.pdf", 0)],
    "Which protocol": [("rfc-editor.org_rfc_rfc2616.txt.pdf", 0)],
    "Which cipher suite": [("rfc-editor.org_rfc_rfc5246.txt.pdf", 9)],
    "Using the Transformer big-model": [("1706.pdf", 18)],
    "What is the average": [("1706.pdf", 18)],
    "By how many BLEU": [("1706.pdf", 18)],
    "Comparing RFC 793": [("rfc-editor.org_rfc_rfc793.txt.pdf", 0), ("rfc9293.pdf", 0)],
    "How does the specified TLS": [
        ("rfc-editor.org_rfc_rfc5246.txt.pdf", 9),
        ("rfc-editor.org_rfc_rfc8446.txt.pdf", 14),
    ],
    "Compare the proposed sequence-to-sequence": [
        ("1705.pdf", 0),
        ("1706.pdf", 0),
        ("1706.pdf", 3),
    ],
    "The ConvS2S paper reports": [("1705.pdf", 25), ("1706.pdf", 18)],
    "For English-to-French": [("1705.pdf", 25), ("1706.pdf", 18)],
    "Do the two research papers": [("1705.pdf", 0), ("1706.pdf", 3)],
    "The two papers report": [("1705.pdf", 25), ("1706.pdf", 18)],
    "Is TLS_RSA_WITH_AES_128_CBC_SHA": [
        ("rfc-editor.org_rfc_rfc5246.txt.pdf", 9),
        ("rfc-editor.org_rfc_rfc8446.txt.pdf", 14),
    ],
    "Across both WMT": [("1705.pdf", 25), ("1706.pdf", 18)],
}


def chunk_id(filename: str, index: int) -> str:
    return hashlib.sha256(f"{Path(filename).stem}:{index}".encode()).hexdigest()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate benchmark chunk IDs from evidence coordinates."
    )
    parser.add_argument("--dataset", type=Path, default=BENCHMARK_PATH)
    parser.add_argument("--verify", action="store_true", help="also check IDs exist in Chroma")
    args = parser.parse_args(argv)

    lines = args.dataset.read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in lines if line.strip()]

    changed = 0
    for rec in records:
        prefix = next((key for key in EVIDENCE if rec["query"].startswith(key)), None)
        if prefix is None:
            raise SystemExit(f"No evidence mapping for query: {rec['query'][:60]!r}")
        new_ids = [chunk_id(filename, index) for filename, index in EVIDENCE[prefix]]
        if rec.get("relevant_chunk_ids") != new_ids:
            changed += 1
        rec["relevant_chunk_ids"] = new_ids

    args.dataset.write_text(
        "".join(
            json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n" for rec in records
        ),
        encoding="utf-8",
    )
    print(f"Rewrote {len(records)} records ({changed} changed) in {args.dataset}")

    if args.verify:
        sys.path.insert(0, str(REPO_ROOT))
        import chromadb

        from embeddings import collection_name

        client = chromadb.PersistentClient(path=str(REPO_ROOT / ".graft" / "chroma"))
        collection = client.get_collection(collection_name())
        existing = set(collection.get()["ids"])
        missing = [
            cid for rec in records for cid in rec["relevant_chunk_ids"] if cid not in existing
        ]
        if missing:
            print(f"{len(missing)} regenerated ID(s) are missing from Chroma:", file=sys.stderr)
            for cid in missing:
                print(f"  {cid}", file=sys.stderr)
            return 1
        print("All regenerated IDs exist in Chroma.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
