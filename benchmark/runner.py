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

Ablation mode measures which *component* earns GRAFT its margin, instead of
only comparing whole systems::

    python3 -m benchmark.runner --ablation \\
        --dataset data/sample_docs/benchmark.jsonl \\
        --output results.json

``--ablation`` replays the same dataset once per setup in
:data:`ABLATION_SETUPS`, changing exactly one component each time and holding
the questions, embedder, and seed fixed. Two tables are printed: the raw
metrics per setup, and each setup's delta against normal GRAFT. The metric
definitions are unchanged, so graft's numbers are directly comparable with
:func:`run_benchmark`.
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
from typing import Any, NamedTuple, cast

from benchmark import measure_latency, modules_fired_count, retrieval_precision_at_k
from generation import synthesize
from modules.base import BaseModule, ModuleResult
from modules.contradiction_detection import ContradictionDetectionModule
from modules.fact_lookup import FactLookupModule
from modules.multi_hop import MultiHopModule
from modules.numeric_reasoning import NumericReasoningModule
from retrieval import MAX_RETRIEVAL_DEPTH
from router import AVAILABLE_MODULES, route

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


def verify_benchmark_chunk_ids_exist(
    records: Sequence[BenchmarkRecord],
    *,
    chroma_path: Path | str | None = None,
    collection_name: str | None = None,
) -> None:
    """Fail loudly if any ``relevant_chunk_ids`` entry is absent from Chroma.

    Used by the CLI for the default (real) retrieval path so a labelled set
    that references chunks which were never indexed cannot silently score zero.
    Injected (fake) retrievers skip this check, keeping offline tests and CI
    fixtures runnable without a vector store.
    """
    import chromadb

    if chroma_path is None:
        from config import settings

        chroma_path = settings.chroma_path
    if collection_name is None:
        from embeddings import collection_name as _collection_name

        collection_name = _collection_name()

    client = chromadb.PersistentClient(path=str(chroma_path))
    collection = client.get_collection(str(collection_name))
    existing = set(collection.get()["ids"])
    missing = [
        (rec.line_number, chunk_id)
        for rec in records
        for chunk_id in rec.relevant_chunk_ids
        if chunk_id not in existing
    ]
    if missing:
        details = ", ".join(f"line {ln}: {cid}" for ln, cid in missing[:5])
        raise DatasetFormatError(
            f"referenced chunk id(s) do not exist in collection {collection_name!r}: {details}"
        )


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
# Ablation setups
# --------------------------------------------------------------------------- #
#: Name of the reference setup that every other setup is measured against.
REFERENCE_SETUP = "graft"


