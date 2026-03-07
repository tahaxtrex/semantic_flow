from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class CourseMetadata(BaseModel):
    title: Optional[str] = None
    author: Optional[str] = None
    target_audience: Optional[str] = None
    subject: Optional[str] = None
    source: str = "Unknown"
    description: Optional[str] = None
    prerequisites: List[str] = Field(default_factory=list)
    learning_outcomes: List[str] = Field(default_factory=list)
    publisher: Optional[str] = None
    year: Optional[str] = None
    isbn: Optional[str] = None
    level: Optional[str] = None
    contributing_authors: List[str] = Field(default_factory=list)


class Segment(BaseModel):
    segment_id: int
    heading: Optional[str] = None
    text: str
    # Values: "instructional" | "exercise" | "solution" | "reference_table"
    segment_type: str = "instructional"


# --- MODULE GATE SCHEMAS ---
# Applied per-segment during the Module Gate evaluation.

class ModuleScores(BaseModel):
    goal_focus: int
    text_readability: int
    pedagogical_clarity: int
    example_concreteness: int
    example_coherence: int
    instructional_alignment: int


class ModuleReasoning(BaseModel):
    goal_focus_rationale: str = ""
    text_readability_rationale: str = ""
    pedagogical_clarity_rationale: str = ""
    example_concreteness_rationale: str = ""
    example_coherence_rationale: str = ""
    instructional_alignment_rationale: str = ""


class EvaluatedSegment(Segment):
    scores: ModuleScores
    reasoning: ModuleReasoning
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


class CourseReasoning(BaseModel):
    prerequisite_alignment_rationale: str = ""
    structural_usability_rationale: str = ""
    business_relevance_rationale: str = ""
    fluidity_continuity_rationale: str = ""


class CourseAssessment(BaseModel):
    scores: CourseScores
    reasoning: CourseReasoning
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

