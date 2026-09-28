"""Multi-hop reasoning specialist module.

Implementation now lives in :mod:`graft.modules.multi_hop`; this module
re-exports it so ``from modules.multi_hop import MultiHopModule`` keeps
working.
"""

from graft.modules.multi_hop import MultiHopModule

__all__ = ["MultiHopModule"]
