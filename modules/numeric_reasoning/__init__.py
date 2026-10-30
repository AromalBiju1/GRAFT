"""Numeric/table reasoning — structured extraction and arithmetic.

The module parses numbers with units, currency, percentages and magnitude
suffixes, associates each value with a nearby label, and handles simple pipe or
space-aligned tables by column. It prefers values whose label matches the query
and excludes years, page numbers and document references from aggregates unless
the question explicitly asks about them.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from modules.base import BaseModule, ModuleResult

#: Maximum values returned by the generic extraction path.
MAX_EXTRACTED_NUMBERS = 10
#: Maximum distinct passages attached as evidence.
MAX_EVIDENCE_CHUNKS = 3
#: Confidence for a computed result whose operands matched the query label.
COMPUTED_CONFIDENCE = 0.9
#: Confidence when no query label was available and all values were used.
FALLBACK_CONFIDENCE = 0.85
#: Confidence when operands use mixed units.
MIXED_UNIT_CONFIDENCE = 0.75
#: Confidence when the requested operation could not be computed.
INSUFFICIENT_CONFIDENCE = 0.3
#: Confidence when no numbers were found.
NO_NUMBERS_CONFIDENCE = 0.2

#: Function words that carry no entity signal.
_STOPWORDS = frozenset(
    {
        "a",
        "about",
        "after",
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
        "between",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "each",
        "for",
        "from",
        "had",
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
        "shall",
        "she",
        "should",
        "so",
        "such",
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

#: Words that describe the requested operation rather than the entity.
_OPERATION_WORDS = frozenset(
    {
        "add",
        "altogether",
        "average",
        "combined",
        "compare",
        "comparison",
        "decrease",
        "difference",
        "extract",
        "figures",
        "growth",
        "highest",
        "increase",
        "largest",
        "lowest",
        "max",
        "maximum",
        "mean",
        "min",
        "minimum",
        "minus",
        "numbers",
        "percent",
        "percentage",
        "rate",
        "smallest",
        "subtract",
        "sum",
        "total",
        "totals",
        "values",
    }
)

#: Words ignored when turning a local phrase into an entity label.
_LABEL_STOPWORDS = frozenset(
    {
        "a",
        "about",
        "after",
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
        "between",
        "by",
        "each",
        "for",
        "from",
        "had",
        "has",
        "have",
        "in",
        "into",
        "is",
        "it",
        "its",
        "more",
        "less",
        "of",
        "on",
        "or",
        "over",
        "per",
        "that",
        "the",
        "their",
        "then",
        "there",
        "these",
        "this",
        "those",
        "to",
        "under",
        "was",
        "were",
        "with",
        "within",
        "grew",
        "grow",
        "increase",
        "increased",
        "decrease",
        "decreased",
        "rise",
        "rose",
        "fall",
        "fell",
        "reach",
        "reached",
        "total",
        "totaled",
        "totalled",
        "amount",
        "amounted",
        "equal",
        "equals",
    }
)

_CURRENCY_SYMBOLS = frozenset({"$", "€", "£", "₹", "¥"})
_MAGNITUDES = {
    "thousand": 1_000.0,
    "k": 1_000.0,
    "million": 1_000_000.0,
    "m": 1_000_000.0,
    "billion": 1_000_000_000.0,
    "b": 1_000_000_000.0,
    "bn": 1_000_000_000.0,
    "trillion": 1_000_000_000_000.0,
}

_WORD_RE = re.compile(r"[a-z0-9]+(?:['-][a-z0-9]+)?", re.I)
_NUMBER_RE = re.compile(
    r"(?<![\w.])"
    r"(?P<open>\()?"
    r"(?P<sign>[+-])?"
    r"(?P<currency>[$€£₹¥])?"
    r"(?P<number>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)"
    r"(?P<suffix>(?:\s*(?:%|percent|thousand|million|billion|trillion)\b)|(?:k|m|bn|b)(?!\w))?"
    r"(?P<close>\))?"
    r"(?![\w])",
    re.I,
)
_CLAUSE_SPLIT_RE = re.compile(
    r"(?<!\d)[.;:](?!\d)|\n|\b(?:and|but|while|whereas)\b",
    re.I,
)
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<!\d)[.!?](?!\d)")
_PAGE_PREFIX_RE = re.compile(r"\bpage\s*[.:#-]?\s*$", re.I)
_REFERENCE_PREFIX_RE = re.compile(
    r"\b(?:module|question|q|part|section|chapter|figure|fig|table|"
    r"reg(?:istration)?\s*no|roll\s*no)\s*[.:#-]?\s*$",
    re.I,
)
_YEAR_CONTEXT_RE = re.compile(r"\b(?:in|during|since|until|by|year|fy|fiscal)\s*$", re.I)
_LINKING_VERB_RE = re.compile(
    r"\b(?:was|were|is|are|reached|totaled|totalled|amounted\s+to|equals?)\b",
    re.I,
)
_SUBJECT_VERB_RE = re.compile(
    r"^(.{1,60}?)\b(?:was|were|is|are|reached|grew|grow|increased|decreased|"
    r"rose|fell|totaled|totalled|amounted|equals?|has|have)\b",
    re.I,
)
_TRAILING_PREPOSITION_RE = re.compile(r"\b(?:of|at|for|in|on|by|to|from)\s*$", re.I)
_LEADING_CONNECTOR_RE = re.compile(r"^(?:and|or|but|the|a|an|with|where|which|that)\s+", re.I)
_SEPARATOR_ROW_RE = re.compile(r"^[\s:|-]+$")


@dataclass(frozen=True, slots=True)
class NumericMention:
    """One number found in a passage, with its unit and nearby label."""

    value: float
    raw: str
    unit: str | None
    label: str
    context: str
    chunk_id: str
    chunk_index: int
    passage_score: float
    start: int
    end: int
    kind: str


@dataclass(frozen=True, slots=True)
class _TableGroup:
    """A validated table: a header row followed by numeric data rows."""

    start: int
    end: int
    header: tuple[str, ...]
    rows: tuple[tuple[int, tuple[str, ...]], ...]


def _safe_score(value: Any) -> float:
    """Clamp a retrieval score to [0, 1]; missing or invalid scores become 0.5."""
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.5
    if not math.isfinite(score):
        return 0.5
    return max(0.0, min(1.0, score))


def _query_terms(query: str) -> set[str]:
    """Return entity words from the query, excluding operation vocabulary."""
    return {
        word
        for word in _WORD_RE.findall(query.lower())
        if word not in _STOPWORDS
        and word not in _OPERATION_WORDS
        and (len(word) > 1 or word.isdigit())
    }


def _label_tokens(mention: NumericMention) -> set[str]:
    return set(_WORD_RE.findall(mention.label.lower()))


def _context_tokens(mention: NumericMention) -> set[str]:
    return set(_WORD_RE.findall(mention.context.lower()))


def _mention_relevance(mention: NumericMention, terms: set[str]) -> float:
    """Score a mention by label hits (2x) before context hits."""
    if not terms:
        return 0.0
    label_hits = len(terms & _label_tokens(mention))
    context_hits = len(terms & _context_tokens(mention))
    return (2 * label_hits + context_hits) / (2 * len(terms))


def _label_matches(mentions: list[NumericMention], terms: set[str]) -> list[NumericMention]:
    if not terms:
        return []
    return [mention for mention in mentions if terms & _label_tokens(mention)]


def _context_matches(mentions: list[NumericMention], terms: set[str]) -> list[NumericMention]:
    if not terms:
        return []
    return [mention for mention in mentions if terms & _context_tokens(mention)]


def _suffix_info(raw_suffix: str) -> tuple[float, str | None]:
    """Return ``(multiplier, unit)`` for a percent or magnitude suffix."""
    suffix = raw_suffix.strip().lower()
    if suffix in {"%", "percent"}:
        return 1.0, "%"
    if suffix in _MAGNITUDES:
        return _MAGNITUDES[suffix], None
    return 1.0, None


def _parse_match(match: re.Match[str], text: str, start: int) -> tuple[float, str | None, str]:
    """Parse one regex match into a normalised value, unit and broad kind."""
    raw_number = match.group("number")
    value = float(raw_number.replace(",", ""))
    if match.group("sign") == "-" or (match.group("open") and match.group("close")):
        value = -value

    suffix = match.group("suffix") or ""
    multiplier, suffix_unit = _suffix_info(suffix)
    value *= multiplier
    unit = match.group("currency") or suffix_unit

    prefix = text[max(0, start - 40) : start]
    if _PAGE_PREFIX_RE.search(prefix):
        kind = "page"
    elif _REFERENCE_PREFIX_RE.search(prefix):
        kind = "reference"
    elif suffix_unit == "%":
        kind = "percent"
    elif _is_year(raw_number, bool(match.group("currency")), suffix, prefix):
        kind = "year"
    else:
        kind = "number"
    return value, unit, kind


def _is_year(raw_number: str, has_currency: bool, suffix: str, prefix: str) -> bool:
    """Detect bare four-digit years used as dates rather than amounts."""
    if has_currency or suffix.strip():
        return False
    if not re.fullmatch(r"\d{4}", raw_number):
        return False
    value = int(raw_number)
    if not 1900 <= value <= 2099:
        return False
    return bool(_YEAR_CONTEXT_RE.search(prefix))


def _sentence_subject(context: str) -> str:
    """Fallback label: the sentence's subject before its first verb."""
    match = _SUBJECT_VERB_RE.match(context)
    subject = match.group(1) if match else context
    words = [
        word
        for word in _WORD_RE.findall(subject.lower())
        if word not in _LABEL_STOPWORDS and not word.isdigit()
    ]
    return " ".join(words[-3:])


