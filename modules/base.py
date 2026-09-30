"""Base class for specialist modules.

All modules follow docs/interfaces.md section 7 common input/output shapes.
Subclasses implement :meth:`run` and return a :class:`ModuleResult`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ModuleResult:
    request_id: str
    module: str
    result: str | dict[str, Any]
    evidence: list[dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "module": self.module,
            "result": self.result,
            "evidence": list(self.evidence),
            "confidence": max(0.0, min(1.0, float(self.confidence))),
            "metadata": dict(self.metadata),
        }


class BaseModule:
    """Abstract specialist module."""

    name: str = "base"

    def run(self, request_id: str, query: str, context: list[dict[str, Any]]) -> ModuleResult:
        raise NotImplementedError

    def __call__(self, request_id: str, query: str, context: list[dict[str, Any]]) -> ModuleResult:
        if not request_id or not str(request_id).strip():
            raise ValueError("request_id must be a non-empty string")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        if not isinstance(context, list):
            raise TypeError("context must be a list")
        return self.run(request_id=request_id, query=query, context=context)
