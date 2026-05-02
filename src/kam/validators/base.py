"""Shared helpers for the v1 validators.

`build_finding` centralizes the severity/confidence cap so individual
validators cannot accidentally emit `severity="high"` or
`confidence="verified"` — those are forbidden in v1 by spec §25.5.2 tier
downgrade. Pydantic enforces this at the type level (Literal-restricted
fields), but `build_finding` makes the cap visible at the call site.
"""

from __future__ import annotations

from typing import Iterable, Optional

from ..models import (
    ConfidenceV1,
    EvidenceSpan,
    Finding,
    FindingCode,
    SeverityV1,
    ValidatorId,
)

# Severity ranking for "<= cap" comparisons. Mirrors the Literal order in models.SeverityV1.
_SEVERITY_RANK = {"low": 0, "low-medium": 1, "medium": 2}
_V1_SEVERITY_CAP = "medium"
_CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}
_V1_CONFIDENCE_CAP = "high"


def _cap_severity(severity: SeverityV1) -> SeverityV1:
    if _SEVERITY_RANK[severity] > _SEVERITY_RANK[_V1_SEVERITY_CAP]:
        return _V1_SEVERITY_CAP  # type: ignore[return-value]
    return severity


def _cap_confidence(confidence: ConfidenceV1) -> ConfidenceV1:
    if _CONFIDENCE_RANK[confidence] > _CONFIDENCE_RANK[_V1_CONFIDENCE_CAP]:
        return _V1_CONFIDENCE_CAP  # type: ignore[return-value]
    return confidence


def build_finding(
    *,
    code: FindingCode,
    validator_id: ValidatorId,
    severity: SeverityV1,
    confidence: ConfidenceV1,
    segment_id: int,
    why: str,
    affected_concepts: Optional[Iterable[str]] = None,
    evidence: Optional[Iterable[EvidenceSpan]] = None,
    repair_options: Optional[Iterable[str]] = None,
    meta_findings: Optional[Iterable[str]] = None,
) -> Finding:
    return Finding(
        code=code,
        validator_id=validator_id,
        severity=_cap_severity(severity),
        confidence=_cap_confidence(confidence),
        segment_id=segment_id,
        affected_concepts=list(affected_concepts or []),
        evidence=list(evidence or []),
        why=why,
        repair_options=list(repair_options or []),
        meta_findings=list(meta_findings or []),  # type: ignore[arg-type]
    )