def _extract_label(text: str, start: int, context: str) -> str:
    """Take the nearest noun phrase before ``start``; fall back to the subject."""
    prefix = re.sub(r"\s+", " ", text[max(0, start - 90) : start]).strip()
    prefix = _CLAUSE_SPLIT_RE.split(prefix)[-1].strip()
    prefix = _TRAILING_PREPOSITION_RE.sub("", prefix).strip()
    verbs = list(_LINKING_VERB_RE.finditer(prefix))
    if verbs:
        prefix = prefix[: verbs[-1].start()].strip()
    prefix = _LEADING_CONNECTOR_RE.sub("", prefix).strip()

    words = [
        word
        for word in _WORD_RE.findall(prefix.lower())
        if word not in _LABEL_STOPWORDS and not word.isdigit()
    ]
    if words:
        return " ".join(words[-3:])
    return _sentence_subject(context)


def _context_around(text: str, start: int, end: int) -> str:
    """Return the sentence or line containing ``start``, ignoring decimal points."""
    left = 0
    for boundary in _SENTENCE_BOUNDARY_RE.finditer(text, 0, start):
        left = boundary.end()
    newline_left = text.rfind("\n", 0, start)
    if newline_left >= left:
        left = newline_left + 1

    right = len(text)
    boundary = _SENTENCE_BOUNDARY_RE.search(text, end)
    if boundary:
        right = boundary.end()
    newline_right = text.find("\n", end)
    if 0 <= newline_right < right:
        right = newline_right
    return re.sub(r"\s+", " ", text[left:right]).strip()


