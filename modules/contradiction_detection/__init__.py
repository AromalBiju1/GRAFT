"""Contradiction-detection specialist module.

Implementation now lives in :mod:`graft.modules.contradiction_detection`; this
module re-exports it so ``from modules.contradiction_detection import
ContradictionDetectionModule`` keeps working.
"""

from graft.modules.contradiction_detection import ContradictionDetectionModule

__all__ = ["ContradictionDetectionModule"]
