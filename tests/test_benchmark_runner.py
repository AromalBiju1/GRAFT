"""Tests for benchmark.runner. Fully offline: injected embedder + retriever."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import baseline as baseline_module
from benchmark.runner import (
    EXIT_DATASET_ERROR,
    EXIT_OK,
    METRIC_KEYS,
    DatasetFormatError,
    load_dataset,
    main,
    run_benchmark,
    token_f1,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

SIMPLE_Q = "What is the default port?"
COMPLEX_Q = "Compare the TLS 1.2 and TLS 1.3 handshakes and explain every difference in detail"

CHUNKS = [
    {"chunk_id": "c1", "text": "The default port is 443.", "score": 0.9, "metadata": {}},
    {
        "chunk_id": "c2",
        "text": "TLS 1.3 removes static RSA key exchange.",
        "score": 0.7,
        "metadata": {},
    },
    {"chunk_id": "c3", "text": "Unrelated filler text.", "score": 0.1, "metadata": {}},
]


def fake_embedder(text: str) -> list[float]:
    return [float(len(text)), 1.0, 0.0]


def fake_retriever(embedding: list[float], **kwargs: Any) -> list[dict[str, Any]]:
    n = kwargs.get("n_results", 5)
    return [dict(c) for c in CHUNKS[:n]]


def write_jsonl(path: Path, rows: list[Any]) -> Path:
    path.write_text("\n".join(r if isinstance(r, str) else json.dumps(r) for r in rows) + "\n")
    return path


@pytest.fixture
def two_query_dataset(tmp_path: Path) -> Path:
    return write_jsonl(
        tmp_path / "bench.jsonl",
        [
            {
                "query": SIMPLE_Q,
                "relevant_chunk_ids": ["c1"],
                "expected_answer": "The default port is 443.",
            },
            {
                "query": COMPLEX_Q,
                "relevant_chunk_ids": ["c2", "c9"],
                "expected_answer": "TLS 1.3 removes static RSA key exchange.",
            },
        ],
    )


# --- dataset loading -------------------------------------------------------- #
class TestLoadDataset:
    def test_loads_valid_file_and_skips_blank_lines(self, tmp_path: Path) -> None:
        row = {"query": "q", "relevant_chunk_ids": ["a"], "expected_answer": "x", "tier": "simple"}
        p = tmp_path / "d.jsonl"
        p.write_text(json.dumps(row) + "\n\n" + json.dumps(row) + "\n")
        records = load_dataset(p)
        assert [r.line_number for r in records] == [1, 3]

    @pytest.mark.parametrize(
        ("bad_line", "fragment"),
        [
            ("{not json", "invalid JSON"),
            ("[1, 2]", "expected a JSON object"),
            (
                '{"query": "q", "expected_answer": "a"}',
                "missing required field 'relevant_chunk_ids'",
            ),
            (
                '{"query": 5, "relevant_chunk_ids": ["a"], "expected_answer": "a"}',
                "'query' must be str",
            ),
            (
                '{"query": "q", "relevant_chunk_ids": [], "expected_answer": "a"}',
                "must be non-empty",
            ),
            (
                '{"query": "q", "relevant_chunk_ids": [1], "expected_answer": "a"}',
                "only non-empty strings",
            ),
            (
                '{"query": " ", "relevant_chunk_ids": ["a"], "expected_answer": "a"}',
                "'query' must be non-empty",
            ),
        ],
    )
    def test_error_names_the_line_number(
        self, tmp_path: Path, bad_line: str, fragment: str
    ) -> None:
        good = json.dumps({"query": "q", "relevant_chunk_ids": ["a"], "expected_answer": "a"})
        p = write_jsonl(tmp_path / "d.jsonl", [good, bad_line])
        with pytest.raises(DatasetFormatError) as exc:
            load_dataset(p)
        assert f"{p}:2:" in str(exc.value)
        assert fragment in str(exc.value)

    def test_missing_and_empty_files_are_format_errors(self, tmp_path: Path) -> None:
        with pytest.raises(DatasetFormatError):
            load_dataset(tmp_path / "nope.jsonl")
        empty = tmp_path / "empty.jsonl"
        empty.write_text("\n\n")
        with pytest.raises(DatasetFormatError, match="no records"):
            load_dataset(empty)


# --- metrics ---------------------------------------------------------------- #
class TestTokenF1:
    def test_identical_is_one(self) -> None:
        assert token_f1("The port is 443", "the PORT is 443!") == 1.0

    def test_disjoint_is_zero(self) -> None:
        assert token_f1("alpha beta", "gamma delta") == 0.0

    def test_partial_overlap(self) -> None:
        # pred 4 tokens, ref 2 tokens, overlap 2 -> P=0.5, R=1.0 -> F1=2/3
        assert token_f1("a b c d", "a b") == pytest.approx(2 / 3)

    def test_repeated_tokens_use_multiset_overlap(self) -> None:
        assert token_f1("a a a", "a") == pytest.approx(0.5)

    def test_empty_handling(self) -> None:
        assert token_f1("", "") == 1.0
        assert token_f1("", "x") == 0.0


# --- end to end ------------------------------------------------------------- #
class TestRunBenchmark:
    def test_two_query_fixture_produces_all_four_metrics(self, two_query_dataset: Path) -> None:
        results = run_benchmark(
            load_dataset(two_query_dataset), embedder=fake_embedder, retriever=fake_retriever
        )
        assert results["n_queries"] == 2
        for row in results["per_query"]:
            for system in ("graft", "baseline"):
                assert set(METRIC_KEYS) <= set(row[system]), (system, row[system])
        for system in ("graft", "baseline"):
            assert set(results["aggregate"][system]) == set(METRIC_KEYS)

    def test_metric_values(self, two_query_dataset: Path) -> None:
        results = run_benchmark(
            load_dataset(two_query_dataset), embedder=fake_embedder, retriever=fake_retriever
        )
        q1, q2 = results["per_query"]

        # Baseline never gates: all four modules fire for both queries.
        assert q1["baseline"]["modules_fired"] == q2["baseline"]["modules_fired"] == 4
        # GRAFT gates: the simple query pays for fact_lookup only.
        assert q1["graft"]["modules_fired"] == 1
        assert q2["graft"]["modules_fired"] > q1["graft"]["modules_fired"]

        # fact_lookup answers with the top chunk, which is exactly expected_answer for q1.
        assert q1["graft"]["accuracy_f1"] == 1.0
        # 1 of the 3 retrieved chunks is relevant -> 1/min(5, 3).
        assert q1["graft"]["precision_at_5"] == pytest.approx(1 / 3, abs=1e-4)
        assert q1["baseline"]["precision_at_5"] == pytest.approx(1 / 3, abs=1e-4)

        assert q1["graft"]["latency_ms"] >= 0 and q1["baseline"]["latency_ms"] >= 0

    def test_graft_retrieves_with_router_depth_baseline_does_not(
        self, two_query_dataset: Path
    ) -> None:
        calls: list[dict[str, Any]] = []

        def spy(embedding: list[float], **kwargs: Any) -> list[dict[str, Any]]:
            calls.append(kwargs)
            return fake_retriever(embedding, **kwargs)

        run_benchmark(load_dataset(two_query_dataset), embedder=fake_embedder, retriever=spy)
        graft_calls = [c for c in calls if "retrieval_depth" in c]
        baseline_calls = [c for c in calls if "retrieval_depth" not in c]
        assert [c["retrieval_depth"] for c in graft_calls] == [0, 2]
        assert len(baseline_calls) == 2

    def test_injected_retriever_is_restored(self, two_query_dataset: Path) -> None:
        before = baseline_module.retrieve
        run_benchmark(
            load_dataset(two_query_dataset), embedder=fake_embedder, retriever=fake_retriever
        )
        assert baseline_module.retrieve is before

    def test_same_seed_gives_same_metrics(self, two_query_dataset: Path) -> None:
        def stable(r: dict[str, Any]) -> list[Any]:
            return [
                {
                    s: {k: q[s][k] for k in METRIC_KEYS if k != "latency_ms"}
                    for s in ("graft", "baseline")
                }
                for q in r["per_query"]
            ]

        recs = load_dataset(two_query_dataset)
        a = run_benchmark(recs, embedder=fake_embedder, retriever=fake_retriever, seed=7)
        b = run_benchmark(recs, embedder=fake_embedder, retriever=fake_retriever, seed=7)
        assert stable(a) == stable(b)
        assert a["seed"] == 7


# --- CLI -------------------------------------------------------------------- #
class TestCli:
    def test_malformed_dataset_exits_1(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        bad = write_jsonl(tmp_path / "bad.jsonl", ["{oops"])
        out = tmp_path / "results.json"
        code = main(["--dataset", str(bad), "--output", str(out)])
        assert code == EXIT_DATASET_ERROR == 1
        assert f"{bad}:1:" in capsys.readouterr().err
        assert not out.exists()

    def test_malformed_dataset_exits_1_via_python_dash_m(self, tmp_path: Path) -> None:
        bad = write_jsonl(tmp_path / "bad.jsonl", ['{"query": "q"}'])
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "benchmark.runner",
                "--dataset",
                str(bad),
                "--output",
                str(tmp_path / "r.json"),
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert proc.returncode == 1
        assert "missing required field" in proc.stderr

    def test_success_writes_json_and_prints_table(
        self, two_query_dataset: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        out = tmp_path / "nested" / "results.json"
        code = main(
            ["--dataset", str(two_query_dataset), "--output", str(out)],
            embedder=fake_embedder,
            retriever=fake_retriever,
        )
        assert code == EXIT_OK == 0
        data = json.loads(out.read_text())
        assert data["n_queries"] == 2 and data["seed"] == 42
        assert set(data["metric_definitions"]) == set(METRIC_KEYS)
        stdout = capsys.readouterr().out
        for label in (
            "Accuracy",
            "Avg latency",
            "Modules fired",
            "precision@5",
            "GRAFT",
            "Baseline",
        ):
            assert label in stdout
