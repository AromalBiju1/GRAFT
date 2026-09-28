"""Fact-lookup specialist module.

Implementation now lives in :mod:`graft.modules.fact_lookup`; this module
re-exports it so ``from modules.fact_lookup import FactLookupModule`` keeps
working.
"""

from graft.modules.fact_lookup import FactLookupModule

__all__ = ["FactLookupModule"]
