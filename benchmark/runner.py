"""Benchmark runner — GRAFT (gated) vs flat baseline on a JSONL query set.

Usage::

    python3 -m benchmark.runner \\
        --dataset data/sample_docs/benchmark.jsonl \\
        --output results.json

Dataset format (one JSON object per line)::

    {"query": "...", "relevant_chunk_ids": ["<sha256>", ...], "expected_answer": "..."}

Both systems answer every query, and each is scored with the same four
metrics. The definitions live here, in :data:`METRIC_DEFINITIONS`, and are
copied into the results file so a number in the report can always be traced
back to its exact definition:

``accuracy_f1``
    Token-overlap F1 between the generated answer and ``expected_answer``
    (:func:`token_f1`). Lower-cased ``\\w+`` tokens, multiset overlap.
``latency_ms``
    Wall-clock time from the query string to the final answer dict. Covers
    query embedding *and* the full pipeline for **both** paths:
    ``embed -> route -> retrieve -> activated modules -> generate`` for GRAFT
    and ``embed -> run_baseline`` for the baseline. Dataset loading and the
    one-off embedder warm-up are excluded.
``modules_fired``
    Number of specialist modules executed for the query
    (:func:`benchmark.modules_fired_count`). GRAFT fires only the modules the
    router activates; the baseline always fires all four.
``precision_at_5``
    :func:`benchmark.retrieval_precision_at_k` with ``k=5`` over the retrieved
    chunks' ``chunk_id`` against ``relevant_chunk_ids``. Summary/root nodes
    carry their own ids, so they count as misses -- ``relevant_chunk_ids``
    are leaf-chunk ids (SHA-256 of ``f"{document_id}:{chunk_index}"``).

Both ends are injectable (``embedder``, ``retriever``) so CI runs with no
network and no vector store. Exit codes: 0 success, 1 dataset format error.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmark import measure_latency, modules_fired_count, retrieval_precision_at_k
from generation import synthesize
from modules.base import BaseModule, ModuleResult
from modules.contradiction_detection import ContradictionDetectionModule
from modules.fact_lookup import FactLookupModule
from modules.multi_hop import MultiHopModule
from modules.numeric_reasoning import NumericReasoningModule
from router import route

#: Default RNG seed. Recorded in the results file.
DEFAULT_SEED = 42
#: k for retrieval precision.
PRECISION_K = 5
#: Chunks the GRAFT path asks the retriever for (matches ``POST /query``).
GRAFT_N_RESULTS = 5
#: Exit codes.
EXIT_OK = 0
EXIT_DATASET_ERROR = 1

REQUIRED_FIELDS: dict[str, type] = {
    "query": str,
    "relevant_chunk_ids": list,
    "expected_answer": str,
}

METRIC_DEFINITIONS: dict[str, str] = {
    "accuracy_f1": "Token-overlap F1 of answer vs expected_answer (lower-cased \\w+ tokens, "
    "multiset overlap).",
    "latency_ms": "Wall-clock ms from query string to answer dict, including query embedding "
    "(excludes dataset load and embedder warm-up).",
    "modules_fired": "Number of specialist modules executed for the query.",
    "precision_at_5": "Hits in top-5 retrieved chunk_ids / min(5, retrieved), against "
    "relevant_chunk_ids.",
}

METRIC_KEYS: tuple[str, ...] = ("accuracy_f1", "latency_ms", "modules_fired", "precision_at_5")

Embedder = Callable[[str], list[float]]
#: Same call shape as :func:`retrieval.retrieve`.
Retriever = Callable[..., list[dict[str, Any]]]


class DatasetFormatError(ValueError):
    """Raised for a malformed dataset; the message names the offending line."""


@dataclass(frozen=True, slots=True)
class BenchmarkRecord:
    line_number: int
    query: str
    relevant_chunk_ids: tuple[str, ...]
    expected_answer: str


# --------------------------------------------------------------------------- #
# Dataset loading
# --------------------------------------------------------------------------- #
def load_dataset(path: Path | str) -> list[BenchmarkRecord]:
    """Read a UTF-8 JSONL benchmark file into records with source line numbers.

    Blank lines are skipped. Unknown extra keys are allowed (e.g. a ``tier``
    tag) and ignored. Each object must have nonblank ``query`` and
    ``expected_answer`` strings and a nonempty ``relevant_chunk_ids`` list
    of nonblank strings. String values are preserved without trimming.

    Raise :class:`DatasetFormatError` for read failures, invalid records, or
    a file with no records. Record errors include ``<path>:<line>:``; read
    failures and empty files include only the path. Invalid UTF-8 raises
    :class:`UnicodeDecodeError` unchanged.
    """
    p = Path(path)
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise DatasetFormatError(f"{p}: cannot read dataset ({exc})") from exc

    records: list[BenchmarkRecord] = []
    for lineno, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        where = f"{p}:{lineno}"
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DatasetFormatError(
                f"{where}: invalid JSON ({exc.msg} at column {exc.colno})"
            ) from exc
        if not isinstance(obj, dict):
            raise DatasetFormatError(f"{where}: expected a JSON object, got {type(obj).__name__}")

        for key, expected_type in REQUIRED_FIELDS.items():
            if key not in obj:
                raise DatasetFormatError(f"{where}: missing required field {key!r}")
            if not isinstance(obj[key], expected_type):
                raise DatasetFormatError(
                    f"{where}: field {key!r} must be {expected_type.__name__}, "
                    f"got {type(obj[key]).__name__}"
                )
        if not obj["query"].strip():
            raise DatasetFormatError(f"{where}: field 'query' must be non-empty")
        if not obj["expected_answer"].strip():
            raise DatasetFormatError(f"{where}: field 'expected_answer' must be non-empty")
        ids = obj["relevant_chunk_ids"]
        if not ids:
            raise DatasetFormatError(f"{where}: field 'relevant_chunk_ids' must be non-empty")
        if not all(isinstance(i, str) and i.strip() for i in ids):
            raise DatasetFormatError(
                f"{where}: field 'relevant_chunk_ids' must contain only non-empty strings"
            )

        records.append(
            BenchmarkRecord(
                line_number=lineno,
                query=obj["query"],
                relevant_chunk_ids=tuple(ids),
                expected_answer=obj["expected_answer"],
            )
        )

    if not records:
        raise DatasetFormatError(f"{p}: dataset contains no records")
    return records


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #
def _tokens(text: str) -> list[str]:
    """Return lowercase runs of Unicode alphanumeric characters and underscores."""
    return re.findall(r"\w+", text.lower())


def token_f1(prediction: str, reference: str) -> float:
    """Token-overlap F1 (SQuAD-style, multiset overlap, no stop-word removal).

    Tokens are lowercase runs of Unicode alphanumeric characters and underscores.
    Both token lists empty -> 1.0; exactly one empty -> 0.0.
    """
    pred, ref = _tokens(prediction), _tokens(reference)
    if not pred and not ref:
        return 1.0
    if not pred or not ref:
        return 0.0
    overlap = sum((Counter(pred) & Counter(ref)).values())
    if overlap == 0:
        return 0.0
    precision = overlap / len(pred)
    recall = overlap / len(ref)
    return 2 * precision * recall / (precision + recall)


def _score(
    answer: str,
    retrieved: list[dict[str, Any]],
    module_results: list[Any],
    record: BenchmarkRecord,
    latency_ms: float,
) -> dict[str, Any]:
    """Return the answer and its four benchmark metrics against *record*.

    Round token F1 and top-five retrieval precision to four decimal places
    and the supplied latency in milliseconds to three. Count all supplied
    module results, including results with no answer or evidence.
    """
    return {
        "answer": answer,
        "accuracy_f1": round(token_f1(answer, record.expected_answer), 4),
        "latency_ms": round(latency_ms, 3),
        "modules_fired": modules_fired_count(module_results),
        "precision_at_5": round(
            retrieval_precision_at_k(retrieved, set(record.relevant_chunk_ids), k=PRECISION_K), 4
        ),
    }


# --------------------------------------------------------------------------- #
# Pipelines
# --------------------------------------------------------------------------- #
def _module_registry() -> dict[str, BaseModule]:
    """Return fresh instances of the four specialist modules, keyed by name."""
    return {
        m.name: m
        for m in (
            FactLookupModule(),
            MultiHopModule(),
            NumericReasoningModule(),
            ContradictionDetectionModule(),
        )
    }


def run_graft(
    request_id: str,
    query: str,
    embedder: Embedder,
    retriever: Retriever,
    registry: dict[str, BaseModule],
) -> dict[str, Any]:
    """Answer a query with router-selected retrieval depth and specialist modules.

    Request five chunks using ``embedder`` and ``retriever``. Execute activated
    modules present in ``registry``; missing names are silently skipped.
    Return the synthesized ``request_id``, ``answer``, and ``evidence``, plus
    raw ``retrieval``, serialized ``module_results``, and the ``routing`` decision.

    Invalid queries, blank request IDs, and invalid router thresholds raise
    :class:`ValueError`. Errors from the supplied callbacks and modules propagate.
    """
    decision = route(query)
    retrieved = retriever(
        embedder(query),
        n_results=GRAFT_N_RESULTS,
        retrieval_depth=decision.retrieval_depth,
    )
    module_results: list[ModuleResult] = [
        registry[name](request_id, query, retrieved)
        for name in decision.activated_modules
        if name in registry
    ]
    final = synthesize(request_id, query, retrieved, module_results)
    final["retrieval"] = retrieved
    final["module_results"] = [r.to_dict() for r in module_results]
    final["routing"] = decision.to_dict()
    return final


@contextmanager
def _baseline_retriever(retriever: Retriever | None) -> Iterator[None]:
    """Temporarily swap the ``retrieve`` that :func:`baseline.run_baseline` calls.

    ``run_baseline`` has no retriever parameter, so injecting one means
    rebinding ``baseline.retrieve`` for the duration of the run.
    ``None`` leaves it unchanged. The original binding is restored on exit,
    including when the body raises; the exception propagates. This changes
    module-wide state, so overlapping uses are not isolated.
    """
    import baseline as baseline_module

    if retriever is None:
        yield
        return
    original = baseline_module.retrieve
    baseline_module.retrieve = retriever
    try:
        yield
    finally:
        baseline_module.retrieve = original


def _mean(values: list[float]) -> float:
    """Return the arithmetic mean rounded to four decimals, or 0.0 for no values."""
    return round(sum(values) / len(values), 4) if values else 0.0


def aggregate(per_query: list[dict[str, Any]], system: str) -> dict[str, float]:
    """Average each benchmark metric for ``system`` (``graft`` or ``baseline``).

    Round means to four decimals; empty input gives 0.0 for every metric.
    Missing system or metric keys in a query result raise :class:`KeyError`.
    """
    return {key: _mean([q[system][key] for q in per_query]) for key in METRIC_KEYS}


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def run_benchmark(
    records: Sequence[BenchmarkRecord],
    *,
    embedder: Embedder | None = None,
    retriever: Retriever | None = None,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    """Run GRAFT and baseline on *records* and return the results dict.

    ``embedder`` / ``retriever`` default to the real
    :func:`embeddings.embed_text` / :func:`retrieval.retrieve`. Pass fakes to
    run with no network and no vector store.

    Return the seed, query count, metric definitions, per-query scores, and
    aggregate means for both systems. Latency includes query embedding and
    answer generation but excludes the initial ``embedder("warm-up")`` call.
    Empty input still warms up the embedder and returns zero aggregate metrics.

    Reset Python's global RNG and NumPy's when available to ``seed``; their
    previous states are not restored. Temporarily replace the baseline's
    module-wide retriever, restoring it even on failure. Errors from seeding,
    warm-up, or either pipeline propagate without returning partial results.
    """
    random.seed(seed)
    try:  # numpy is optional; seed it if a downstream module pulls it in
        import numpy as np

        np.random.seed(seed)
    except ImportError:  # pragma: no cover
        pass

    if embedder is None:
        from embeddings import embed_text as embedder  # type: ignore[assignment]
    if retriever is None:
        from retrieval import retrieve as retriever  # type: ignore[assignment]
    assert embedder is not None and retriever is not None

    from baseline import run_baseline

    registry = _module_registry()
    embedder("warm-up")  # keep one-off model load out of the first query's latency

    per_query: list[dict[str, Any]] = []
    with _baseline_retriever(retriever):
        for idx, rec in enumerate(records, start=1):
            graft_out, graft_ms = measure_latency(
                run_graft, f"bench_{idx:04d}_graft", rec.query, embedder, retriever, registry
            )
            base_out, base_ms = measure_latency(
                lambda q=rec.query, i=idx: run_baseline(f"bench_{i:04d}_base", q, embedder(q))
            )
            per_query.append(
                {
                    "line_number": rec.line_number,
                    "query": rec.query,
                    "expected_answer": rec.expected_answer,
                    "relevant_chunk_ids": list(rec.relevant_chunk_ids),
                    "graft": {
                        **_score(
                            graft_out["answer"],
                            graft_out["retrieval"],
                            graft_out["module_results"],
                            rec,
                            graft_ms,
                        ),
                        "complexity": graft_out["routing"]["complexity"],
                        "retrieval_depth": graft_out["routing"]["retrieval_depth"],
                        "modules": graft_out["routing"]["activated_modules"],
                    },
                    "baseline": {
                        **_score(
                            base_out["answer"],
                            base_out["retrieval"],
                            base_out["module_results"],
                            rec,
                            base_ms,
                        ),
                        "modules": [m["module"] for m in base_out["module_results"]],
                    },
                }
            )

    return {
        "seed": seed,
        "n_queries": len(per_query),
        "metric_definitions": METRIC_DEFINITIONS,
        "aggregate": {
            "graft": aggregate(per_query, "graft"),
            "baseline": aggregate(per_query, "baseline"),
        },
        "per_query": per_query,
    }


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #
def format_summary(results: dict[str, Any]) -> str:
    """Render the per-query and aggregate console tables."""
    lines: list[str] = []
    header = (
        f"{'#':>3}  {'query':<44} {'tier':<8} "
        f"{'F1 g/b':>11} {'ms g/b':>15} {'mods g/b':>9} {'P@5 g/b':>11}"
    )
    lines += [header, "-" * len(header)]
    for i, q in enumerate(results["per_query"], start=1):
        g, b = q["graft"], q["baseline"]
        text = q["query"] if len(q["query"]) <= 44 else q["query"][:41] + "..."
        lines.append(
            f"{i:>3}  {text:<44} {g['complexity']:<8} "
            f"{g['accuracy_f1']:>5.2f}/{b['accuracy_f1']:<5.2f} "
            f"{g['latency_ms']:>7.1f}/{b['latency_ms']:<7.1f} "
            f"{g['modules_fired']:>4}/{b['modules_fired']:<4} "
            f"{g['precision_at_5']:>5.2f}/{b['precision_at_5']:<5.2f}"
        )

    agg = results["aggregate"]
    rows = (
        ("Accuracy (token F1)", "accuracy_f1", "{:.3f}"),
        ("Avg latency (ms)", "latency_ms", "{:.1f}"),
        ("Modules fired / query", "modules_fired", "{:.2f}"),
        ("Retrieval precision@5", "precision_at_5", "{:.3f}"),
    )
    lines += [
        "",
        f"Aggregate over {results['n_queries']} queries (seed={results['seed']})",
        f"{'Metric':<24} {'GRAFT':>10} {'Baseline':>10}",
        "-" * 46,
    ]
    for label, key, fmt in rows:
        graft_cell = fmt.format(agg["graft"][key])
        base_cell = fmt.format(agg["baseline"][key])
        lines.append(f"{label:<24} {graft_cell:>10} {base_cell:>10}")
    return "\n".join(lines)


def main(
    argv: Sequence[str] | None = None,
    *,
    embedder: Embedder | None = None,
    retriever: Retriever | None = None,
) -> int:
    """Run the benchmark CLI using *argv*, or process arguments when it is ``None``.

    Forward optional callbacks to :func:`run_benchmark`. Create output parent
    directories, overwrite the output JSON file, print a summary, and return 0
    on success. A :class:`DatasetFormatError` prints an error to stderr and
    returns 1 before running the benchmark or writing output.

    Argument parsing raises :class:`SystemExit` for help or invalid arguments.
    UTF-8 decoding, benchmark, and output I/O errors propagate unchanged.
    """
    parser = argparse.ArgumentParser(
        prog="python3 -m benchmark.runner",
        description="Run GRAFT vs the flat baseline on a JSONL benchmark set.",
    )
    parser.add_argument("--dataset", required=True, type=Path, help="JSONL benchmark file")
    parser.add_argument("--output", required=True, type=Path, help="where to write results JSON")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="RNG seed (default: 42)")
    args = parser.parse_args(argv)

    try:
        records = load_dataset(args.dataset)
    except DatasetFormatError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_DATASET_ERROR

    results = run_benchmark(records, embedder=embedder, retriever=retriever, seed=args.seed)
    results["dataset"] = str(args.dataset)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(format_summary(results))
    print(f"\nResults written to {args.output}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())