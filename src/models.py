from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

# ADR-033: Single source of truth for CourseMetadata lives in metadata.py.
# Re-exported here so existing `from src.models import CourseMetadata` imports keep working.
from src.metadata import CourseMetadata


class Segment(BaseModel):
    segment_id: int
    heading: Optional[str] = None
    text: str
    # Values: "instructional" | "preface" | "exercise" | "solution" |
    #         "reference_table" | "frontmatter" | "glossary" | "summary" |
    #         "assessment"
    # "preface" is routed to Course Gate context (ADR-040); all others except
    # "instructional" receive zero Module Gate scores and are bypassed.
    segment_type: str = "instructional"


# --- MODULE GATE SCHEMAS ---
# Applied per-segment during the Module Gate evaluation.

class ModuleScores(BaseModel):
    goal_focus: int
    text_readability: int
    pedagogical_clarity: int
    example_concreteness: int
    example_coherence: int


class ModuleReasoning(BaseModel):
    goal_focus_rationale: str = ""
    text_readability_rationale: str = ""
    pedagogical_clarity_rationale: str = ""
    example_concreteness_rationale: str = ""
    example_coherence_rationale: str = ""


class EvaluatedSegment(Segment):
    scores: ModuleScores
    reasoning: ModuleReasoning
    # Per-criterion breakdown: {"goal_focus": {"c1": 2, "c2": 1, ...}, ...}
    criteria_scores: Dict[str, Any] = Field(default_factory=dict)
    # 1-2 sentence summary of this segment, used as input to the Course Gate.
    summary: str = ""
    incomplete: bool = False


# --- COURSE GATE SCHEMAS ---
# Applied once, holistically, after all Module evaluations are done.

class CourseScores(BaseModel):
    prerequisite_alignment: int
    structural_usability: int
    business_relevance: int
    fluidity_continuity: int
    instructional_alignment: int  # ADR-016: cross-module alignment belongs at Course Gate


class CourseReasoning(BaseModel):
    prerequisite_alignment_rationale: str = ""
    structural_usability_rationale: str = ""
    business_relevance_rationale: str = ""
    fluidity_continuity_rationale: str = ""
    instructional_alignment_rationale: str = ""  # ADR-016


class CourseAssessment(BaseModel):
    scores: CourseScores
    reasoning: CourseReasoning
    # Per-criterion breakdown: {"prerequisite_alignment": {"c1": 2, ...}, ...}
    criteria_scores: Dict[str, Any] = Field(default_factory=dict)
    overall_score: float


# --- TREE-STRUCTURED ASSESSMENT OUTPUT (ADR-024) ---

class RubricResult(BaseModel):
    """A single rubric's score and rationale, used in the assessment tree."""
    score: float
    rationale: str = ""


class GateReport(BaseModel):
    """Aggregated result for one evaluation gate (Module or Course).

    overall_score is the simple mean of all rubric scores within the gate.
    rubrics maps each rubric name to its RubricResult (weighted average for
    the Module Gate; direct score for the Course Gate).
    """
    overall_score: float
    rubrics: Dict[str, RubricResult]


class AssessmentTree(BaseModel):
    """Light tree-structured view of the full evaluation (ADR-024).

    Provides a human-readable, hierarchical summary of both gate results:
      assessment
      ├── module_gate  (weighted average across segments)
      │   ├── overall_score
      │   └── rubrics: {goal_focus: {score, rationale}, ...}
      └── course_gate  (single holistic evaluation)
          ├── overall_score
          └── rubrics: {prerequisite_alignment: {score, rationale}, ...}
    """
    module_gate: GateReport
    course_gate: GateReport


# --- TOP-LEVEL OUTPUT SCHEMA ---

class CourseEvaluation(BaseModel):
    course_metadata: CourseMetadata
    assessment: AssessmentTree          # ADR-024 tree view (primary)
    module_gate: Dict[str, Any]         # flat dict kept for backwards compat
    course_gate: CourseAssessment       # single holistic course evaluation
    segments: List[EvaluatedSegment]    # full segment data (text + module scores)
    evaluation_meta: Dict[str, Any]