def _table_cells(line: str) -> tuple[str, tuple[str, ...]] | None:
    """Split a pipe or space-aligned table row, or return ``None``."""
    stripped = line.strip()
    if not stripped:
        return None
    if "|" in stripped and stripped.count("|") >= 2:
        cells = tuple(cell.strip() for cell in stripped.strip("|").split("|"))
        return ("pipe", cells) if len(cells) >= 2 else None
    cells = tuple(cell for cell in re.split(r"\s{2,}", stripped) if cell)
    return ("aligned", cells) if len(cells) >= 2 else None


def _validated_group(
    start: int, end: int, rows: list[tuple[int, tuple[str, ...]]]
) -> _TableGroup | None:
    """Validate a candidate group and keep only rows with numeric data."""
    if len(rows) < 2:
        return None
    header = rows[0][1]
    width = len(header)
    if width < 2 or any(_NUMBER_RE.search(cell) for cell in header):
        return None
    if not any(re.search(r"[A-Za-z]", cell) for cell in header):
        return None

    data_rows: list[tuple[int, tuple[str, ...]]] = []
    for offset, cells in rows[1:]:
        if len(cells) != width:
            continue
        if all(not cell or _SEPARATOR_ROW_RE.match(cell) for cell in cells):
            continue
        if any(_NUMBER_RE.search(cell) for cell in cells):
            data_rows.append((offset, cells))
    if not data_rows:
        return None
    return _TableGroup(start=start, end=end, header=header, rows=tuple(data_rows))


def _table_groups(text: str) -> list[_TableGroup]:
    """Find consecutive pipe or aligned rows that form a numeric table."""
    groups: list[_TableGroup] = []
    current_kind: str | None = None
    current_rows: list[tuple[int, tuple[str, ...]]] = []
    current_start = 0
    current_end = 0
    offset = 0

    for line in text.splitlines(keepends=True):
        parsed = _table_cells(line.rstrip("\r\n"))
        kind = parsed[0] if parsed else None
        if parsed and kind == current_kind:
            current_rows.append((offset, parsed[1]))
            current_end = offset + len(line)
        else:
            if current_rows:
                group = _validated_group(current_start, current_end, current_rows)
                if group is not None:
                    groups.append(group)
            if parsed:
                current_kind = kind
                current_rows = [(offset, parsed[1])]
                current_start = offset
                current_end = offset + len(line)
            else:
                current_kind = None
                current_rows = []
        offset += len(line)

    if current_rows:
        group = _validated_group(current_start, current_end, current_rows)
        if group is not None:
            groups.append(group)
    return groups


