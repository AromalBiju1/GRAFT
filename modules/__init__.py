"""Specialist reasoning modules activated by the router.

Implementations now live in :mod:`graft.modules.*`; this module re-exports the
shared base types so the ``modules`` package is a single import surface.
"""

from graft.modules.base import BaseModule, ModuleResult

__all__ = ["BaseModule", "ModuleResult"]
