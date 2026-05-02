"""Pydantic models for the KAM adapter's findings + report.

Strict subset of the master spec:
  - severity capped at "medium" (no "high" in v1; tier downgrade per §25.5.2).
  - confidence capped at "high" (no "verified" in v1; same reason).
  - provisional always True.
  - tier always "extracted".
  - calibration_record_id always None for now (§33.4: no records exist).

Bumping any of these caps requires writing a calibration record, building
the validated tier, and updating the spec audit checklist in the plan.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

# --- Closed-set Literals shared across the package -----------------------

FindingCode = Literal["STR-001", "STR-002", "LOAD-003"]
ValidatorId = Literal["V-CYC", "V-FWD-shallow", "V-DENS"]
SeverityV1 = Literal["low", "low-medium", "medium"]
ConfidenceV1 = Literal["low", "medium", "high"]
EvidenceSource = Literal["llm_extraction", "post_hoc_substring_match"]
SkipReason = Literal["non_instructional", "extraction_failed", "empty_segment"]


# --- Evidence + concept events ------------------------------------------

class EvidenceSpan(BaseModel):
    model_config = ConfigDict(frozen=True)

    segment_id: int
    span_start: int = Field(ge=0)
    span_end: int = Field(ge=0)
    span_text: str
    confidence: ConfidenceV1
    source: EvidenceSource


class ConceptEvent(BaseModel):
    concept_id: str
    canonical_name: str
    segment_id: int
    first_span: EvidenceSpan
    intent: Literal["mentioned"] = "mentioned"


# --- Finding -------------------------------------------------------------

class Finding(BaseModel):
    """One structural observation about a segment.

    Spec §13.3: every Finding carries structured EvidenceSpans.
    Spec §25.5.2: tier-2 downgrade — extracted-tier findings cap at
                  severity=medium, confidence=high, provisional=True.
    """

    code: FindingCode
    validator_id: ValidatorId
    validator_class: Literal["graph-deterministic"] = "graph-deterministic"
    severity: SeverityV1
    confidence: ConfidenceV1
    provisional: Literal[True] = True
    tier: Literal["extracted"] = "extracted"
    segment_id: int
    affected_concepts: List[str] = Field(default_factory=list)
    evidence: List[EvidenceSpan] = Field(default_factory=list)
    why: str
    repair_options: List[str] = Field(default_factory=list)
    calibration_record_id: Optional[str] = None
    meta_findings: List[Literal["META-002"]] = Field(default_factory=list)


# --- Module-level report -------------------------------------------------

class ExtractionMetadata(BaseModel):
    extractor_model: str
    extractor_prompt_version: str
    extracted_at: datetime
    cache_hit: bool
    extraction_concept_count: int = 0
    extraction_relation_count: int = 0
    cycle_validator_timeout_hit: bool = False
    extraction_confidence_min: ConfidenceV1 = "medium"


class ModuleGraphReport(BaseModel):
    segment_id: int
    concepts: List[ConceptEvent] = Field(default_factory=list)
    prereq_edges: List[Tuple[str, str]] = Field(default_factory=list)
    findings: List[Finding] = Field(default_factory=list)
    extraction_metadata: ExtractionMetadata
    skipped_reason: Optional[SkipReason] = None
