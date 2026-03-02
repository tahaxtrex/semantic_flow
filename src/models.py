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


# --- TOP-LEVEL OUTPUT SCHEMA ---

class CourseEvaluation(BaseModel):
    course_metadata: CourseMetadata
    module_gate: Dict[str, Any]       # overall_score + per-dimension averages
    course_gate: CourseAssessment     # single holistic course evaluation
    segments: List[EvaluatedSegment]  # full segment data (text + module scores)
    evaluation_meta: Dict[str, Any]
