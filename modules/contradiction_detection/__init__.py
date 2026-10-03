"""Contradiction detection — compares claims across retrieved sources.

Uses lazy NLI predictions with the original surface heuristic as an offline fallback.
Output matches docs/interfaces.md section 7.4 plus structured conflict list.
"""

from __future__ import annotations

import logging
import math
import re
import threading
from typing import Any, Protocol, TypedDict

from modules.base import BaseModule, ModuleResult

logger = logging.getLogger(__name__)
NLI_MODEL = "microsoft/deberta-v3-mnli"
NLI_CONTRADICTION_THRESHOLD = 0.7


class NLIPrediction(TypedDict):
    """Label and probability returned by an NLI client."""

    label: str
    score: float


class NLIClient(Protocol):
    """Predict the relationship between a premise and a hypothesis."""

    def predict(self, premise: str, hypothesis: str) -> NLIPrediction: ...


class NLIUnavailableError(RuntimeError):
    """The NLI backend cannot provide a usable prediction."""


def _normalize_label(label: str, id2label: dict[Any, str] | None = None) -> str:
    normalized = label.strip().lower()
    if normalized.startswith("label_") and id2label:
        index = normalized.removeprefix("label_")
        if index.isdigit():
            mapped = id2label.get(int(index), id2label.get(index, label))
            normalized = mapped.strip().lower()
    if normalized not in {"contradiction", "entailment", "neutral"}:
        # Class indices have no universal ordering; never guess their meaning.
        raise NLIUnavailableError(f"Unrecognized NLI label: {label!r}")
    return normalized


class TransformersNLIClient:
    """Load a text-classification pipeline on first prediction, once per client."""

    def __init__(self) -> None:
        self._pipeline: Any = None
        self._failure: str | None = None
        self._lock = threading.Lock()

    def _load_pipeline(self) -> Any:
        with self._lock:
            if self._failure is not None:
                raise NLIUnavailableError(self._failure)
            if self._pipeline is None:
                try:
                    from transformers import pipeline

                    self._pipeline = pipeline("text-classification", model=NLI_MODEL)
                except (ImportError, OSError, RuntimeError, ValueError) as exc:
                    self._failure = f"Cannot initialize {NLI_MODEL}: {exc}"
                    raise NLIUnavailableError(self._failure) from exc
            return self._pipeline

    def predict(self, premise: str, hypothesis: str) -> NLIPrediction:
        """Classify the full text pair, truncating to the model's input limit."""
        classifier = self._load_pipeline()
        try:
            output = classifier({"text": premise, "text_pair": hypothesis}, truncation=True)
        except (ImportError, OSError, RuntimeError) as exc:
            raise NLIUnavailableError(f"NLI inference unavailable: {exc}") from exc
        prediction = output[0]
        return {
            "label": _normalize_label(prediction["label"], classifier.model.config.id2label),
            "score": float(prediction["score"]),
        }


_default_nli_client = TransformersNLIClient()


_NEGATION_RE = re.compile(
    r"\b(no[nt]?|never|not|without|banned|prohibited|deprecated|obsolete|must not|cannot)\b", re.I
)


def _has_negation(text: str) -> bool:
    return bool(_NEGATION_RE.search(text))


def _keyword_overlap(a: str, b: str) -> float:
    ta = set(re.findall(r"[a-z0-9]{3,}", a.lower()))
    tb = set(re.findall(r"[a-z0-9]{3,}", b.lower()))
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0


class ContradictionDetectionModule(BaseModule):
    """Compare overlapping passages with NLI or the original heuristic fallback."""

    name = "contradiction_detection"

    def __init__(self, nli_client: NLIClient | None = None) -> None:
        self.nli_client = nli_client if nli_client is not None else _default_nli_client

    def run(self, request_id: str, query: str, context: list[dict[str, Any]]) -> ModuleResult:
        if len(context) < 2:
            return ModuleResult(
                request_id=request_id,
                module=self.name,
                result={
                    "conflicts": [],
                    "summary": "Need at least 2 passages to detect contradictions.",
                },
                evidence=list(context),
                confidence=0.3,
                metadata={"reason": "insufficient_context"},
            )

        conflicts: list[dict[str, Any]] = []
        nli_pairs = 0
        fallback_pairs = 0
        # Preserve the existing keyword-overlap candidate selection.
        for i in range(len(context)):
            for j in range(i + 1, len(context)):
                a = str(context[i].get("text", ""))
                b = str(context[j].get("text", ""))
                overlap = _keyword_overlap(a, b)
                if overlap < 0.25:
                    continue
                try:
                    prediction = self.nli_client.predict(a, b)
                    label = _normalize_label(prediction["label"])
                    score = float(prediction["score"])
                    if not math.isfinite(score) or not 0.0 <= score <= 1.0:
                        raise NLIUnavailableError(f"Invalid NLI score: {score!r}")
                except (NLIUnavailableError, ImportError, OSError) as exc:
                    logger.warning("Using contradiction heuristic: %s", exc)
                    fallback_pairs += 1
                else:
                    nli_pairs += 1
                    if label == "contradiction" and score > NLI_CONTRADICTION_THRESHOLD:
                        conflicts.append(
                            {
                                "passage_a": {
                                    "chunk_id": context[i].get("chunk_id"),
                                    "text": a[:400],
                                },
                                "passage_b": {
                                    "chunk_id": context[j].get("chunk_id"),
                                    "text": b[:400],
                                },
                                "overlap": round(overlap, 3),
                                "signal": "nli_contradiction",
                                "message": "Potential contradiction detected by NLI.",
                                "nli_label": label,
                                "nli_score": score,
                            }
                        )
                    continue
                neg_a, neg_b = _has_negation(a), _has_negation(b)
                if neg_a != neg_b:
                    conflicts.append(
                        {
                            "passage_a": {"chunk_id": context[i].get("chunk_id"), "text": a[:400]},
                            "passage_b": {"chunk_id": context[j].get("chunk_id"), "text": b[:400]},
                            "overlap": round(overlap, 3),
                            "signal": "negation_mismatch",
                            "message": (
                                "Potential contradiction: similar topic but opposite "
                                "polarity (stub heuristic)."
                            ),
                        }
                    )

        summary = (
            f"Flagged {len(conflicts)} potential conflict(s) (heuristic, needs NLI verification)."
            if conflicts
            else "No contradictions flagged by stub heuristic."
        )
        if nli_pairs:
            method = "NLI with heuristic fallback" if fallback_pairs else "NLI"
            summary = (
                f"Flagged {len(conflicts)} potential conflict(s) ({method})."
                if conflicts
                else f"No contradictions flagged by {method}."
            )
        confidence = 0.75 if conflicts else 0.55
        return ModuleResult(
            request_id=request_id,
            module=self.name,
            result={"conflicts": conflicts, "summary": summary},  # type: ignore[arg-type]
            evidence=list(context),
            confidence=confidence,
            metadata={
                "conflicts_found": len(conflicts),
                **(
                    {"heuristic": "negation_mismatch + keyword_overlap"}
                    if not nli_pairs or fallback_pairs
                    else {}
                ),
                "nli_pairs": nli_pairs,
                "fallback_pairs": fallback_pairs,
            },
        )


__all__ = [
    "ContradictionDetectionModule",
    "NLIClient",
    "NLIPrediction",
    "NLIUnavailableError",
    "TransformersNLIClient",
]
