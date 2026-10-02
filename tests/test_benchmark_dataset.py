"""Offline contract and answer regressions for the sample-document benchmark.

These tests validate the labelled data, without calling a router or an LLM.
Evidence coordinates use the document stems and chunk indices encoded in the
dataset, following the chunk-ID contract in docs/interfaces.md. They do not
re-ingest PDFs or depend on a particular PDF parser/tokenizer version.
"""

import hashlib
import json
import re
from decimal import Decimal
from pathlib import Path

import pytest

SAMPLE_DOCS = Path(__file__).resolve().parents[1] / "data" / "sample_docs"
BENCHMARK_LINES = (SAMPLE_DOCS / "benchmark.jsonl").read_text(encoding="utf-8").splitlines()

CONVS2S_INTRO = ("1705.pdf", 0)
CONVS2S_RESULTS = ("1705.pdf", 25)
TRANSFORMER_ABSTRACT = ("1706.pdf", 0)
TRANSFORMER_ARCHITECTURE = ("1706.pdf", 3)
TRANSFORMER_RESULTS = ("1706.pdf", 18)
TCP_ORIGINAL = ("rfc-editor.org_rfc_rfc793.txt.pdf", 0)
TCP_CURRENT = ("rfc9293.pdf", 0)
HTTP_11 = ("rfc-editor.org_rfc_rfc2616.txt.pdf", 0)
TLS_12 = ("rfc-editor.org_rfc_rfc5246.txt.pdf", 9)
TLS_13 = ("rfc-editor.org_rfc_rfc8446.txt.pdf", 14)


def _unique_object(pairs):
    """Do not silently accept JSON keys that overwrite earlier labels."""
    keys = [key for key, _ in pairs]
    assert len(keys) == len(set(keys)), f"Duplicate JSON keys: {keys}"
    return dict(pairs)


@pytest.fixture(scope="module")
def records():
    """Load benchmark records while rejecting duplicate JSON keys."""
    return [json.loads(line, object_pairs_hook=_unique_object) for line in BENCHMARK_LINES]


def _question(records, prefix):
    """Find cases by query, so rearranging JSONL lines does not break tests."""
    matches = [record for record in records if record["query"].startswith(prefix)]
    assert len(matches) == 1, f"Expected one question starting with {prefix!r}"
    return matches[0]


@pytest.mark.parametrize(
    "line", BENCHMARK_LINES, ids=[f"line-{i + 1}" for i in range(len(BENCHMARK_LINES))]
)
def test_each_line_is_a_complete_labelled_question(line):
    """Validate required fields, nonempty text, and unique SHA-256 evidence IDs."""
    assert line.strip(), "Blank lines are not benchmark records"
    record = json.loads(line, object_pairs_hook=_unique_object)
    assert isinstance(record, dict)
    assert set(record) == {"query", "relevant_chunk_ids", "expected_answer"}
    for field in ("query", "expected_answer"):
        value = record[field]
        assert isinstance(value, str), f"{field} must be text"
        assert value.strip(), f"{field} must not be empty"
        assert value == value.strip(), f"{field} has surrounding whitespace"

    chunk_ids = record["relevant_chunk_ids"]
    assert isinstance(chunk_ids, list)
    assert chunk_ids, "Every question needs retrieval ground truth"
    for chunk_id in chunk_ids:
        assert isinstance(chunk_id, str)
        assert re.fullmatch(r"[0-9a-f]{64}", chunk_id), "Expected a SHA-256 chunk ID"
    assert len(chunk_ids) == len(set(chunk_ids)), "Repeated evidence skews retrieval metrics"


def test_dataset_retains_twenty_questions_without_duplicate_queries(records):
    """Require at least twenty questions with distinct normalized query text."""
    assert len(records) >= 20
    queries = [" ".join(record["query"].casefold().split()) for record in records]
    assert len(queries) == len(set(queries)), "Duplicate queries bias benchmark averages"


