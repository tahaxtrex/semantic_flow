"""String-only concept slug canonicalization.

Spec §5.1.4 describes a full canonicalization pipeline with embedding-based
near-duplicate merging at thresholds 0.78 / 0.92. Those thresholds need
calibration records the project does not yet have (§33.4.3), so v1
deliberately implements only the *string-normalization* prefix of the
pipeline. No embeddings, no cosine similarity, no auto-merge.

Two concepts collapse to the same slug iff their normalized name strings
match exactly. This is a strict subset of the spec but it is honest about
its scope and never produces an uncalibrated merge.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable

_WHITESPACE_RE = re.compile(r"\s+")
_NON_SLUG_CHAR_RE = re.compile(r"[^a-z0-9\-]+")
_MULTI_HYPHEN_RE = re.compile(r"-{2,}")


def normalize_concept_name(name: str) -> str:
    """Strip diacritics, lowercase, collapse whitespace. No tokenization."""
    if not name:
        return ""
    decomposed = unicodedata.normalize("NFKD", name)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _WHITESPACE_RE.sub(" ", stripped.strip().lower())


def slugify(name: str) -> str:
    """Deterministic slug for a concept name.

    Examples:
        "Gradient Descent"     -> "gradient-descent"
        "gradient-descent"     -> "gradient-descent"
        "GRADIENT DESCENT"     -> "gradient-descent"
        "naïve Bayes"          -> "naive-bayes"
        "GD"                   -> "gd"          (no synonym expansion in v1)
    """
    norm = normalize_concept_name(name)
    if not norm:
        return ""
    slug = norm.replace(" ", "-")
    slug = _NON_SLUG_CHAR_RE.sub("-", slug)
    slug = _MULTI_HYPHEN_RE.sub("-", slug).strip("-")
    return slug


def canonical_aliases(canonical_name: str, aliases: Iterable[str]) -> list[str]:
    """Return alias strings normalized for span matching, deduped, with the
    canonical name first. Empty strings are dropped. Order is stable.
    """
    seen: set[str] = set()
    ordered: list[str] = []
    for candidate in (canonical_name, *aliases):
        if candidate is None:
            continue
        norm = normalize_concept_name(candidate)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        ordered.append(norm)
    return ordered
