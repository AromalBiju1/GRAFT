"""Router — query complexity classification + activation gating.

Contract: docs/interfaces.md sections 4, 6, 7.
Implements a cheap, dependency-free heuristic router so the pipeline is
runnable without an LLM. Replace ``classify`` / ``route`` internals with a
trained classifier when available; the public return shape stays the same.

Router thresholds are read from :mod:`config` to avoid magic numbers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from config import settings

# Specialist module registry — single source of truth for activation names
AVAILABLE_MODULES: tuple[str, ...] = (
    "fact_lookup",
    "multi_hop",
    "numeric_reasoning",
    "contradiction_detection",
)

COMPLEXITY_LABELS: tuple[str, ...] = ("simple", "moderate", "complex")

#: Complexity -> retrieval depth. 0 = leaves, 1 = summaries, 2 = root.
COMPLEXITY_TO_DEPTH: dict[str, int] = {"simple": 0, "moderate": 1, "complex": 2}

# --- Classifier tuning (named constants per .github/copilot-instructions) ---
#: Word count at which the length signal saturates.
LENGTH_SATURATION_WORDS = 25.0
#: Clause count at which the clause signal saturates.
CLAUSE_SATURATION_COUNT = 3.0
#: Number of distinct reasoning operations that saturate the compound signal.
COMPOUND_SATURATION_OPS = 3

_WEIGHT_LENGTH = 0.35
_WEIGHT_CLAUSE = 0.25
_WEIGHT_NUMERIC = 0.30
_WEIGHT_MULTI_HOP = 0.35
_WEIGHT_CONTRADICTION = 0.35
_WEIGHT_PER_EXTRA_OP = 0.12

#: Confidence bounds per label, derived from distance to the nearest threshold.
CONFIDENCE_FLOOR = 0.7
CONFIDENCE_CEILING = 1.0

# Intent hints. These use word *stems* followed by ``\w*`` rather than
# ``\b<word>\b``: a bare ``\bconflict\b`` does not match "conflicts", which
# silently prevented contradiction detection from ever activating on the most
# natural phrasing. Stems keep singular, plural and inflected forms matched.
_NUMERIC_HINTS = re.compile(
    r"(\d+[%$€₹]?|\$?\d[\d,]*\.?\d*|\btables?\b|\bcolumns?\b|\bsums?\b|\baverages?\b|\btotals?\b|compare.*\d)",
    re.I,
)
_MULTI_HOP_HINTS = re.compile(
    r"(\bcompar\w+|\bcontrast\w+|\bdifferences?\b|\brelationships?\b"
    r"|\bacross\b|between.*and|\bboth\b|\ball three\b|\bmultiple\b)",
    re.I,
)
_CONTRADICTION_HINTS = re.compile(
    r"(\bconflict\w*|\bcontradict\w*|\binconsist\w*|\bdisagree\w*|\bversus\b|\bvs\.?|\boutdat\w*|\bsupersed\w*|\bobsolet\w*)",
    re.I,
)

# Clause boundaries: a query built from several of these is asking for several
# things at once, which is what separates "moderate" from "complex". A trailing
# "?" is deliberately excluded -- it terminates the query rather than adding a
# request, and counting it made every single-question lookup look compound.
_CLAUSE_MARKERS = (";", ",", " and ", " then ", " also ", " plus ", " as well as ")

# Distinct reasoning operations. A query requesting two or more of these is
# compound, e.g. "compare A and B and explain the differences" asks for a
# comparison *and* an explanation. The patterns are deliberately disjoint: a
# word like "difference" belongs to exactly one operation, otherwise the
# operation count double-counts and short comparisons get over-rated.
_OPERATION_HINTS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("compare", re.compile(r"\b(compare|contrast|differences?|versus|vs\.?|relationship)\b", re.I)),
    (
        "explain",
        re.compile(
            r"\b(explain|why|reason|describe|discuss|elaborate|summarize|summarise)\b", re.I
        ),
    ),
    ("enumerate", re.compile(r"\b(list|all|each|enumerate|every|across|per)\b", re.I)),
    (
        "aggregate",
        re.compile(
            r"\b(sum|total|average|mean|median|maximum|minimum|calculate|compute|count)\b", re.I
        ),
    ),
    (
        "detect_conflict",
        re.compile(
            r"(\bconflict\w*|\bcontradict\w*|\binconsist\w*|\bdisagree\w*|\bmismatch\w*"
            r"|\boutdat\w*|\bsupersed\w*|\bobsolet\w*)\b",
            re.I,
        ),
    ),
)



@dataclass(slots=True)
class RoutingDecision:
    complexity: str  # simple | moderate | complex
    confidence: float  # 0..1
    retrieval_depth: int  # 0=leaf, 1=summary, 2=root etc.
    activated_modules: list[str]

    def to_dict(self) -> dict:
        return {
            "complexity": self.complexity,
            "confidence": self.confidence,
            "retrieval_depth": self.retrieval_depth,
            "activated_modules": list(self.activated_modules),
        }


def _COMPOUND_OPERATION_COUNT(query: str) -> int:
    """Number of distinct reasoning operations the query asks for."""
    return sum(1 for _, pattern in _OPERATION_HINTS if pattern.search(query))


def complexity_score(query: str) -> float:
    """Score how much work a query implies, normalised to 0..1.

    The score sums five independent signals so that no single phrasing quirk
    decides the tier:

    * **length** — longer questions tend to carry more constraints.
    * **clauses** — how many independent requests the query packs together.
    * **operations** — how many *distinct* reasoning operations are asked for.
      This is what separates "compare A and B" (one operation) from
      "compare A and B and explain every difference" (two), which the old
      length-and-keyword score rated identically.
    * **numeric intent** — figures, tables, aggregates.
    * **multi-hop / contradiction intent** — cross-passage or cross-document
      reasoning, and conflict hunting.
    """
    q = query.strip()

    word_count = len(q.split())
    length_signal = min(word_count / LENGTH_SATURATION_WORDS, 1.0)

    clause_count = sum(q.count(marker) for marker in _CLAUSE_MARKERS)
    clause_signal = min(clause_count / CLAUSE_SATURATION_COUNT, 1.0)

    operations = _COMPOUND_OPERATION_COUNT(q)
    # Only the operations *beyond the first* add compound-request pressure, so
    # "compare A and B" stays cheaper than "compare A and B and explain why".
    extra_ops = min(max(0, operations - 1), COMPOUND_SATURATION_OPS - 1)

    numeric_signal = 1.0 if _NUMERIC_HINTS.search(q) else 0.0
    multi_hop_signal = 1.0 if _MULTI_HOP_HINTS.search(q) else 0.0
    contradiction_signal = 1.0 if _CONTRADICTION_HINTS.search(q) else 0.0

    score = (
        _WEIGHT_LENGTH * length_signal
        + _WEIGHT_CLAUSE * clause_signal
        + _WEIGHT_PER_EXTRA_OP * extra_ops
        + _WEIGHT_NUMERIC * numeric_signal
        + _WEIGHT_MULTI_HOP * multi_hop_signal
        + _WEIGHT_CONTRADICTION * contradiction_signal
    )
    return max(0.0, min(1.0, score))


def classify(query: str) -> tuple[str, float]:
    """Rule-based complexity classifier.

    Returns ``(label, confidence)``. Deterministic, no external calls.

    ``score < settings.router_threshold_simple`` => ``simple``
    ``score > settings.router_threshold_complex`` => ``complex``
    otherwise => ``moderate``

    Confidence is the distance from the nearest threshold, so a query sitting
    on a boundary reports low confidence and a clear-cut one reports high.
    """
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")

    score = complexity_score(query)

    simple_thr = settings.router_threshold_simple
    complex_thr = settings.router_threshold_complex
    if simple_thr >= complex_thr:
        raise ValueError(
            f"router_threshold_simple ({simple_thr}) must be < "
            f"router_threshold_complex ({complex_thr})"
        )

    span = CONFIDENCE_CEILING - CONFIDENCE_FLOOR
    if score < simple_thr:
        label = "simple"
        confidence = CONFIDENCE_CEILING - (score / simple_thr) * span
    elif score > complex_thr:
        label = "complex"
        confidence = CONFIDENCE_FLOOR + ((score - complex_thr) / (1.0 - complex_thr)) * span
    else:
        label = "moderate"
        # Peaks at the midpoint of the moderate band, lowest at either edge.
        mid = (simple_thr + complex_thr) / 2
        half = (complex_thr - simple_thr) / 2
        confidence = CONFIDENCE_FLOOR + (0.5 * span) * (1.0 - abs(score - mid) / half)

    confidence = max(0.0, min(1.0, round(float(confidence), 3)))
    return label, confidence


def route(query: str, *, retrieval_depth_override: int | None = None) -> RoutingDecision:
    """Make a full routing decision for *query*.

    Maps complexity -> retrieval_depth (simple->0, moderate->1, complex->2)
    and selects specialist modules via keyword hints plus a sensible
    default per complexity tier.

    ``retrieval_depth_override`` allows callers (e.g. evaluation harness)
    to force a depth for ablation without changing classifier logic.
    """
    complexity, confidence = classify(query)

    # Depth mapping: keep within 0..2 for the 3-level tree; deeper trees should
    # clamp or extend this mapping. This is the core of the gating contract --
    # simple queries must stay at the leaves, otherwise shallow retrieval (the
    # whole point of GRAFT) never happens.
    retrieval_depth = COMPLEXITY_TO_DEPTH[complexity]
    if retrieval_depth_override is not None:
        retrieval_depth = retrieval_depth_override

    # --- Module activation -------------------------------------------------
    # The complexity tier is the gate: a simple query pays for fact_lookup
    # only. Above that, activation is driven by *what the query asks for*, not
    # by the tier, so a "moderate" query that happens to mention a conflict
    # still reaches contradiction detection.
    activated: list[str] = ["fact_lookup"]

    if complexity != "simple":
        if _CONTRADICTION_HINTS.search(query):
            activated.append("contradiction_detection")
        if _NUMERIC_HINTS.search(query):
            activated.append("numeric_reasoning")
        if _MULTI_HOP_HINTS.search(query) or _COMPOUND_OPERATION_COUNT(query) > 1:
            activated.append("multi_hop")
        # A complex query always needs at least one reasoning pass, even when
        # no hint keyword fired (e.g. a long, clause-heavy question).
        if complexity == "complex" and len(activated) == 1:
            activated.append("multi_hop")

    # Deduplicate while preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for m in activated:
        if m not in seen and m in AVAILABLE_MODULES:
            seen.add(m)
            deduped.append(m)

    return RoutingDecision(
        complexity=complexity,
        confidence=confidence,
        retrieval_depth=retrieval_depth,
        activated_modules=deduped,
    )


__all__ = [
    "AVAILABLE_MODULES",
    "COMPLEXITY_LABELS",
    "COMPLEXITY_TO_DEPTH",
    "RoutingDecision",
    "classify",
    "complexity_score",
    "route",
]
