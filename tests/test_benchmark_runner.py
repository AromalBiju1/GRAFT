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
    ABLATION_SETUPS,
    EXIT_DATASET_ERROR,
    EXIT_OK,
    METRIC_KEYS,
    REFERENCE_SETUP,
    SETUP_NAMES,
    DatasetFormatError,
    aggregate_setups,
    compute_deltas,
    format_ablation_summary,
    get_setup,
    load_dataset,
    main,
    run_ablation,
    run_benchmark,
    run_setup,
    token_f1,
)
from retrieval import MAX_RETRIEVAL_DEPTH
from router import AVAILABLE_MODULES

REPO_ROOT = Path(__file__).resolve().parents[1]

SIMPLE_Q = "What is the default port?"
COMPLEX_Q = "Compare the TLS 1.2 and TLS 1.3 handshakes and explain every difference in detail"
CONTRADICTION_Q = "Do the release notes conflict with the outdated spec? Explain the discrepancy"

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


@pytest.fixture
def ablation_dataset(tmp_path: Path) -> Path:
    """Three questions covering every routing signal an ablation can switch off.

    The conflict hunt matters: the router only activates
    ``contradiction_detection`` for a non-simple query that mentions a
    conflict, so without it ``no_contradiction`` would be a no-op and the test
    could not tell a working ablation from a broken one.
    """
    return write_jsonl(
        tmp_path / "ablation.jsonl",
        [
            {
                "query": SIMPLE_Q,
                "relevant_chunk_ids": ["c1"],
                "expected_answer": "The default port is 443.",
            },
            {
                "query": COMPLEX_Q,
                "relevant_chunk_ids": ["c2"],
                "expected_answer": "TLS 1.3 removes static RSA key exchange.",
            },
            {
                "query": CONTRADICTION_Q,
                "relevant_chunk_ids": ["c2"],
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


# --- ablation: setup registry ----------------------------------------------- #
class TestAblationSetups:
    def test_registry_covers_every_setup_in_the_issue(self) -> None:
        assert SETUP_NAMES == (
            "graft",
            "no_gating",
            "depth_always_0",
            "all_modules",
            "no_contradiction",
            "no_multi_hop",
        )
        assert [s.name for s in ABLATION_SETUPS] == list(SETUP_NAMES)

    def test_every_setup_has_a_description_for_the_report(self) -> None:
        for setup in ABLATION_SETUPS:
            assert setup.description.strip(), setup.name
            assert setup.to_dict()["description"] == setup.description

    def test_reference_setup_is_the_unmodified_pipeline(self) -> None:
        """``graft`` must not secretly ablate anything, or deltas are meaningless."""
        graft = get_setup(REFERENCE_SETUP)
        assert graft.retrieval_depth is None
        assert not graft.disabled_modules
        assert graft.force_all_modules is False

    def test_each_setup_disables_at_most_one_component(self) -> None:
        """Two changes at once would make the delta uninterpretable."""
        for setup in ABLATION_SETUPS[1:]:
            switches = [
                setup.retrieval_depth is not None,
                bool(setup.disabled_modules),
                setup.force_all_modules,
            ]
            assert sum(switches) == 1, setup.name

    def test_get_setup_rejects_unknown_names_and_lists_valid_ones(self) -> None:
        with pytest.raises(KeyError) as exc:
            get_setup("no_such_setup")
        assert "no_such_setup" in str(exc.value)
        assert "no_multi_hop" in str(exc.value)


# --- ablation: each setup changes exactly its own component ---------------- #
class TestAblationBehaviour:
    def test_graft_matches_the_unmodified_pipeline(self, ablation_dataset: Path) -> None:
        """setup=None and setup=graft must produce identical measurements."""
        records = load_dataset(ablation_dataset)
        reference = run_setup(
            records, get_setup("graft"), embedder=fake_embedder, retriever=fake_retriever
        )
        unablated = run_benchmark(records, embedder=fake_embedder, retriever=fake_retriever)
        for row, query in zip(reference, unablated["per_query"], strict=True):
            graft_row = query["graft"]
            assert row["retrieval_depth"] == graft_row["retrieval_depth"]
            assert row["modules"] == graft_row["modules"]
            assert row["accuracy_f1"] == graft_row["accuracy_f1"]
            assert row["precision_at_5"] == graft_row["precision_at_5"]
            assert row["modules_fired"] == graft_row["modules_fired"]

    def test_no_gating_always_searches_the_deepest_level(self, ablation_dataset: Path) -> None:
        rows = run_setup(
            load_dataset(ablation_dataset),
            get_setup("no_gating"),
            embedder=fake_embedder,
            retriever=fake_retriever,
        )
        assert [r["retrieval_depth"] for r in rows] == [MAX_RETRIEVAL_DEPTH] * 3
        # And it really differs from graft, which leaves a simple query at the leaves.
        graft = run_setup(
            load_dataset(ablation_dataset),
            get_setup("graft"),
            embedder=fake_embedder,
            retriever=fake_retriever,
        )
        assert graft[0]["retrieval_depth"] == 0

    def test_depth_always_0_searches_only_raw_chunks(self, ablation_dataset: Path) -> None:
        rows = run_setup(
            load_dataset(ablation_dataset),
            get_setup("depth_always_0"),
            embedder=fake_embedder,
            retriever=fake_retriever,
        )
        assert [r["retrieval_depth"] for r in rows] == [0, 0, 0]

    def test_all_modules_runs_every_module_regardless_of_routing(
        self, ablation_dataset: Path
    ) -> None:
        records = load_dataset(ablation_dataset)
        rows = run_setup(
            records, get_setup("all_modules"), embedder=fake_embedder, retriever=fake_retriever
        )
        assert [r["modules_fired"] for r in rows] == [len(AVAILABLE_MODULES)] * 3
        assert all(set(r["modules"]) == set(AVAILABLE_MODULES) for r in rows)
        # Even the simple lookup the router would have gated down to one module.
        graft = run_setup(
            records, get_setup("graft"), embedder=fake_embedder, retriever=fake_retriever
        )
        assert graft[0]["modules_fired"] == 1

    @pytest.mark.parametrize(
        ("setup_name", "absent_module"),
        [("no_contradiction", "contradiction_detection"), ("no_multi_hop", "multi_hop")],
    )
    def test_disabled_module_never_runs(
        self, ablation_dataset: Path, setup_name: str, absent_module: str
    ) -> None:
        records = load_dataset(ablation_dataset)
        rows = run_setup(
            records, get_setup(setup_name), embedder=fake_embedder, retriever=fake_retriever
        )
        assert all(absent_module not in r["modules"] for r in rows)
        # Every other module the router picked must still run, so the ablation
        # removes one component rather than silently emptying the pipeline.
        graft = run_setup(
            records, get_setup("graft"), embedder=fake_embedder, retriever=fake_retriever
        )
        for row, graft_row in zip(rows, graft, strict=True):
            assert set(row["modules"]) == set(graft_row["modules"]) - {absent_module}

    def test_disabling_a_routed_module_actually_reduces_the_count(
        self, ablation_dataset: Path
    ) -> None:
        """Guards the fixture: the router must route contradiction for these queries."""
        records = load_dataset(ablation_dataset)
        graft = run_setup(
            records, get_setup("graft"), embedder=fake_embedder, retriever=fake_retriever
        )
        assert any("contradiction_detection" in r["modules"] for r in graft)
        assert any("multi_hop" in r["modules"] for r in graft)

    def test_every_setup_runs_on_a_two_question_dataset(self, two_query_dataset: Path) -> None:
        """The acceptance criterion: a tiny set exercises every configuration."""
        records = load_dataset(two_query_dataset)
        for setup in ABLATION_SETUPS:
            rows = run_setup(records, setup, embedder=fake_embedder, retriever=fake_retriever)
            assert len(rows) == 2, setup.name
            assert all(set(METRIC_KEYS) <= set(row) for row in rows), setup.name
            assert all(row["setup"] == setup.name for row in rows), setup.name


# --- ablation: aggregation and deltas --------------------------------------- #
class TestComputeDeltas:
    def test_reference_deltas_are_all_zero(self) -> None:
        agg = {
            "graft": dict.fromkeys(METRIC_KEYS, 0.5),
            "all_modules": dict.fromkeys(METRIC_KEYS, 0.9),
        }
        assert compute_deltas(agg)["graft"] == dict.fromkeys(METRIC_KEYS, 0.0)

    def test_delta_is_setup_minus_reference(self) -> None:
        agg = {
            "graft": {
                "accuracy_f1": 0.8000,
                "latency_ms": 12.0000,
                "modules_fired": 2.0000,
                "precision_at_5": 0.6000,
            },
            "all_modules": {
                "accuracy_f1": 0.7000,
                "latency_ms": 30.5000,
                "modules_fired": 4.0000,
                "precision_at_5": 0.6000,
            },
        }
        assert compute_deltas(agg)["all_modules"] == {
            "accuracy_f1": -0.1,
            "latency_ms": 18.5,
            "modules_fired": 2.0,
            "precision_at_5": 0.0,
        }

    def test_sign_shows_which_component_carried_the_metric(self) -> None:
        """A negative accuracy delta means removing the component hurt accuracy."""
        agg = {
            "graft": dict.fromkeys(METRIC_KEYS, 1.0),
            "no_multi_hop": dict.fromkeys(METRIC_KEYS, 0.25),
        }
        assert compute_deltas(agg)["no_multi_hop"]["accuracy_f1"] == -0.75

    def test_rounds_to_four_decimals_like_every_other_metric(self) -> None:
        agg = {
            "graft": dict.fromkeys(METRIC_KEYS, 0.0),
            "depth_always_0": {
                "accuracy_f1": 1 / 3,
                "latency_ms": 0.0,
                "modules_fired": 0.0,
                "precision_at_5": 0.6666666,
            },
        }
        deltas = compute_deltas(agg)["depth_always_0"]
        assert deltas["accuracy_f1"] == 0.3333
        assert deltas["precision_at_5"] == 0.6667

    def test_missing_reference_is_a_key_error(self) -> None:
        with pytest.raises(KeyError):
            compute_deltas({"all_modules": dict.fromkeys(METRIC_KEYS, 1.0)})


class TestAggregateSetups:
    def test_means_each_metric_per_setup(self) -> None:
        rows = {
            "graft": [
                {
                    "accuracy_f1": 1.0,
                    "latency_ms": 10.0,
                    "modules_fired": 1.0,
                    "precision_at_5": 0.5,
                },
                {
                    "accuracy_f1": 0.0,
                    "latency_ms": 20.0,
                    "modules_fired": 3.0,
                    "precision_at_5": 1.0,
                },
            ],
        }
        assert aggregate_setups(rows)["graft"] == {
            "accuracy_f1": 0.5,
            "latency_ms": 15.0,
            "modules_fired": 2.0,
            "precision_at_5": 0.75,
        }

    def test_a_setup_with_no_rows_scores_zero_not_an_error(self) -> None:
        assert aggregate_setups({"graft": []})["graft"] == dict.fromkeys(METRIC_KEYS, 0.0)


# --- ablation: end to end --------------------------------------------------- #
class TestRunAblation:
    def test_reports_all_four_metrics_for_every_setup(self, ablation_dataset: Path) -> None:
        results = run_ablation(
            load_dataset(ablation_dataset), embedder=fake_embedder, retriever=fake_retriever
        )
        assert results["mode"] == "ablation"
        assert results["n_queries"] == 3
        assert set(results["aggregate"]) == set(SETUP_NAMES)
        for name in SETUP_NAMES:
            assert set(results["aggregate"][name]) == set(METRIC_KEYS), name
            assert set(results["deltas"][name]) == set(METRIC_KEYS), name

    def test_aggregate_is_the_mean_of_the_per_query_rows(self, ablation_dataset: Path) -> None:
        results = run_ablation(
            load_dataset(ablation_dataset), embedder=fake_embedder, retriever=fake_retriever
        )
        for name in SETUP_NAMES:
            rows = [q["setups"][name] for q in results["per_query"]]
            for key in METRIC_KEYS:
                if key == "latency_ms":
                    continue  # timing, not a value to reproduce exactly
                expected = round(sum(r[key] for r in rows) / len(rows), 4)
                assert results["aggregate"][name][key] == pytest.approx(expected), (name, key)

    def test_every_measurement_is_labelled_with_its_setup(self, ablation_dataset: Path) -> None:
        results = run_ablation(
            load_dataset(ablation_dataset), embedder=fake_embedder, retriever=fake_retriever
        )
        for query in results["per_query"]:
            assert set(query["setups"]) == set(SETUP_NAMES)
            for name, row in query["setups"].items():
                assert row["setup"] == name
        # The saved definitions name each setup too, so a result file read later
        # says which configuration produced which number.
        for name in SETUP_NAMES:
            assert results["setups"][name]["name"] == name
            assert results["setups"][name]["description"].strip()
        assert results["reference_setup"] == REFERENCE_SETUP

    def test_graft_reference_row_has_zero_delta(self, ablation_dataset: Path) -> None:
        results = run_ablation(
            load_dataset(ablation_dataset), embedder=fake_embedder, retriever=fake_retriever
        )
        assert results["deltas"][REFERENCE_SETUP] == dict.fromkeys(METRIC_KEYS, 0.0)

    def test_deltas_track_the_measured_aggregates(self, ablation_dataset: Path) -> None:
        results = run_ablation(
            load_dataset(ablation_dataset), embedder=fake_embedder, retriever=fake_retriever
        )
        reference = results["aggregate"][REFERENCE_SETUP]
        for name, deltas in results["deltas"].items():
            for key in METRIC_KEYS:
                expected = results["aggregate"][name][key] - reference[key]
                assert deltas[key] == pytest.approx(expected, abs=1e-4), (name, key)

    def test_all_questions_use_the_same_embedder_and_seed(self, ablation_dataset: Path) -> None:
        """Only the component under test may differ between setups."""
        seen: list[str] = []

        def recording_embedder(text: str) -> list[float]:
            if text != "warm-up":
                seen.append(text)
            return fake_embedder(text)

        results = run_ablation(
            load_dataset(ablation_dataset), embedder=recording_embedder, retriever=fake_retriever
        )
        n_setups = len(SETUP_NAMES)
        n_queries = results["n_queries"]
        # Every setup asks every question exactly once -- no setup skips or repeats.
        assert seen == [SIMPLE_Q, COMPLEX_Q, CONTRADICTION_Q] * n_setups
        assert len(seen) == n_setups * n_queries

    def test_retrieval_size_is_held_constant_across_setups(self, ablation_dataset: Path) -> None:
        """Only depth may vary between setups; n_results must not drift."""
        calls: list[dict[str, Any]] = []

        def spy(embedding: list[float], **kwargs: Any) -> list[dict[str, Any]]:
            calls.append(kwargs)
            return fake_retriever(embedding, **kwargs)

        run_ablation(load_dataset(ablation_dataset), embedder=fake_embedder, retriever=spy)
        assert len(calls) == len(SETUP_NAMES) * 3
        assert {c["n_results"] for c in calls} == {5}
        # Depths are limited to the levels the three setups are allowed to pin.
        assert {c["retrieval_depth"] for c in calls} <= {0, 1, MAX_RETRIEVAL_DEPTH}

    def test_graft_aggregate_is_comparable_with_run_benchmark(self, ablation_dataset: Path) -> None:
        """Ablation must not quietly redefine the metrics."""
        records = load_dataset(ablation_dataset)
        ablated = run_ablation(records, embedder=fake_embedder, retriever=fake_retriever)
        plain = run_benchmark(records, embedder=fake_embedder, retriever=fake_retriever)
        for key in METRIC_KEYS:
            if key == "latency_ms":
                continue
            assert ablated["aggregate"][REFERENCE_SETUP][key] == plain["aggregate"]["graft"][key]

    def test_seed_is_recorded_for_the_whole_run(self, ablation_dataset: Path) -> None:
        results = run_ablation(
            load_dataset(ablation_dataset),
            embedder=fake_embedder,
            retriever=fake_retriever,
            seed=7,
        )
        assert results["seed"] == 7

    def test_accepts_a_subset_that_includes_the_reference(self, two_query_dataset: Path) -> None:
        subset = [get_setup(n) for n in ("graft", "no_multi_hop")]
        results = run_ablation(
            load_dataset(two_query_dataset),
            subset,
            embedder=fake_embedder,
            retriever=fake_retriever,
        )
        assert list(results["aggregate"]) == ["graft", "no_multi_hop"]
        assert results["deltas"]["no_multi_hop"]["modules_fired"] < 0

    def test_rejects_unusable_setup_lists(self, two_query_dataset: Path) -> None:
        records = load_dataset(two_query_dataset)
        with pytest.raises(ValueError, match="must not be empty"):
            run_ablation(records, [], embedder=fake_embedder, retriever=fake_retriever)
        with pytest.raises(ValueError, match="must include the reference setup"):
            run_ablation(
                records,
                [get_setup("no_multi_hop")],
                embedder=fake_embedder,
                retriever=fake_retriever,
            )
        with pytest.raises(ValueError, match="unique"):
            run_ablation(
                records,
                [get_setup("graft"), get_setup("graft")],
                embedder=fake_embedder,
                retriever=fake_retriever,
            )

    def test_a_broken_setup_propagates_instead_of_returning_partial_results(
        self, two_query_dataset: Path
    ) -> None:
        def exploding_retriever(embedding: list[float], **kwargs: Any) -> list[dict[str, Any]]:
            raise RuntimeError("vector store is down")

        with pytest.raises(RuntimeError, match="vector store is down"):
            run_ablation(
                load_dataset(two_query_dataset),
                [get_setup("graft"), get_setup("no_multi_hop")],
                embedder=fake_embedder,
                retriever=exploding_retriever,
            )


# --- ablation: console report ----------------------------------------------- #
def delta_table_rows(summary: str) -> list[str]:
    """Data rows of the second table, i.e. the delta-vs-graft one.

    Setup names appear in both tables, so a test that wants the delta row has
    to look past the "Change vs graft" heading rather than match the first hit.
    """
    _, marker, delta_table = summary.partition(f"Change vs {REFERENCE_SETUP}")
    assert marker, "summary has no delta table"
    _, _, body = delta_table.partition("\n")  # drop the tail of the heading line
    return [
        line for line in body.splitlines() if line.strip() and not line.startswith(("Setup", "-"))
    ]


class TestFormatAblationSummary:
    @pytest.fixture
    def summary(self, ablation_dataset: Path) -> str:
        results = run_ablation(
            load_dataset(ablation_dataset), embedder=fake_embedder, retriever=fake_retriever
        )
        return format_ablation_summary(results)

    def test_prints_every_setup_and_both_tables(self, summary: str) -> None:
        for name in SETUP_NAMES:
            assert name in summary, name
        assert "Accuracy" in summary
        assert "Latency ms" in summary
        assert "Modules" in summary
        assert "P@5" in summary
        assert f"Change vs {REFERENCE_SETUP}" in summary
        assert "dAccuracy" in summary and "dModules" in summary
        assert len(delta_table_rows(summary)) == len(SETUP_NAMES)

    def test_every_setup_lists_what_it_changed(self, summary: str) -> None:
        for setup in ABLATION_SETUPS:
            assert setup.description in summary, setup.name

    def test_reference_row_shows_signed_zero_deltas(self, summary: str) -> None:
        line = next(ln for ln in delta_table_rows(summary) if ln.startswith(f"{REFERENCE_SETUP} "))
        # Signed so a column of deltas reads as a signed number, not a magnitude.
        assert line.count("+0.") == 4

    def test_deltas_are_rendered_with_a_sign(self, ablation_dataset: Path) -> None:
        results = run_ablation(
            load_dataset(ablation_dataset), embedder=fake_embedder, retriever=fake_retriever
        )
        # all_modules runs strictly more modules than graft, so its delta is positive.
        assert results["deltas"]["all_modules"]["modules_fired"] > 0
        line = next(
            ln
            for ln in delta_table_rows(format_ablation_summary(results))
            if ln.startswith("all_modules ")
        )
        assert "+" in line
        assert line.split()[-1].startswith("+")


# --- ablation: CLI ----------------------------------------------------------- #
class TestAblationCli:
    def test_ablation_flag_writes_every_setup_and_prints_both_tables(
        self, two_query_dataset: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        out = tmp_path / "nested" / "results.json"
        code = main(
            ["--dataset", str(two_query_dataset), "--output", str(out), "--ablation"],
            embedder=fake_embedder,
            retriever=fake_retriever,
        )
        assert code == EXIT_OK

        data = json.loads(out.read_text())
        assert data["mode"] == "ablation"
        assert set(data["aggregate"]) == set(SETUP_NAMES)
        assert set(data["deltas"]) == set(SETUP_NAMES)
        for name in SETUP_NAMES:
            assert set(data["aggregate"][name]) == set(METRIC_KEYS), name
            assert data["setups"][name]["name"] == name

        stdout = capsys.readouterr().out
        for name in SETUP_NAMES:
            assert name in stdout, name
        assert f"Change vs {REFERENCE_SETUP}" in stdout
        assert "Baseline" not in stdout  # ablation mode replaces the baseline table

    def test_results_are_written_to_the_existing_output_file(
        self, two_query_dataset: Path, tmp_path: Path
    ) -> None:
        out = tmp_path / "results.json"
        main(
            ["--dataset", str(two_query_dataset), "--output", str(out), "--ablation"],
            embedder=fake_embedder,
            retriever=fake_retriever,
        )
        assert json.loads(out.read_text())["n_queries"] == 2

    def test_seed_is_forwarded_to_the_run(self, two_query_dataset: Path, tmp_path: Path) -> None:
        out = tmp_path / "results.json"
        main(
            [
                "--dataset",
                str(two_query_dataset),
                "--output",
                str(out),
                "--ablation",
                "--seed",
                "11",
            ],
            embedder=fake_embedder,
            retriever=fake_retriever,
        )
        assert json.loads(out.read_text())["seed"] == 11

    def test_default_mode_still_compares_graft_with_baseline(
        self, two_query_dataset: Path, tmp_path: Path
    ) -> None:
        """Regression guard: --ablation must not hijack the default path."""
        out = tmp_path / "results.json"
        main(
            ["--dataset", str(two_query_dataset), "--output", str(out)],
            embedder=fake_embedder,
            retriever=fake_retriever,
        )
        data = json.loads(out.read_text())
        assert "mode" not in data
        assert set(data["aggregate"]) == {"graft", "baseline"}

    def test_malformed_dataset_still_exits_1_in_ablation_mode(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        bad = write_jsonl(tmp_path / "bad.jsonl", ["{oops"])
        out = tmp_path / "results.json"
        code = main(
            ["--dataset", str(bad), "--output", str(out), "--ablation"],
            embedder=fake_embedder,
            retriever=fake_retriever,
        )
        assert code == EXIT_DATASET_ERROR == 1
        assert f"{bad}:1:" in capsys.readouterr().err
        assert not out.exists()