def _normalize_header(cell: str) -> str:
    return " ".join(_WORD_RE.findall(cell.lower()))


def _parse_table_mentions(
    text: str,
    group: _TableGroup,
    chunk_id: str,
    chunk_index: int,
    passage_score: float,
) -> list[NumericMention]:
    """Create one mention per numeric cell, labelled by its column header."""
    mentions: list[NumericMention] = []
    for row_offset, cells in group.rows:
        line_start = text.rfind("\n", 0, row_offset) + 1
        line_end = text.find("\n", row_offset)
        line = text[line_start : line_end if line_end >= 0 else len(text)]
        for column, cell in enumerate(cells):
            if column >= len(group.header):
                continue
            label = _normalize_header(group.header[column])
            for match in _NUMBER_RE.finditer(cell):
                cell_start = line.find(cell)
                if cell_start < 0:
                    cell_start = 0
                start = line_start + cell_start + match.start()
                end = start + len(match.group(0))
                value, unit, _ = _parse_match(match, text, start)
                kind = "year" if "year" in label else "table"
                mentions.append(
                    NumericMention(
                        value=value,
                        raw=match.group(0).strip(),
                        unit=unit,
                        label=label,
                        context=" | ".join(cells),
                        chunk_id=chunk_id,
                        chunk_index=chunk_index,
                        passage_score=passage_score,
                        start=start,
                        end=end,
                        kind=kind,
                    )
                )
    return mentions


def _parse_general_mentions(
    text: str,
    chunk_id: str,
    chunk_index: int,
    passage_score: float,
    skip_ranges: list[tuple[int, int]],
) -> list[NumericMention]:
    """Parse numbers outside table ranges, attaching labels and context."""
    mentions: list[NumericMention] = []
    for match in _NUMBER_RE.finditer(text):
        start, end = match.start(), match.end()
        if any(range_start <= start < range_end for range_start, range_end in skip_ranges):
            continue
        value, unit, kind = _parse_match(match, text, start)
        context = _context_around(text, start, end)
        mentions.append(
            NumericMention(
                value=value,
                raw=match.group(0).strip(),
                unit=unit,
                label=_extract_label(text, start, context),
                context=context,
                chunk_id=chunk_id,
                chunk_index=chunk_index,
                passage_score=passage_score,
                start=start,
                end=end,
                kind=kind,
            )
        )
    return mentions


def _dedupe_mentions(mentions: list[NumericMention]) -> list[NumericMention]:
    unique: dict[tuple[str, int, int], NumericMention] = {}
    for mention in mentions:
        unique.setdefault((mention.chunk_id, mention.start, mention.end), mention)
    return sorted(unique.values(), key=lambda item: (item.chunk_index, item.start, item.end))


def _parse_context(context: list[dict[str, Any]]) -> list[NumericMention]:
    """Parse every unique passage and return mentions in document order."""
    mentions: list[NumericMention] = []
    seen_texts: set[str] = set()

    for chunk_index, chunk in enumerate(context):
        if not isinstance(chunk, dict):
            continue
        text = str(chunk.get("text") or "")
        stripped = text.strip()
        if not stripped or stripped in seen_texts:
            continue
        seen_texts.add(stripped)

        chunk_id = str(chunk.get("chunk_id") or chunk.get("node_id") or f"chunk_{chunk_index}")
        passage_score = _safe_score(chunk.get("score"))
        groups = _table_groups(text)
        skip_ranges = [(group.start, group.end) for group in groups]
        for group in groups:
            mentions.extend(
                _parse_table_mentions(text, group, chunk_id, chunk_index, passage_score)
            )
        mentions.extend(
            _parse_general_mentions(text, chunk_id, chunk_index, passage_score, skip_ranges)
        )
    return _dedupe_mentions(mentions)


