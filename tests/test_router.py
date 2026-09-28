"""Tests for the router: complexity classification, depth mapping, and gating.

The router is the gating component -- every downstream module depends on its
depth and activation decisions -- so these tests pin the behaviours the rest of
the pipeline relies on.
"""

from __future__ import annotations

import pytest

from graft.config import settings
from graft.router import (
    AVAILABLE_MODULES,
    COMPLEXITY_TO_DEPTH,
    RoutingDecision,
    classify,
    complexity_score,
    route,
)

SIMPLE_QUERIES = [
    "What is the capital?",
    "What is the capital of France?",
    "Summarize the document",
    "What are the main conclusions?",
]

MODERATE_QUERIES = [
    "Compare the two policies",
    "What is the difference between TCP and UDP?",
    "What is the total revenue in 2024?",
]

COMPLEX_QUERIES = [
    "Compare the two documents and explain every difference in detail",
    "Summarize the section and explain the differences between the two policies",
    "Compare A and B across all documents and explain differences",
]


def _zebra_query(
    *, words: int | None = None, longest_below: float | None = None, max_words: int = 200
) -> str:
    """Build a neutral query of padding words that carry no hint keywords.

    The score is therefore driven purely by the length signal, which lets the
    confidence tests probe either side of a threshold without hardcoding fragile
    example sentences. Pass ``words`` for an exact length, or ``longest_below``
    for the longest query whose score is still under that value.
    """
    filler = "zebra"
    if words is not None:
        return " ".join([filler] * words)
    best = ""
    for n in range(1, max_words + 1):
        candidate = " ".join([filler] * n)
        if longest_below is not None and complexity_score(candidate) >= longest_below:
            break
        best = candidate
    if not best:  # pragma: no cover - the filler always scores above 0
        raise AssertionError("could not build a neutral query")
    return best


class TestClassify:
    @pytest.mark.parametrize("query", SIMPLE_QUERIES)
    def test_short_factual_queries_are_simple(self, query: str) -> None:
        assert classify(query)[0] == "simple"

    @pytest.mark.parametrize("query", MODERATE_QUERIES)
    def test_mid_length_queries_are_moderate(self, query: str) -> None:
        assert classify(query)[0] == "moderate"

    @pytest.mark.parametrize("query", COMPLEX_QUERIES)
    def test_multi_clause_comparisons_are_complex(self, query: str) -> None:
        assert classify(query)[0] == "complex"

    def test_confidence_falls_as_score_approaches_a_threshold(self) -> None:
        # Confidence is the distance to the nearest threshold, so within one
        # tier the router is least sure about the query sitting just under the
        # boundary and most sure about the one far from it.
        pairs = sorted((complexity_score(q), classify(q)[1]) for q in SIMPLE_QUERIES)
        for (_, clear_conf), (_, boundary_conf) in zip(pairs, pairs[1:], strict=False):
            assert boundary_conf <= clear_conf

    def test_boundary_query_is_less_confident_than_a_clear_one(self) -> None:
        near = _zebra_query(longest_below=settings.router_threshold_simple)
        far = _zebra_query(words=2)
        assert complexity_score(near) > complexity_score(far)
        assert classify(near)[0] == classify(far)[0] == "simple"
        assert classify(near)[1] < classify(far)[1]

    def test_confidence_is_within_bounds_for_every_tier(self) -> None:
        for query in SIMPLE_QUERIES + MODERATE_QUERIES + COMPLEX_QUERIES:
            label, confidence = classify(query)
            assert label in ("simple", "moderate", "complex")
            assert 0.0 <= confidence <= 1.0

    @pytest.mark.parametrize("bad", ["", "   ", "\n\t"])
    def test_empty_query_raises(self, bad: str) -> None:
        with pytest.raises(ValueError):
            classify(bad)

    def test_non_string_query_raises(self) -> None:
        with pytest.raises(ValueError):
            classify(None)  # type: ignore[arg-type]

    def test_score_is_normalised(self) -> None:
        for query in SIMPLE_QUERIES + COMPLEX_QUERIES:
            assert 0.0 <= complexity_score(query) <= 1.0


