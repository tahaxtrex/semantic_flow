"""Post-hoc evidence-span resolution.

Locates every occurrence of a concept's canonical name and aliases inside
a segment's raw text. Returns (start, end) char offsets. Used by
V-FWD-shallow to compare first-mention positions of A vs B for a given
prereq edge A→B.

Matching rules:
  - Case-insensitive — "Gradient" matches "gradient" matches "GRADIENT".
  - Word-boundary aware — "subgradient" must NOT match "gradient".
    Implemented via lookaround so we treat hyphens, digits, and letters
    as part of "word". Punctuation, whitespace, and string boundaries
    are valid edges.
  - Multi-word aliases collapse internal whitespace before matching, so
    "gradient  descent" (double space in source) still matches alias
    "gradient descent".
  - Aliases are resolved through canonicalize.normalize_concept_name first
    so a Concept with mixed-case aliases is matched consistently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Optional

from .canonicalize import canonical_aliases, normalize_concept_name

# A "word character" for our purposes: letters, digits, and hyphens.
# (Hyphens count as part of the word so "subgradient" doesn't false-match "gradient",
# and "graph-extraction" is treated as a single token.)
_WORD_CHAR = r"[A-Za-z0-9\-]"
_LEFT_BOUNDARY = rf"(?<!{_WORD_CHAR})"
_RIGHT_BOUNDARY = rf"(?!{_WORD_CHAR})"


@dataclass(frozen=True)
class SpanMatch:
    start: int
    end: int
    matched_text: str
    matched_alias: str  # the normalized alias that produced this match


def _build_alias_pattern(alias_norm: str) -> Optional[re.Pattern[str]]:
    """Build a case-insensitive regex for a single normalized alias."""
    if not alias_norm:
        return None
    # Split the alias on whitespace; allow any run of whitespace between tokens.
    tokens = alias_norm.split()
    if not tokens:
        return None
    escaped = [re.escape(tok) for tok in tokens]
    body = r"\s+".join(escaped)
    return re.compile(_LEFT_BOUNDARY + body + _RIGHT_BOUNDARY, re.IGNORECASE)


def find_spans(
    text: str,
    canonical_name: str,
    aliases: Iterable[str] = (),
) -> List[SpanMatch]:
    """Return every span where canonical_name OR any alias appears in text.

    Matches are returned in the order they appear in the text (smallest
    `start` first). If multiple aliases match overlapping ranges, all are
    returned — V-FWD-shallow only cares about the minimum start, so the
    redundancy is harmless.
    """
    if not text or not canonical_name:
        return []
    aliases_norm = canonical_aliases(canonical_name, aliases)
    matches: list[SpanMatch] = []
    for alias_norm in aliases_norm:
        pattern = _build_alias_pattern(alias_norm)
        if pattern is None:
            continue
        for m in pattern.finditer(text):
            matches.append(
                SpanMatch(
                    start=m.start(),
                    end=m.end(),
                    matched_text=m.group(0),
                    matched_alias=alias_norm,
                )
            )
    matches.sort(key=lambda s: (s.start, s.end))
    return matches


def first_span(
    text: str,
    canonical_name: str,
    aliases: Iterable[str] = (),
) -> Optional[SpanMatch]:
    spans = find_spans(text, canonical_name, aliases)
    return spans[0] if spans else None
