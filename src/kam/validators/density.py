"""V-DENS — concept density (graph-deterministic, LOAD-003).

Density = len(concepts_in_segment) / sentence_count(segment.text).

Crucial honesty: the denominator is computed from the **raw segment text**,
not from KAM's `meaning_units` (which would re-introduce LLM variance).
Sentence count uses a regex over `[.!?]` followed by whitespace + capital
letter, with a lookbehind that excludes common abbreviations.

Threshold default 1.0 concepts/sentence is **uncalibrated**; every emitted
finding carries `meta_findings=["META-002"]` (spec §33.4.2 stale-calibration
warning) and a `provisional=True` flag enforced by the Finding model.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence

from ..models import EvidenceSpan, Finding
from .base import build_finding

logger = logging.getLogger(__name__)

V_DENS_DEFAULT_THRESHOLD = 1.0

# Common abbreviations that end in a period but do NOT terminate a sentence.
_ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr",
    "e.g", "i.e", "etc", "vs", "cf", "fig", "eq", "approx",
    "no", "vol", "sec",
    "st", "mt", "rd", "ave",
    "u.s", "u.k",
}

# Pattern: a period/!/? possibly followed by closing quote/paren, then mandatory
# whitespace, then a capital letter or digit (next-sentence start).
_SENTENCE_BOUNDARY_RE = re.compile(
    r"(?P<terminator>[.!?])(?P<close>[\"'\)\]]?)\s+(?=[A-Z0-9])"
)
_TOKEN_BEFORE_PERIOD_RE = re.compile(r"([A-Za-z][A-Za-z\.]*)\Z")


@dataclass
class VDensResult:
    findings: List[Finding]
    concept_count: int
    sentence_count: int
    density: float


def count_sentences(text: str) -> int:
    """Conservative sentence count over raw text.

    Splits on terminator + whitespace + uppercase/digit. Skips boundaries
    where the token immediately before the terminator is a known
    abbreviation. The minimum reportable count is 1 if there's any
    non-empty text — a one-sentence segment must not divide-by-zero.
    """
    if not text or not text.strip():
        return 0

    boundaries = 0
    for match in _SENTENCE_BOUNDARY_RE.finditer(text):
        prefix = text[: match.start()]
        prev_token = _TOKEN_BEFORE_PERIOD_RE.search(prefix)
        if prev_token is not None:
            token = prev_token.group(1).rstrip(".").lower()
            if token in _ABBREVIATIONS:
                continue
        boundaries += 1
    sentence_count = boundaries + 1
    # If the trailing chars don't end in a terminator, we still treat the tail as a sentence.
    return max(sentence_count, 1)


def run_v_dens(
    *,
    segment_id: int,
    segment_text: str,
    concept_count: int,
    threshold: float = V_DENS_DEFAULT_THRESHOLD,
    extra_evidence: Iterable[EvidenceSpan] = (),
    affected_concepts: Optional[Sequence[str]] = None,
) -> VDensResult:
    sentences = count_sentences(segment_text)
    if sentences == 0:
        density = 0.0
    else:
        density = concept_count / sentences

    findings: List[Finding] = []
    if sentences > 0 and density > threshold:
        findings.append(
            build_finding(
                code="LOAD-003",
                validator_id="V-DENS",
                severity="low-medium",
                confidence="medium",
                segment_id=segment_id,
                affected_concepts=affected_concepts or [],
                evidence=list(extra_evidence),
                why=(
                    f"Concept density {concept_count}/{sentences} ≈ {density:.2f} "
                    f"exceeds uncalibrated threshold {threshold}."
                ),
                repair_options=[
                    "Split the section so each chunk introduces fewer concepts.",
                    "Add bridging prose or examples around the densest concept clusters.",
                ],
                meta_findings=["META-002"],
            )
        )

    return VDensResult(
        findings=findings,
        concept_count=concept_count,
        sentence_count=sentences,
        density=density,
    )