def _operation(query: str) -> str:
    """Choose the arithmetic operation requested by ``query``."""
    lowered = query.lower()
    if re.search(
        r"\b(?:percentage|percent)\s+(?:increase|decrease|change|growth)\b"
        r"|\bgrowth\s+rate\b|\bpercent\s+growth\b|%\s*(?:increase|decrease|change)",
        lowered,
    ):
        return "percent_change"
    if re.search(
        r"\b(?:difference|subtract|minus|gap|compare|comparison)\b"
        r"|\bhow much (?:more|less)\b|\bversus\b|\bvs\b",
        lowered,
    ):
        return "difference"
    if re.search(r"\b(?:average|mean)\b", lowered):
        return "average"
    if re.search(r"\b(?:minimum|lowest|smallest|least)\b", lowered):
        return "min"
    if re.search(r"\b(?:maximum|highest|largest|greatest)\b", lowered):
        return "max"
    if re.search(r"\b(?:sum|total|totals|add|altogether|combined)\b", lowered):
        return "sum"
    return "extract"


def _query_mentions_years(query: str, terms: set[str]) -> bool:
    return "year" in query.lower() or any(
        re.fullmatch(r"(?:19|20)\d{2}", term) for term in terms
    )


def _query_term_position(query: str, term: str) -> int:
    match = re.search(rf"(?<!\w){re.escape(term)}(?!\w)", query.lower())
    return match.start() if match else 10**9


def _sort_by_query_order(
    mentions: list[NumericMention], query: str, terms: set[str]
) -> list[NumericMention]:
    """Order operands by where their entity appears in the question."""
    if not terms:
        return sorted(mentions, key=lambda item: (item.chunk_index, item.start))

    def position(mention: NumericMention) -> int:
        if mention.kind == "year":
            year_position = _query_term_position(query, mention.raw.strip("()$€£₹¥,."))
            if year_position < 10**9:
                return year_position
        matched = terms & _label_tokens(mention)
        if not matched:
            matched = terms & _context_tokens(mention)
        positions = [_query_term_position(query, term) for term in matched]
        return min(positions) if positions else 10**9

    return sorted(mentions, key=lambda item: (position(item), item.chunk_index, item.start))


def _select_for_operation(
    mentions: list[NumericMention],
    query: str,
    operation: str,
    terms: set[str],
) -> tuple[list[NumericMention], int]:
    """Select operands for ``operation`` and count excluded year mentions."""
    usable = [mention for mention in mentions if mention.kind not in {"page", "reference"}]
    if not usable:
        return [], 0

    if operation == "extract":
        ranked = sorted(
            usable,
            key=lambda item: (
                -_mention_relevance(item, terms),
                -item.passage_score,
                item.chunk_index,
                item.start,
            ),
        )
        return ranked[:MAX_EXTRACTED_NUMBERS], 0

    matched = _label_matches(usable, terms)
    if not matched:
        matched = _context_matches(usable, terms)
    if not matched:
        matched = usable

    excluded_years = 0
    if operation in {"sum", "average", "min", "max", "percent_change"}:
        non_years = [mention for mention in matched if mention.kind != "year"]
        if non_years:
            excluded_years = len(matched) - len(non_years)
            matched = non_years
    elif operation == "difference":
        if _query_mentions_years(query, terms):
            year_terms = {
                term for term in terms if re.fullmatch(r"(?:19|20)\d{2}", term)
            }
            year_matches = [
                mention
                for mention in matched
                if mention.kind == "year"
                and mention.raw.strip("()$€£₹¥,.") in year_terms
            ]
            if year_matches:
                matched = year_matches
        else:
            non_years = [mention for mention in matched if mention.kind != "year"]
            if non_years:
                excluded_years = len(matched) - len(non_years)
                matched = non_years

    if operation in {"difference", "percent_change"}:
        matched = _sort_by_query_order(matched, query, terms)
    return matched, excluded_years


def _compute(operation: str, values: list[float]) -> float | None:
    """Apply a supported arithmetic operation to ``values``."""
    if operation == "sum":
        return sum(values)
    if operation == "average":
        return sum(values) / len(values) if values else None
    if operation == "difference":
        return values[0] - values[1] if len(values) >= 2 else None
    if operation == "percent_change":
        if len(values) < 2 or values[0] == 0:
            return None
        return (values[1] - values[0]) / abs(values[0]) * 100.0
    if operation == "min":
        return min(values) if values else None
    if operation == "max":
        return max(values) if values else None
    return None


def _common_unit(mentions: list[NumericMention]) -> tuple[str | None, bool]:
    """Return a shared unit and whether the operands mix units."""
    units = {mention.unit for mention in mentions if mention.unit}
    if len(units) == 1:
        unit = next(iter(units))
        missing = any(mention.unit is None for mention in mentions)
        return unit, missing
    return None, len(units) > 1


