"""V-FWD-shallow — degraded forward-usage validator (STR-002).

For each prereq edge `A → B` within the segment:

  1. Find every span of A (canonical name + aliases) in the segment text.
  2. Find every span of B in the segment text.
  3. If A or B has zero spans, skip — we have no positional evidence.
  4. Take min(A.start) and min(B.start). If `min(B) < min(A)`, the segment
     uses B before introducing A.
  5. Before emitting STR-002, look at a window of `lookback_chars` on
     EITHER side of B's first span. Real prose typically announces the
     forward reference *after* the concept name ("Backpropagation, which
     we will see later, ..."), but some patterns also sit before. If any
     forward-reference pattern matches in either window, advance to the
     next B span and retry. Only emit if no usable B span survives the
     filter.

This is a pure character-offset comparison — no LLM, no embeddings.
Spec §33.2 (intent-aware forward usage) is the v2 upgrade; v1 calls itself
"V-FWD-shallow" to keep the degradation visible in the Finding payload.

Per §25.5.2 tier downgrade, severity caps at "medium" even though the
spec's V-FWD nominally emits at "high".
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from ..models import EvidenceSpan, Finding
from ..spans import SpanMatch, find_spans
from .base import build_finding

logger = logging.getLogger(__name__)

# 50-character lookback window for forward-reference pattern matching, per plan.
FORWARD_REFERENCE_LOOKBACK_CHARS = 50

# Default forward-reference patterns. These indicate the author is ANNOUNCING
# concept B in advance of teaching it ("we'll see X later"), so a B-before-A
# observation here is not a real sequencing violation.
DEFAULT_FORWARD_REFERENCE_PATTERNS: Tuple[str, ...] = (
    r"\bwe(?:'ll|\s+will)\s+(?:see|cover|discuss|introduce|define|return\s+to)\b",
    r"\bas\s+we(?:'ll|\s+will)\s+(?:see|discuss|cover)\b",
    r"\b(?:introduced|defined|covered)\s+later\b",
    r"\bcovered\s+in\s+(?:section|chapter|module|unit)\b",
    r"\b(?:see|cf\.)\s+(?:section|chapter|figure|table)\s+\d",
    r"\blater\s+(?:in\s+this\s+)?(?:section|chapter|module|unit|book)\b",
    r"\bfor\s+now,?\b",
    r"\b(?:before|until)\s+(?:we|you)\s+(?:learn|see|understand)\b",
)


@dataclass
class ConceptKey:
    """Minimal concept descriptor needed by V-FWD-shallow."""

    concept_id: str
    canonical_name: str
    aliases: Tuple[str, ...] = ()


@dataclass
class VFwdShallowResult:
    findings: List[Finding]
    edges_examined: int
    edges_skipped_no_spans: int
    edges_filtered_forward_ref: int


def _compile_filters(patterns: Iterable[str]) -> List[re.Pattern[str]]:
    return [re.compile(p, re.IGNORECASE) for p in patterns]


# No `(?=[A-Z0-9])` lookahead — `Pattern.finditer(text, pos, endpos)` makes
# any character past `endpos` invisible to the regex, so a lookahead at the
# window edge would silently fail to match. The simpler `[.!?]\s+` form
# occasionally treats abbreviations as sentence ends, which only *shrinks*
# the filter window and is the safer direction (lets more findings through).
_SENTENCE_BREAK_RE = re.compile(r"[.!?]\s+")


def _is_forward_reference(
    text: str,
    span: SpanMatch,
    *,
    lookback_chars: int,
    filters: Sequence[re.Pattern[str]],
) -> bool:
    # Check both sides of the span, but stop at the nearest sentence
    # boundary so a cue from a separate sentence cannot shield this span.
    # Forward-reference cues typically trail the concept name ("X, which
    # we will see later, ..."), but cues like "before we learn X" lead it.
    pre_start = max(0, span.start - lookback_chars)
    post_end = min(len(text), span.end + lookback_chars)

    # Search sentence-break boundaries on the full `text` with position
    # bounds so the regex's lookahead can peek one character past the
    # window edge. Searching on a sliced substring loses that lookahead
    # context and misses real boundaries adjacent to the span.
    pre_search_start = pre_start
    for m in _SENTENCE_BREAK_RE.finditer(text, pre_start, span.start):
        pre_search_start = m.end()
    pre_window = text[pre_search_start : span.start]

    post_search_end = post_end
    m_post = _SENTENCE_BREAK_RE.search(text, span.end, post_end)
    if m_post is not None:
        post_search_end = m_post.start()
    post_window = text[span.end : post_search_end]

    return any(p.search(pre_window) or p.search(post_window) for p in filters)


def _earliest_real_span(
    text: str,
    spans: Sequence[SpanMatch],
    *,
    lookback_chars: int,
    filters: Sequence[re.Pattern[str]],
) -> Tuple[Optional[SpanMatch], int]:
    """Return the first span not preceded by a forward-reference cue, plus
    the count of spans we filtered out before finding it.
    """
    filtered = 0
    for span in spans:
        if _is_forward_reference(text, span, lookback_chars=lookback_chars, filters=filters):
            filtered += 1
            continue
        return span, filtered
    return None, filtered


def run_v_fwd_shallow(
    *,
    segment_id: int,
    segment_text: str,
    concepts: Sequence[ConceptKey],
    prereq_edges: Sequence[Tuple[str, str]],
    forward_reference_patterns: Sequence[str] = DEFAULT_FORWARD_REFERENCE_PATTERNS,
    lookback_chars: int = FORWARD_REFERENCE_LOOKBACK_CHARS,
) -> VFwdShallowResult:
    by_id: Dict[str, ConceptKey] = {c.concept_id: c for c in concepts}
    filters = _compile_filters(forward_reference_patterns)

    findings: List[Finding] = []
    edges_examined = 0
    skipped_no_spans = 0
    filtered_total = 0

    for src_id, dst_id in prereq_edges:
        edges_examined += 1
        src = by_id.get(src_id)
        dst = by_id.get(dst_id)
        if src is None or dst is None:
            skipped_no_spans += 1
            continue

        a_spans = find_spans(segment_text, src.canonical_name, src.aliases)
        b_spans = find_spans(segment_text, dst.canonical_name, dst.aliases)
        if not a_spans or not b_spans:
            skipped_no_spans += 1
            continue

        a_first = a_spans[0]
        b_first, filtered = _earliest_real_span(
            segment_text, b_spans, lookback_chars=lookback_chars, filters=filters
        )
        filtered_total += filtered
        if b_first is None:
            # All B spans were inside forward-reference windows: not a violation.
            continue
        if b_first.start >= a_first.start:
            continue

        evidence = (
            EvidenceSpan(
                segment_id=segment_id,
                span_start=b_first.start,
                span_end=b_first.end,
                span_text=segment_text[b_first.start : b_first.end],
                confidence="medium",
                source="post_hoc_substring_match",
            ),
            EvidenceSpan(
                segment_id=segment_id,
                span_start=a_first.start,
                span_end=a_first.end,
                span_text=segment_text[a_first.start : a_first.end],
                confidence="medium",
                source="post_hoc_substring_match",
            ),
        )
        findings.append(
            build_finding(
                code="STR-002",
                validator_id="V-FWD-shallow",
                severity="medium",
                confidence="medium",
                segment_id=segment_id,
                affected_concepts=[src.concept_id, dst.concept_id],
                evidence=evidence,
                why=(
                    f"Concept {dst.canonical_name!r} (first appearance at char "
                    f"{b_first.start}) is a prerequisite of itself only if A precedes B; "
                    f"its prerequisite {src.canonical_name!r} is not introduced until "
                    f"char {a_first.start}, so the segment uses {dst.canonical_name!r} "
                    f"before introducing {src.canonical_name!r}."
                ),
                repair_options=[
                    f"Introduce {src.canonical_name!r} before first use of "
                    f"{dst.canonical_name!r}.",
                    f"Mark the first appearance of {dst.canonical_name!r} as a "
                    "deliberate forward reference (e.g., 'we will see X later').",
                ],
            )
        )

    return VFwdShallowResult(
        findings=findings,
        edges_examined=edges_examined,
        edges_skipped_no_spans=skipped_no_spans,
        edges_filtered_forward_ref=filtered_total,
    )
