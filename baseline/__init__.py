"""Flat RAG baseline used for comparison benchmarks.

Implementation now lives in :mod:`graft.baseline`; this module re-exports it so
``from baseline import run_baseline`` keeps working.
"""

from graft.baseline import run_baseline

__all__ = ["run_baseline"]
