"""Numeric reasoning specialist module.

Implementation now lives in :mod:`graft.modules.numeric_reasoning`; this module
re-exports it so ``from modules.numeric_reasoning import
NumericReasoningModule`` keeps working.
"""

from graft.modules.numeric_reasoning import NumericReasoningModule

__all__ = ["NumericReasoningModule"]