class TestDepthMapping:
    """The gating contract: simple queries must retrieve shallowly.

    Regression guard. ``settings.default_retrieval_depth`` used to override this
    map, which sent simple queries to depth 1 -- i.e. the opposite of shallow
    retrieval, and the reason the router appeared to do nothing.
    """

    def test_simple_maps_to_depth_0(self) -> None:
        for query in SIMPLE_QUERIES:
            assert route(query).retrieval_depth == 0, query

    def test_moderate_maps_to_depth_1(self) -> None:
        for query in MODERATE_QUERIES:
            assert route(query).retrieval_depth == 1, query

    def test_complex_maps_to_depth_2(self) -> None:
        for query in COMPLEX_QUERIES:
            assert route(query).retrieval_depth == 2, query

    def test_depth_map_matches_constant(self) -> None:
        for query in SIMPLE_QUERIES + MODERATE_QUERIES + COMPLEX_QUERIES:
            decision = route(query)
            assert decision.retrieval_depth == COMPLEXITY_TO_DEPTH[decision.complexity]

    def test_override_forces_depth_2_regardless_of_complexity(self) -> None:
        assert route("What is the capital?", retrieval_depth_override=2).retrieval_depth == 2

    def test_override_forces_depth_0_on_a_complex_query(self) -> None:
        query = COMPLEX_QUERIES[0]
        assert route(query).retrieval_depth == 2
        assert route(query, retrieval_depth_override=0).retrieval_depth == 0


class TestModuleActivation:
    def test_simple_activates_only_fact_lookup(self) -> None:
        for query in SIMPLE_QUERIES:
            assert route(query).activated_modules == ["fact_lookup"], query

    def test_activates_fact_lookup_for_simple_queries(self) -> None:
        assert "fact_lookup" in route("What is the capital?").activated_modules

    @pytest.mark.parametrize(
        "keyword",
        [
            "conflict",
            "conflicts",
            "contradict",
            "contradicts",
            "inconsistencies",
            "disagree",
            "outdated",
            "superseded",
        ],
    )
    def test_activates_contradiction_detection_on_conflict_keywords(self, keyword: str) -> None:
        # Regression guard: the hint pattern used to be \bconflict\b, which does
        # not match "conflicts", so the module never fired on the plural form.
        decision = route(f"Does the {keyword} in section 3 of these documents matter?")
        assert "contradiction_detection" in decision.activated_modules, keyword

    @pytest.mark.parametrize(
        "keyword", ["total", "totals", "average", "averages", "table", "tables", "80"]
    )
    def test_activates_numeric_reasoning_on_numeric_keywords(self, keyword: str) -> None:
        decision = route(f"What is the {keyword} for this service?")
        assert "numeric_reasoning" in decision.activated_modules, keyword

    def test_activates_multi_hop_on_comparison_keywords(self) -> None:
        assert "multi_hop" in route("Compare the two policies").activated_modules

    def test_complex_query_activates_at_least_two_modules(self) -> None:
        for query in COMPLEX_QUERIES:
            assert len(route(query).activated_modules) >= 2, query

    def test_every_activated_module_is_registered(self) -> None:
        for query in SIMPLE_QUERIES + MODERATE_QUERIES + COMPLEX_QUERIES:
            for name in route(query).activated_modules:
                assert name in AVAILABLE_MODULES

    def test_activation_has_no_duplicates(self) -> None:
        for query in COMPLEX_QUERIES:
            names = route(query).activated_modules
            assert len(names) == len(set(names))


class TestRoutingDecision:
    def test_to_dict_returns_exactly_the_contract_keys(self) -> None:
        assert set(route("What is the capital?").to_dict()) == {
            "complexity",
            "confidence",
            "retrieval_depth",
            "activated_modules",
        }

    def test_to_dict_does_not_alias_internal_list(self) -> None:
        decision = route("What is the capital?")
        payload = decision.to_dict()
        payload["activated_modules"].append("bogus")
        assert "bogus" not in decision.activated_modules

    def test_dataclass_fields(self) -> None:
        decision = RoutingDecision(
            complexity="simple",
            confidence=0.9,
            retrieval_depth=0,
            activated_modules=["fact_lookup"],
        )
        assert decision.complexity == "simple"
        assert decision.to_dict()["retrieval_depth"] == 0
