"""Fact lookup — extractive factual QA over retrieved evidence.

The module ranks retrieved passages by query-term coverage first and retrieval
score second, then returns the sentence that best answers the query instead of
dumping the whole chunk. It is deliberately deterministic and offline: the
generation stage still owns optional LLM synthesis.
"""

from __future__ import annotations

import re
from typing import Any

from modules.base import BaseModule, ModuleResult

#: Maximum characters returned as the extracted answer.
MAX_ANSWER_CHARS = 800
#: A short, label-like sentence is treated as a heading and expanded with its
#: following list item or sentence.
HEADING_MAX_CHARS = 30
HEADING_MAX_WORDS = 4
#: Maximum sentences included for heading/list answers.
MAX_ANSWER_SENTENCES = 3

#: Function words and question words that would otherwise dominate overlap.
_STOPWORDS = frozenset(
    {
        "a",
        "about",
        "all",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "been",
        "being",
        "by",
        "can",
        "compare",
        "could",
        "describe",
        "detail",
        "did",
        "difference",
        "do",
        "does",
        "each",
        "every",
        "explain",
        "find",
        "for",
        "from",
        "give",
        "has",
        "have",
        "he",
        "her",
        "here",
        "him",
        "his",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "list",
        "me",
        "might",
        "must",
        "my",
        "no",
        "not",
        "of",
        "on",
        "or",
        "our",
        "please",
        "question",
        "questions",
        "retrieve",
        "shall",
        "she",
        "should",
        "show",
        "so",
        "such",
        "tell",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "to",
        "us",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "whom",
        "whose",
        "why",
        "will",
        "with",
        "would",
        "yes",
        "you",
        "your",
    }
)

_WORD_RE = re.compile(r"[a-z0-9]+(?:['-][a-z0-9]+)?", re.I)
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")
_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*•]|\d+\s*[a-zA-Z)]|[ivxlcdm]+\s*[.)])\s+", re.I)
_LIST_QUERY_RE = re.compile(r"\b(?:list|questions?|all|every|each|items?)\b", re.I)


def _terms(text: str) -> set[str]:
    """Return content words from ``text`` (numbers count even when short)."""
    return {
        word
        for word in _WORD_RE.findall(text.lower())
        if word not in _STOPWORDS and (len(word) > 1 or word.isdigit())
    }


def _coverage(terms: set[str], text: str) -> float:
    """Fraction of query terms present in ``text``."""
    if not terms:
        return 0.0
    return len(terms & _terms(text)) / len(terms)


def _safe_score(value: Any) -> float:
    """Clamp a retrieval score to [0, 1]; missing or invalid scores become 0.5."""
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.5
    if score != score:  # NaN
        return 0.5
    return max(0.0, min(1.0, score))


def _rank_passages(
    context: list[dict[str, Any]], terms: set[str]
) -> list[tuple[float, float, int, dict[str, Any]]]:
    """Rank passages by query coverage, then retrieval score, then original order."""
    ranked: list[tuple[float, float, int, dict[str, Any]]] = []
    for index, chunk in enumerate(context):
        if not isinstance(chunk, dict):
            continue
        text = str(chunk.get("text") or "")
        ranked.append((_coverage(terms, text), _safe_score(chunk.get("score")), -index, chunk))

    if terms and any(coverage > 0.0 for coverage, _, _, _ in ranked):
        ranked.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    else:
        ranked.sort(key=lambda item: (item[1], item[2]), reverse=True)
    return ranked


def _sentences(text: str) -> list[str]:
    """Split text into sentence-like units without destroying source line breaks."""
    normalized = re.sub(r"[ \t]+", " ", text).strip()
    if not normalized:
        return []
    return [sentence.strip() for sentence in _SENTENCE_RE.split(normalized) if sentence.strip()]


def _is_heading(sentence: str) -> bool:
    """A short label such as ``Module II`` needs the next sentence to be useful."""
    words = _WORD_RE.findall(sentence)
    return len(sentence) <= HEADING_MAX_CHARS and len(words) <= HEADING_MAX_WORDS