@pytest.mark.parametrize(
    ("prefix", "evidence"),
    [
        ("What kind of neural network", [CONVS2S_INTRO]),
        ("How does the ConvS2S paper", [CONVS2S_INTRO]),
        ("What mechanism does the Transformer", [TRANSFORMER_ARCHITECTURE]),
        ("What BLEU score", [TRANSFORMER_RESULTS]),
        ("In what year", [TCP_ORIGINAL]),
        ("Which earlier TCP", [TCP_CURRENT]),
        ("Which protocol", [HTTP_11]),
        ("Which cipher suite", [TLS_12]),
        ("Using the Transformer big-model", [TRANSFORMER_RESULTS]),
        ("What is the average", [TRANSFORMER_RESULTS]),
        ("By how many BLEU", [TRANSFORMER_RESULTS]),
        ("Comparing RFC 793", [TCP_ORIGINAL, TCP_CURRENT]),
        ("How does the specified TLS", [TLS_12, TLS_13]),
        (
            "Compare the proposed sequence-to-sequence",
            [CONVS2S_INTRO, TRANSFORMER_ABSTRACT, TRANSFORMER_ARCHITECTURE],
        ),
        ("The ConvS2S paper reports", [CONVS2S_RESULTS, TRANSFORMER_RESULTS]),
        ("For English-to-French", [CONVS2S_RESULTS, TRANSFORMER_RESULTS]),
        ("Do the two research papers", [CONVS2S_INTRO, TRANSFORMER_ARCHITECTURE]),
        ("The two papers report", [CONVS2S_RESULTS, TRANSFORMER_RESULTS]),
        ("Is TLS_RSA_WITH_AES_128_CBC_SHA", [TLS_12, TLS_13]),
        ("Across both WMT", [CONVS2S_RESULTS, TRANSFORMER_RESULTS]),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_questions_retain_all_and_only_their_labelled_evidence(records, prefix, evidence):
    """Match evidence IDs to labelled source stems and chunk indices."""
    expected_ids = set()
    for filename, index in evidence:
        source = SAMPLE_DOCS / filename
        assert source.is_file(), f"Missing benchmark source: {filename}"
        # Document IDs are PDF stems, including the '.txt' component for RFCs.
        expected_ids.add(hashlib.sha256(f"{source.stem}:{index}".encode()).hexdigest())
    assert set(_question(records, prefix)["relevant_chunk_ids"]) == expected_ids


# Decimal arithmetic avoids hiding small BLEU differences behind rounding.
# These are the reported table values, not scores from a new model run.
BIG_DE, BIG_FR = Decimal("28.4"), Decimal("41.8")
BASE_DE, BASE_FR = Decimal("27.3"), Decimal("38.1")
CONVS2S_DE, CONVS2S_FR = Decimal("26.43"), Decimal("41.62")
CONVS2S_DE_IN_TRANSFORMER = Decimal("26.36")


@pytest.mark.parametrize(
    ("prefix", "expected_numbers"),
    [
        ("What BLEU score", [BIG_FR]),
        ("Using the Transformer big-model", [BIG_DE, BIG_FR, BIG_DE + BIG_FR]),
        ("What is the average", [BASE_DE, BASE_FR, 2, (BASE_DE + BASE_FR) / 2]),
        ("By how many BLEU", [BIG_DE, BASE_DE, BIG_DE - BASE_DE]),
        ("Comparing RFC 793", [793, 1981, 9293, 2022, 2022 - 1981]),
        ("The ConvS2S paper reports", [BIG_DE, CONVS2S_DE, BIG_DE - CONVS2S_DE]),
        ("For English-to-French", [BIG_FR, 10, CONVS2S_FR, BIG_FR - CONVS2S_FR]),
        (
            "The two papers report",
            [CONVS2S_DE, CONVS2S_DE_IN_TRANSFORMER, CONVS2S_DE - CONVS2S_DE_IN_TRANSFORMER],
        ),
        ("Across both WMT", [BIG_DE, CONVS2S_DE, BIG_FR, 10, CONVS2S_FR, BIG_DE + BIG_FR]),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_numeric_answers_preserve_operands_and_correct_results(records, prefix, expected_numbers):
    """Check answer numbers against the expected operands and computed results."""
    answer = _question(records, prefix)["expected_answer"]
    # Signs attached to digits count; spaced subtraction and '10-model' do not.
    numbers = [Decimal(value) for value in re.findall(r"(?<!\w)-?\d+(?:\.\d+)?", answer)]
    assert numbers == expected_numbers


@pytest.mark.parametrize(
    ("prefix", "required_claims"),
    [
        ("What kind of neural network", [r"entirely.*convolutional"]),
        ("How does the ConvS2S paper", [r"fully parallelized.*training"]),
        ("What mechanism does the Transformer", [r"entirely.*attention", r"global dependencies"]),
        ("In what year", [r"\b1981\b"]),
        ("Which earlier TCP", [r"RFC 9293.*obsoletes RFC 793"]),
        ("Which protocol", [r"HTTP/1\.1"]),
        ("Which cipher suite", [r"\bTLS_RSA_WITH_AES_128_CBC_SHA\b"]),
        (
            "Compare the proposed sequence-to-sequence",
            [
                r"ConvS2S.*convolutional",
                r"attention modules.*decoder",
                r"Transformer.*solely.*attention",
            ],
        ),
        (
            "Do the two research papers",
            [r"ConvS2S.*convolutions", r"Transformer.*attention", r"not contradictory.*one model"],
        ),
        ("The ConvS2S paper reports", [r"Transformer big.*higher", r"configurations differ"]),
        (
            "For English-to-French",
            [r"favor of Transformer big", r"configurations are not identical"],
        ),
        ("The two papers report", [r"do not explain the discrepancy", r"rather than.*reconciled"]),
        ("Across both WMT", [r"Transformer big is higher on both", r"not a controlled"]),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_answers_preserve_key_facts_and_comparison_qualifications(records, prefix, required_claims):
    """Require each answer to retain its key claims and comparison caveats."""
    answer = _question(records, prefix)["expected_answer"]
    for claim in required_claims:
        assert re.search(claim, answer, re.IGNORECASE), f"Missing claim: {claim}"


@pytest.mark.parametrize(
    "prefix", ["How does the specified TLS", "Is TLS_RSA_WITH_AES_128_CBC_SHA"]
)
def test_tls_paraphrases_keep_mandatory_and_removed_in_their_respective_versions(records, prefix):
    """Keep cipher-suite requirements tied to their respective TLS versions."""
    answer = _question(records, prefix)["expected_answer"]
    assert re.search(r"RFC 5246.*TLS_RSA_WITH_AES_128_CBC_SHA.*mandatory.*TLS 1\.2", answer)
    assert re.search(r"RFC 8446.*removes static RSA.*TLS 1\.3", answer)
    assert re.search(r"different protocol versions|protocol-version change", answer)
