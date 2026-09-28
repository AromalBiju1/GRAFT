"""Final answer synthesis.

Implementation now lives in :mod:`graft.generation`; this module re-exports it
so ``from generation import synthesize`` keeps working.
"""

from graft.generation import synthesize

__all__ = ["synthesize"]