def _is_list_item(sentence: str) -> bool:
    return bool(_LIST_ITEM_RE.match(sentence))


def _clip_answer(text: str, terms: set[str]) -> str:
    """Keep the answer within budget, preferring a window around query terms."""
    if len(text) <= MAX_ANSWER_CHARS:
        return text

    lowered = text.lower()
    positions = [lowered.find(term) for term in terms if lowered.find(term) >= 0]
    if not positions:
        return text[:MAX_ANSWER_CHARS].rstrip()

    center = min(positions)
    start = max(0, center - MAX_ANSWER_CHARS // 3)
    end = min(len(text), start + MAX_ANSWER_CHARS)
    start = max(0, end - MAX_ANSWER_CHARS)
    snippet = text[start:end].strip()
    if start > 0:
        first_space = snippet.find(" ")
        if first_space > 0:
            snippet = snippet[first_space + 1 :]
    return snippet[:MAX_ANSWER_CHARS].rstrip()


def _extract_answer(text: str, query: str) -> tuple[str, str, set[str]]:
    """Return ``(answer, selection, matched_terms)`` for one passage."""
    terms = _terms(query)
    sentences = _sentences(text)
    if not sentences:
        return "", "passage", set()

    if not terms:
        answer = _clip_answer(text.strip(), terms)
        return answer, "passage", set()

    scores = [_coverage(terms, sentence) for sentence in sentences]
    best_index = max(range(len(sentences)), key=lambda index: (scores[index], -index))
    if scores[best_index] == 0.0:
        fallback = " ".join(sentences[:2])
        answer = _clip_answer(fallback, terms)
        return answer, "passage", sorted(terms & _terms(answer))

    selected = [best_index]
    is_list_query = bool(_LIST_QUERY_RE.search(query))
    while len(selected) < MAX_ANSWER_SENTENCES:
        next_index = selected[-1] + 1
        if next_index >= len(sentences):
            break
        next_sentence = sentences[next_index]
        should_expand = _is_heading(sentences[selected[0]]) or (
            is_list_query and _is_list_item(next_sentence)
        )
        if not should_expand:
            break
        candidate = " ".join(sentences[index] for index in [*selected, next_index])
        if len(candidate) > MAX_ANSWER_CHARS:
            break
        selected.append(next_index)

    answer = " ".join(sentences[index] for index in selected).strip()
    answer = _clip_answer(answer, terms)
    matched = sorted(terms & _terms(answer))
    return answer, "sentence", matched


class FactLookupModule(BaseModule):
    name = "fact_lookup"

    def run(self, request_id: str, query: str, context: list[dict[str, Any]]) -> ModuleResult:
        if not context:
            return ModuleResult(
                request_id=request_id,
                module=self.name,
                result="No context provided.",
                evidence=[],
                confidence=0.0,
                metadata={"reason": "empty_context"},
            )

        terms = _terms(query)
        ranked = _rank_passages(context, terms)
        selected: dict[str, Any] | None = None
        answer = ""
        selection = "passage"
        matched: set[str] = set()

        for _, score, _, chunk in ranked:
            text = str(chunk.get("text") or "").strip()
            if not text:
                continue
            answer, selection, matched = _extract_answer(text, query)
            if answer:
                selected = chunk
                confidence = score
                break

        if selected is None:
            return ModuleResult(
                request_id=request_id,
                module=self.name,
                result="No answer found in context.",
                evidence=[],
                confidence=0.0,
                metadata={"retrieved_count": len(context), "reason": "blank_context"},
            )

        return ModuleResult(
            request_id=request_id,
            module=self.name,
            result=answer,
            evidence=[selected],
            confidence=confidence,
            metadata={
                "retrieved_count": len(context),
                "selection": selection,
                "matched_terms": matched,
                "query_coverage": round(_coverage(terms, answer), 3),
                "source_chunk_id": selected.get("chunk_id"),
                "answer_chars": len(answer),
            },
        )


__all__ = ["FactLookupModule"]