def _format_value(value: float | int, unit: str | None) -> str:
    text = f"{float(value):,.2f}".rstrip("0").rstrip(".")
    if unit == "%":
        return f"{text}%"
    if unit in _CURRENCY_SYMBOLS:
        return f"{unit}{text}"
    return text


def _build_payload(
    operation: str,
    selected: list[NumericMention],
) -> tuple[dict[str, Any], str | None, bool, float | None]:
    """Return the public result payload plus unit/confidence helpers."""
    values = [mention.value for mention in selected]
    if operation == "extract":
        result_value: float | list[float] | None = values
        unit, mixed = None, False
    else:
        result_value = _compute(operation, values)
        unit, mixed = _common_unit(selected)
        if operation == "percent_change":
            unit, mixed = "%", False

    formatted = (
        _format_value(result_value, unit)
        if isinstance(result_value, (int, float)) and not isinstance(result_value, bool)
        else None
    )
    details = [
        {
            "value": mention.value,
            "raw": mention.raw,
            "label": mention.label,
            "unit": mention.unit,
            "chunk_id": mention.chunk_id,
            "kind": mention.kind,
        }
        for mention in selected
    ]
    payload = {
        "result": result_value,
        "numbers_found": values,
        "count": len(values),
        "operands": values,
        "operand_details": details,
        "unit": unit,
        "formatted": formatted,
    }
    return payload, unit, mixed, result_value


def _evidence_for(
    selected: list[NumericMention], context: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Return only the passages that contributed at least one operand."""
    by_id: dict[str, dict[str, Any]] = {}
    for index, chunk in enumerate(context):
        if not isinstance(chunk, dict):
            continue
        chunk_id = str(chunk.get("chunk_id") or chunk.get("node_id") or f"chunk_{index}")
        by_id.setdefault(chunk_id, chunk)

    evidence: list[dict[str, Any]] = []
    seen: set[str] = set()
    for mention in selected:
        if mention.chunk_id in seen:
            continue
        chunk = by_id.get(mention.chunk_id)
        if chunk is None:
            continue
        evidence.append(chunk)
        seen.add(mention.chunk_id)
        if len(evidence) >= MAX_EVIDENCE_CHUNKS:
            break
    return evidence


class NumericReasoningModule(BaseModule):
    name = "numeric_reasoning"

    def run(self, request_id: str, query: str, context: list[dict[str, Any]]) -> ModuleResult:
        mentions = _parse_context(context)
        operation = _operation(query)
        terms = _query_terms(query)

        if not mentions:
            payload, _, mixed, _ = _build_payload("extract", [])
            payload["result"] = None
            return ModuleResult(
                request_id=request_id,
                module=self.name,
                result=payload,
                evidence=[],
                confidence=NO_NUMBERS_CONFIDENCE,
                metadata={
                    "operation": "no_numbers",
                    "candidate_count": 0,
                    "operand_count": 0,
                    "selected_labels": [],
                    "excluded_years": 0,
                    "table_used": False,
                    "mixed_units": mixed,
                    "reason": "no_numbers",
                },
            )

        selected, excluded_years = _select_for_operation(mentions, query, operation, terms)
        payload, _, mixed, result_value = _build_payload(operation, selected)

        reason: str | None = None
        if operation != "extract" and result_value is None:
            reason = "insufficient_operands"
            confidence = INSUFFICIENT_CONFIDENCE
        elif operation == "extract":
            confidence = 0.55 if selected else NO_NUMBERS_CONFIDENCE
        else:
            label_matched = bool(_label_matches(selected, terms))
            confidence = COMPUTED_CONFIDENCE if label_matched else FALLBACK_CONFIDENCE
            if not terms:
                confidence = FALLBACK_CONFIDENCE
            if mixed:
                confidence = MIXED_UNIT_CONFIDENCE

        metadata: dict[str, Any] = {
            "operation": operation,
            "candidate_count": len(mentions),
            "operand_count": len(selected),
            "selected_labels": sorted({mention.label for mention in selected if mention.label}),
            "excluded_years": excluded_years,
            "table_used": any(mention.kind == "table" for mention in selected),
            "mixed_units": mixed,
        }
        if reason:
            metadata["reason"] = reason

        return ModuleResult(
            request_id=request_id,
            module=self.name,
            result=payload,
            evidence=_evidence_for(selected, context),
            confidence=confidence,
            metadata=metadata,
        )


__all__ = ["NumericReasoningModule"]
