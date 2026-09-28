"""Query complexity classification and activation gating.

Implementation now lives in :mod:`graft.router`. This module re-exports it so
``from router import route`` keeps working for the teammates who own this
component, without maintaining a second copy of the gating logic.
"""

from graft.router import (
    AVAILABLE_MODULES,
    COMPLEXITY_LABELS,
    COMPLEXITY_TO_DEPTH,
    RoutingDecision,
    classify,
    complexity_score,
    route,
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