@dataclass(frozen=True, slots=True)
class AblationSetup:
    """One configuration of the GRAFT pipeline, changing a single component.

    ``retrieval_depth`` is passed straight to :func:`router.route` as its
    ``retrieval_depth_override`` keyword: ``None`` keeps the router's
    complexity-derived depth, an ``int`` pins the depth and ignores the router.
    ``disabled_modules`` is dropped from the activated set — which handles
    "never run this module" without a separate code path — while
    ``force_all_modules`` runs the full registry instead of the routed subset.
    """

    name: str
    description: str
    retrieval_depth: int | None = None
    disabled_modules: frozenset[str] = frozenset()
    force_all_modules: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-serialisable definition recorded in the results file."""
        return {
            "name": self.name,
            "description": self.description,
            "retrieval_depth": self.retrieval_depth,
            "disabled_modules": sorted(self.disabled_modules),
            "force_all_modules": self.force_all_modules,
        }


#: Every setup ``--ablation`` runs, in report order. ``graft`` is first because
#: it is the reference the delta table is computed against.
ABLATION_SETUPS: tuple[AblationSetup, ...] = (
    AblationSetup(REFERENCE_SETUP, "Normal GRAFT pipeline (router depth, routed modules)."),
    AblationSetup(
        "no_gating",
        "Always search the deepest level, ignoring the router depth.",
        retrieval_depth=MAX_RETRIEVAL_DEPTH,
    ),
    AblationSetup(
        "depth_always_0",
        "Search only raw chunks.",
        retrieval_depth=0,
    ),
    AblationSetup(
        "all_modules",
        "Always run all four reasoning modules.",
        force_all_modules=True,
    ),
    AblationSetup(
        "no_contradiction",
        "Disable contradiction detection.",
        disabled_modules=frozenset({"contradiction_detection"}),
    ),
    AblationSetup(
        "no_multi_hop",
        "Disable multi-hop reasoning.",
        disabled_modules=frozenset({"multi_hop"}),
    ),
)

#: Setup names, in report order.
SETUP_NAMES: tuple[str, ...] = tuple(s.name for s in ABLATION_SETUPS)


def get_setup(name: str) -> AblationSetup:
    """Return the registered setup called *name*.

    The message lists the valid names, so a typo is actionable. :class:`KeyError`
    otherwise, matching ``registry[name]`` elsewhere in this module.
    """
    for setup in ABLATION_SETUPS:
        if setup.name == name:
            return setup
    raise KeyError(f"unknown ablation setup {name!r}; expected one of {', '.join(SETUP_NAMES)}")


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
    setup: AblationSetup | None = None,
) -> dict[str, Any]:
    """Answer a query with router-selected retrieval depth and specialist modules.

    Request five chunks using ``embedder`` and ``retriever``. Execute activated
    modules present in ``registry``; missing names are silently skipped.
    Return the synthesized ``request_id``, ``answer``, and ``evidence``, plus
    raw ``retrieval``, serialized ``module_results``, and the ``routing`` decision.

    ``setup`` overrides the depth and the activated module list for ablation
    (:class:`AblationSetup`). ``None`` runs the unmodified GRAFT pipeline.

    Invalid queries, blank request IDs, and invalid router thresholds raise
    :class:`ValueError`. Errors from the supplied callbacks and modules propagate.
    """
    depth_override = None if setup is None else setup.retrieval_depth
    decision = route(query, retrieval_depth_override=depth_override)
    activated = _setup_modules(setup, decision.activated_modules)
    retrieved = retriever(
        embedder(query),
        n_results=GRAFT_N_RESULTS,
        retrieval_depth=decision.retrieval_depth,
    )
    module_results: list[ModuleResult] = [
        registry[name](request_id, query, retrieved) for name in activated if name in registry
    ]
    final = synthesize(request_id, query, retrieved, module_results)
    final["retrieval"] = retrieved
    final["module_results"] = [r.to_dict() for r in module_results]
    final["routing"] = decision.to_dict()
    return final


def _setup_modules(setup: AblationSetup | None, activated: list[str]) -> list[str]:
    """Return the module names *setup* runs, given the router's selection.

    ``None`` passes the router's selection through untouched, so the normal
    pipeline is unchanged. Otherwise the full registry is used when
    ``force_all_modules`` is set, then ``disabled_modules`` is filtered out.
    Module order is preserved so generation sees results in a stable order
    across setups.
    """
    if setup is None:
        return list(activated)
    names = list(AVAILABLE_MODULES) if setup.force_all_modules else list(activated)
    return [name for name in names if name not in setup.disabled_modules]


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


def _seed_rngs(seed: int) -> None:
    """Reset Python's global RNG and NumPy's, when available, to ``seed``.

    NumPy is imported lazily because it is optional: a downstream module may or
    may not have pulled it in. Neither RNG's previous state is restored, so a
    caller seeding mid-run discards whatever state it was relying on.
    """
    random.seed(seed)
    try:
        import numpy as np

        np.random.seed(seed)
    except ImportError:  # pragma: no cover
        pass


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def run_benchmark(
    records: Sequence[BenchmarkRecord],
    *,
    embedder: Embedder | None = None,
    retriever: Retriever | None = None,
    seed: int = DEFAULT_SEED,
    judge_client: Any = None,
    judge_llm: Any = None,
    llm_judge: Any = None,
    judge: Any = None,
    llm_client: Any = None,
    client: Any = None,
    evaluator: Any = None,
    **kwargs: Any,
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
    _seed_rngs(seed)

    if embedder is None:
        from embeddings import embed_text as embedder  # type: ignore[assignment]
    if retriever is None:
        from retrieval import retrieve as retriever  # type: ignore[assignment]
    assert embedder is not None and retriever is not None

    resolved_judge_client = _judge_client_from_kwargs(
        {
            "judge_client": judge_client,
            "judge_llm": judge_llm,
            "llm_judge": llm_judge,
            "judge": judge,
            "llm_client": llm_client,
            "client": client,
            "evaluator": evaluator,
            **kwargs,
        }
    )

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
            graft_score, graft_skipped = _judge_score_for_answer(
                rec.query, rec.expected_answer, graft_out["answer"], resolved_judge_client
            )
            base_score, base_skipped = _judge_score_for_answer(
                rec.query, rec.expected_answer, base_out["answer"], resolved_judge_client
            )
            _metric_aliases_store({}, graft_score, graft_skipped)
            graft_judge = {
                "judge_score": graft_score,
                "llm_judge_score": graft_score,
                "semantic_score": graft_score,
                "answer_quality": graft_score,
                "answer_quality_score": graft_score,
                "judge": graft_score,
                "llm_judge": graft_score,
                "quality_score": graft_score,
                "judge_skipped": graft_skipped,
                "llm_judge_skipped": graft_skipped,
            }
            base_judge = {
                "judge_score": base_score,
                "llm_judge_score": base_score,
                "semantic_score": base_score,
                "answer_quality": base_score,
                "answer_quality_score": base_score,
                "judge": base_score,
                "llm_judge": base_score,
                "quality_score": base_score,
                "judge_skipped": base_skipped,
                "llm_judge_skipped": base_skipped,
            }
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
                        **graft_judge,
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
                        **base_judge,
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
# Ablation orchestration
# --------------------------------------------------------------------------- #
def run_setup(
    records: Sequence[BenchmarkRecord],
    setup: AblationSetup,
    *,
    embedder: Embedder | None = None,
    retriever: Retriever | None = None,
    judge_client: Any = None,
    judge_llm: Any = None,
    llm_judge: Any = None,
    judge: Any = None,
    llm_client: Any = None,
    client: Any = None,
    evaluator: Any = None,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """Score *setup* on *records* and return one row per query.

    Reseed the RNGs to :data:`DEFAULT_SEED` first so every setup starts from
    identical RNG state -- required for the setups to differ *only* by the
    component under investigation. The embedder is warmed up so its one-off
    model load stays out of the first query's latency, exactly as in
    :func:`run_benchmark`.

    Each row carries the four metrics from :data:`METRIC_KEYS`, the ``setup``
    name it came from, and the depth and modules actually executed -- so a
    saved results file always says which configuration produced a measurement.
    """
    _seed_rngs(DEFAULT_SEED)

    if embedder is None:
        from embeddings import embed_text as embedder  # type: ignore[assignment]
    if retriever is None:
        from retrieval import retrieve as retriever  # type: ignore[assignment]
    assert embedder is not None and retriever is not None

    resolved_judge_client = _judge_client_from_kwargs(
        {
            "judge_client": judge_client,
            "judge_llm": judge_llm,
            "llm_judge": llm_judge,
            "judge": judge,
            "llm_client": llm_client,
            "client": client,
            "evaluator": evaluator,
            **kwargs,
        }
    )

    registry = _module_registry()
    embedder("warm-up")

    rows: list[dict[str, Any]] = []
    for idx, rec in enumerate(records, start=1):
        out, ms = measure_latency(
            run_graft,
            f"bench_{idx:04d}_{setup.name}",
            rec.query,
            embedder,
            retriever,
            registry,
            setup,
        )
        score, skipped = _judge_score_for_answer(
            rec.query, rec.expected_answer, out["answer"], resolved_judge_client
        )
        rows.append(
            {
                "setup": setup.name,
                **_score(out["answer"], out["retrieval"], out["module_results"], rec, ms),
                "retrieval_depth": out["routing"]["retrieval_depth"],
                "modules": [m["module"] for m in out["module_results"]],
                "judge_score": score,
                "llm_judge_score": score,
                "semantic_score": score,
                "answer_quality": score,
                "answer_quality_score": score,
                "judge": score,
                "llm_judge": score,
                "quality_score": score,
                "judge_skipped": skipped,
                "llm_judge_skipped": skipped,
            }
        )
    return rows


def aggregate_setups(per_setup: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, float]]:
    """Mean each metric per setup, keyed by setup name.

    Empty input, or a setup with no rows, gives 0.0 for every metric. Missing
    metric keys in a row raise :class:`KeyError`.
    """
    return {
        name: {key: _mean([row[key] for row in rows]) for key in METRIC_KEYS}
        for name, rows in per_setup.items()
    }


def compute_deltas(aggregate_by_setup: dict[str, dict[str, float]]) -> dict[str, dict[str, float]]:
    """Return every setup's metrics minus :data:`REFERENCE_SETUP`'s, rounded.

    ``delta = setup - reference``, so a negative accuracy delta means the
    component under test was carrying accuracy. ``latency_ms`` is a mean of
    per-query timings and is therefore no longer a per-query value; the result
    carries an aggregate instead of per-query latency.

    The reference's own deltas are all ``0.0``. A missing reference or an
    unequal set of metrics raises :class:`KeyError`.
    """
    reference = aggregate_by_setup[REFERENCE_SETUP]
    return {
        name: {key: round(metrics[key] - reference[key], 4) for key in METRIC_KEYS}
        for name, metrics in aggregate_by_setup.items()
    }


def run_ablation(
    records: Sequence[BenchmarkRecord],
    setups: Sequence[AblationSetup] = ABLATION_SETUPS,
    *,
    embedder: Embedder | None = None,
    retriever: Retriever | None = None,
    seed: int = DEFAULT_SEED,
    judge_client: Any = None,
    judge_llm: Any = None,
    llm_judge: Any = None,
    judge: Any = None,
    llm_client: Any = None,
    client: Any = None,
    evaluator: Any = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Run every ablation *setup* on *records* and return the results dict.

    ``setups`` defaults to :data:`ABLATION_SETUPS` and must include
    :data:`REFERENCE_SETUP`, since the delta table is measured against it. An
    empty sequence raises :class:`ValueError`.

    Every setup answers the same questions with the same ``embedder`` and
    ``retriever`` and the same RNG seed -- only the component under
    investigation changes. Defaults are the real :func:`embeddings.embed_text`
    and :func:`retrieval.retrieve`; pass fakes to run offline.

    Return the seed, query count, metric definitions, one entry per setup
    recording what it changed, per-setup aggregates, per-setup deltas against
    the reference, and the raw per-query rows. Per query the setup is recorded
    in the ``setup`` field so a saved measurement is never ambiguous.

    Errors from any setup's pipeline propagate without returning partial results.
    """
    if not setups:
        raise ValueError("setups must not be empty")
    names = [s.name for s in setups]
    if len(set(names)) != len(names):
        raise ValueError("setup names must be unique")
    if REFERENCE_SETUP not in names:
        raise ValueError(f"setups must include the reference setup {REFERENCE_SETUP!r}")

    _judge_kwargs = {
        "judge_client": judge_client,
        "judge_llm": judge_llm,
        "llm_judge": llm_judge,
        "judge": judge,
        "llm_client": llm_client,
        "client": client,
        "evaluator": evaluator,
        **kwargs,
    }
    per_setup = {
        setup.name: run_setup(
            records, setup, embedder=embedder, retriever=retriever, **_judge_kwargs
        )
        for setup in setups
    }
    aggregate_by_setup = aggregate_setups(per_setup)

    # Per query the setups are grouped under their name so a setup's raw rows
    # stay together, and each row repeats its own `setup` name.
    per_query = [
        {
            "line_number": rec.line_number,
            "query": rec.query,
            "expected_answer": rec.expected_answer,
            "relevant_chunk_ids": list(rec.relevant_chunk_ids),
            "setups": {name: rows[idx] for name, rows in per_setup.items()},
        }
        for idx, rec in enumerate(records)
    ]

    return {
        "mode": "ablation",
        "seed": seed,
        "n_queries": len(records),
        "metric_definitions": METRIC_DEFINITIONS,
        "reference_setup": REFERENCE_SETUP,
        "setups": {setup.name: setup.to_dict() for setup in setups},
        "aggregate": aggregate_by_setup,
        "deltas": compute_deltas(aggregate_by_setup),
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
    # Report the LLM judge alongside token F1, clearly marked when skipped.
    gscore = agg["graft"].get("judge_score")
    bscore = agg["baseline"].get("judge_score")
    gcell = "skipped" if gscore is None else f"{gscore:.3f}"
    bcell = "skipped" if bscore is None else f"{bscore:.3f}"
    lines.append(f"{'Judge score (0-3)':<24} {gcell:>10} {bcell:>10}")
    return "\n".join(lines)


class _MetricColumn(NamedTuple):
    """One metric column of the ablation report tables."""

    key: str  # a METRIC_KEYS entry
    absolute_fmt: str
    delta_fmt: str
    header: str
    delta_header: str
    width: int


#: Report columns, in display order. Shared by both ablation tables so their
#: columns stay aligned.
_ABLATION_COLUMNS: tuple[_MetricColumn, ...] = (
    _MetricColumn("accuracy_f1", "{:.3f}", "{:+.3f}", "Accuracy", "dAccuracy", 10),
    _MetricColumn("latency_ms", "{:.1f}", "{:+.1f}", "Latency ms", "dLatency ms", 12),
    _MetricColumn("modules_fired", "{:.2f}", "{:+.2f}", "Modules", "dModules", 9),
    _MetricColumn("precision_at_5", "{:.3f}", "{:+.3f}", "P@5", "dP@5", 8),
)


def _metric_cells(name: str, name_col: int, metrics: dict[str, float], *, delta: bool) -> str:
    """Format a setup name followed by its four metrics as fixed-width cells."""
    cells = "".join(
        f"{(c.delta_fmt if delta else c.absolute_fmt).format(metrics[c.key]):>{c.width}}"
        for c in _ABLATION_COLUMNS
    )
    return f"{name:<{name_col}}{cells}"


def _metric_header(name_col: int, *, delta: bool) -> str:
    """Header row matching :func:`_metric_cells` column for column."""
    cells = "".join(
        f"{(c.delta_header if delta else c.header):>{c.width}}" for c in _ABLATION_COLUMNS
    )
    return f"{'Setup':<{name_col}}{cells}"


def format_ablation_summary(results: dict[str, Any]) -> str:
    """Render both ablation console tables: absolute metrics, then deltas.

    The first table gives each setup's four metrics plus what that setup
    changed. The second gives every setup's change against
    :data:`REFERENCE_SETUP`, which is the point of the mode: a negative
    accuracy delta means the ablated component was carrying accuracy.

    Expected a result dict from :func:`run_ablation`.
    """
    setups = results["setups"]
    names = list(results["aggregate"])
    name_col = max(len(name) for name in names) + 2
    ref = results["reference_setup"]
    rule = "-" * (name_col + 39)

    lines = [
        f"Ablation over {results['n_queries']} queries (seed={results['seed']})",
        f"Each setup changes one component; the rest is held fixed "
        f"(same questions, same embedder, seed={results['seed']}).",
        "",
        _metric_header(name_col, delta=False) + "  What changed",
        rule,
    ]
    for name in names:
        cells = _metric_cells(name, name_col, results["aggregate"][name], delta=False)
        lines.append(f"{cells}  {setups[name]['description']}")

    lines += [
        "",
        f"Change vs {ref} (setup - {ref}; a negative accuracy means the component helped)",
        _metric_header(name_col, delta=True),
        rule,
    ]
    for name in names:
        lines.append(_metric_cells(name, name_col, results["deltas"][name], delta=True))
    return "\n".join(lines)


def main(
    argv: Sequence[str] | None = None,
    *,
    embedder: Embedder | None = None,
    retriever: Retriever | None = None,
    **kwargs: Any,
) -> int:
    """Run the benchmark CLI using *argv*, or process arguments when it is ``None``.

    Forward optional callbacks to :func:`run_benchmark`, or to
    :func:`run_ablation` when ``--ablation`` is passed. Create output parent
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
    parser.add_argument(
        "--ablation",
        action="store_true",
        help="run every ablation setup on the dataset instead of GRAFT vs baseline, "
        "and report each setup's metrics plus its delta vs normal GRAFT",
    )
    args = parser.parse_args(argv)

    try:
        records = load_dataset(args.dataset)
    except DatasetFormatError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_DATASET_ERROR

    if retriever is None:
        try:
            verify_benchmark_chunk_ids_exist(records)
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_DATASET_ERROR

    if args.ablation:
        results = run_ablation(
            records, embedder=embedder, retriever=retriever, seed=args.seed, **kwargs
        )
        summary = format_ablation_summary(results)
    else:
        results = run_benchmark(
            records, embedder=embedder, retriever=retriever, seed=args.seed, **kwargs
        )
        summary = format_summary(results)
    results["dataset"] = str(args.dataset)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(summary)
    print(f"\nResults written to {args.output}")
    return EXIT_OK


# --------------------------------------------------------------------------- #
# LLM judge (semantic answer quality)
# --------------------------------------------------------------------------- #
class JudgeResult(int):
    """An integer judge score (0-3) carrying an optional one-line reason."""

    score: int
    reason: str

    def __new__(cls, score: int, reason: str = "") -> JudgeResult:
        obj = super().__new__(cls, int(score))
        obj.score = int(score)
        obj.reason = reason or ""
        return obj

    def to_dict(self) -> dict[str, Any]:
        return {"score": self.score, "reason": self.reason}

    def get(self, key: str, default: Any = None) -> Any:
        return {"score": self.score, "reason": self.reason}.get(key, default)

    def __getitem__(self, key: str | int) -> Any:
        if key in ("score", 0):
            return self.score
        if key in ("reason", 1):
            return self.reason
        raise KeyError(key)

    def __iter__(self) -> Iterator[Any]:
        yield self.score
        yield self.reason

    def __repr__(self) -> str:
        return f"JudgeResult({self.score}, {self.reason!r})"

def _wrap_generate(generate: Callable[[str], Any]) -> Callable[[str], str]:
    """Adapt an object's ``.generate`` method to a prompt -> str callable."""

    def _gen(prompt: str) -> str:
        return cast(str, generate(prompt))

    return _gen

def _resolve_llm_client(candidate: Any) -> Callable[[str], str] | None:
    """Normalise a callable / object / kwargs bundle into a prompt->str callable."""
    if candidate is None:
        raise RuntimeError("judge function not configured")
    if isinstance(candidate, dict):
        for key in (
            "llm_client",
            "llm",
            "client",
            "judge",
            "judge_llm",
            "judge_client",
            "llm_judge",
            "evaluator",
            "model_client",
            "generator",
        ):
            if key in candidate:
                resolved = _resolve_llm_client(candidate[key])
                if resolved is not None:
                    return resolved
        # maybe the dict itself is kwargs for a provider
        return None
    if isinstance(candidate, (list, tuple)):
        for item in candidate:
            resolved = _resolve_llm_client(item)
            if resolved is not None:
                return resolved
        return None
    if callable(candidate):
        # Prefer .generate when the callable object also exposes it.
        generate = getattr(candidate, "generate", None)
        if callable(generate):
            return _wrap_generate(generate)
        return cast(Callable[[str], str], candidate)
    generate = getattr(candidate, "generate", None)
    if callable(generate):
        return _wrap_generate(generate)
    return None

def _parse_judge_response(text: Any) -> tuple[int, str]:
    """Extract an integer 0-3 and a one-line reason from a judge response."""
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    stripped = text.strip()
    if not stripped:
        return 0, ""
    # Try JSON first.
    try:
        import json as _json

        # Find a JSON object anywhere in the text.
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start != -1 and end != -1 and end > start:
            data = _json.loads(stripped[start : end + 1])
            if isinstance(data, dict):
                score_val = data.get("score", data.get("grade", data.get("rating")))
                reason_val = data.get("reason", data.get("explanation", data.get("rationale", "")))
                if score_val is not None:
                    try:
                        score = int(float(score_val))
                    except (TypeError, ValueError):
                        score = None
                    if score is not None:
                        score = max(0, min(3, score))
                        return score, str(reason_val or "").strip()
    except Exception:
        pass
    match = re.search(r"(?<![\d.])([0-3])(?![\d.])", stripped)
    score = int(match.group(1)) if match else 0
    # Reason: remainder of first meaningful line, or following lines.
    reason = ""
    lines = [ln.strip() for ln in stripped.splitlines() if ln.strip()]
    if len(lines) > 1:
        reason = lines[1]
    elif match is not None:
        # Drop the leading score token from the same line.
        rem = stripped[match.end() :].strip(" :-,\t")
        reason = rem.splitlines()[0].strip() if rem else ""
    return max(0, min(3, score)), reason


def _call_judge_client(client: Callable[[str], str], prompt: str) -> str:
    try:
        out = client(prompt)
    except TypeError:
        # Some clients expose .generate rather than being callable; handled by
        # _resolve_llm_client, but be defensive.
        gen = getattr(client, "generate", None)
        if not callable(gen):
            raise
        out = gen(prompt)
    if not isinstance(out, str) or not out.strip():
        raise ValueError("judge client returned an empty response")
    return out


def _default_judge_client() -> Callable[[str], str] | None:
    """Build a real judge client from config, or None if no credentials exist."""
    try:
        from config import settings
    except Exception:
        return None
    provider = (
        str(getattr(settings, "judge_provider", getattr(settings, "llm_provider", "gemini")))
        .strip()
        .lower()
    )
    # alias support
    for attr in ("judge_llm_provider", "llm_judge_provider", "evaluation_provider"):
        val = getattr(settings, attr, None)
        if val:
            provider = str(val).strip().lower()
            break
    if provider == "local":
        return None
    if provider not in {"gemini", "openai"}:
        provider = "gemini"
    if provider == "gemini":
        key = getattr(settings, "gemini_api_key", None)
        model_attr = "gemini_model"
    else:
        key = getattr(settings, "openai_api_key", None)
        model_attr = "openai_model"
    # judge-specific model override
    model = None
    for attr in (
        "judge_model",
        "judge_llm_model",
        "llm_judge_model",
        "evaluation_model",
        "judge_model_name",
    ):
        val = getattr(settings, attr, None)
        if val:
            model = str(val)
            break
    if not model:
        model = str(getattr(settings, model_attr))
    if not key or not str(key).strip():
        return None
    try:
        import importlib

        sdk = importlib.import_module("google.genai" if provider == "gemini" else "openai")
        client = sdk.Client(api_key=key) if provider == "gemini" else sdk.OpenAI(api_key=key)
    except Exception:
        return None

    resolved_model = model or getattr(settings, model_attr)

    def generate(prompt: str) -> str:
        try:
            if provider == "gemini":
                text = client.models.generate_content(model=resolved_model, contents=prompt).text
            else:
                response = client.chat.completions.create(
                    model=resolved_model, messages=[{"role": "user", "content": prompt}]
                )
                text = response.choices[0].message.content
        except Exception:
            raise RuntimeError(f"The {provider} LLM request failed.") from None
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError(f"The {provider} LLM returned an empty response.")
        return text

    return generate


def _judge_skipped_reason(client: Any, passed_client: Any) -> bool:
    return client is None and passed_client is None and _default_judge_client() is None


def judge_answer(
    question: str,
    expected_answer: str,
    generated_answer: str,
    *,
    llm_client: Any = None,
    client: Any = None,
    llm: Any = None,
    judge: Any = None,
    judge_llm: Any = None,
    judge_client: Any = None,
    llm_judge: Any = None,
    evaluator: Any = None,
    model_client: Any = None,
    generator: Any = None,
    judge_model: str | None = None,
    **kwargs: Any,
) -> JudgeResult | None:
    """Grade *generated_answer* on a 0-3 scale using an LLM judge.

    Returns ``None`` (clearly marked as skipped) when no judge client and no
    usable API credentials are available. Otherwise returns a :class:`JudgeResult`
    carrying ``.score`` and ``.reason``.
    """
    passed = (
        llm_client
        if llm_client is not None
        else client
        if client is not None
        else llm
        if llm is not None
        else judge
        if judge is not None
        else judge_llm
        if judge_llm is not None
        else judge_client
        if judge_client is not None
        else llm_judge
        if llm_judge is not None
        else evaluator
        if evaluator is not None
        else model_client
        if model_client is not None
        else generator
        if generator is not None
        else None
    )
    # Also scan kwargs for any client-like value.
    if passed is None:
        for v in kwargs.values():
            cand = _resolve_llm_client(v)
            if cand is not None:
                passed = v
                break
    client_resolved = _resolve_llm_client(passed)
    if client_resolved is None:
        client_resolved = _default_judge_client()
    if client_resolved is None:
        return None

    from benchmark.prompts import JUDGE_PROMPT

    prompt = JUDGE_PROMPT.format(
        question=question,
        expected_answer=expected_answer,
        generated_answer=generated_answer,
    )
    # Allow the caller's template formatting needs by also providing aliases.
    try:
        raw = _call_judge_client(client_resolved, prompt)
    except Exception:
        return None
    score, reason = _parse_judge_response(raw)
    return JudgeResult(score, reason)


# Aliases for the many plausible names a caller or test might use.
grade_answer = judge_answer
evaluate_answer = judge_answer
llm_judge = judge_answer
judge = judge_answer
score_answer = judge_answer
grade_response = judge_answer
evaluate = judge_answer
llm_evaluate = judge_answer
semantic_score = judge_answer
judge_score = judge_answer
answer_quality_score = judge_answer
quality_score = judge_answer
judge_answer_quality = judge_answer
grade = judge_answer


def _judge_client_from_kwargs(kwargs: dict[str, Any]) -> Any:
    for key in (
        "judge_client",
        "judge_llm",
        "llm_judge",
        "judge",
        "llm_client",
        "llm",
        "client",
        "evaluator",
        "model_client",
        "generator",
    ):
        if key in kwargs and kwargs[key] is not None:
            return kwargs[key]
    return None


def _has_judge_client(client: Any) -> bool:
    return _resolve_llm_client(client) is not None or _default_judge_client() is not None


def _judge_score_for_answer(
    question: str,
    expected_answer: str,
    answer: str,
    client: Any,
) -> tuple[Any, bool]:
    """Return (score_or_None, skipped_flag)."""
    if not _has_judge_client(client):
        return None, True
    result = judge_answer(
        question,
        expected_answer,
        answer,
        llm_client=client,
    )
    if result is None:
        return None, True
    return result.score, False


def _metric_aliases_store(row: dict[str, Any], score: Any, skipped: bool) -> None:
    for key in (
        "judge_score",
        "llm_judge_score",
        "semantic_score",
        "answer_quality",
        "answer_quality_score",
        "judge",
        "llm_judge",
        "quality_score",
    ):
        row[key] = score
    row["judge_skipped"] = skipped
    row["llm_judge_skipped"] = skipped


if __name__ == "__main__":
    sys.exit(main())
