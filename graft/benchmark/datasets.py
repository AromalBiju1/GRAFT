"""Test question sets for benchmarking.

Keeps the question bank versioned alongside metric code so benchmark results
remain reproducible. Mirrors the analytical breakdown in data/sample_docs/sample_docs.md.
"""

from __future__ import annotations

# Minimal seed set — extend with labelled relevance sets per question.
SEED_QUESTIONS: list[dict] = [
    {
        "id": "q_fact_001",
        "query": "What cipher suites does TLS 1.2 support that TLS 1.3 bans?",
        "relevant_docs": [
            "rfc-editor.org_rfc_rfc5246.txt.pdf",
            "rfc-editor.org_rfc_rfc8446.txt.pdf",
        ],
        "tags": ["fact_lookup", "contradiction_detection"],
        "expected_depth": 0,
    },
    {
        "id": "q_compare_001",
        "query": (
            "Compare the CNN sequence-to-sequence claims in 1705 "
            "with the Transformer claims in 1706."
        ),
        "relevant_docs": ["1705.pdf", "1706.pdf"],
        "tags": ["multi_hop", "contradiction_detection"],
        "expected_depth": 2,
    },
    {
        "id": "q_numeric_001",
        "query": (
            "How many levels deep is the NIST SP 800-53 control hierarchy "
            "and how many controls are at the deepest level?"
        ),
        "relevant_docs": ["SP_800-53_v5_1-derived-OSCAL.pdf"],
        "tags": ["numeric_reasoning"],
        "expected_depth": 1,
    },
    {
        "id": "q_contradiction_001",
        "query": "Is HTTP/1.1 defined by RFC 2616 still current, or has it been superseded?",
        "relevant_docs": [
            "rfc-editor.org_rfc_rfc2616.txt.pdf",
            "rfc-editor.org_rfc_rfc9110.txt.pdf",
        ],
        "tags": ["contradiction_detection", "fact_lookup"],
        "expected_depth": 1,
    },
    {
        "id": "q_simple_001",
        "query": "What year was the original TCP specification published?",
        "relevant_docs": ["rfc-editor.org_rfc_rfc793.txt.pdf"],
        "tags": ["fact_lookup"],
        "expected_depth": 0,
    },
]

__all__ = ["SEED_QUESTIONS"]
