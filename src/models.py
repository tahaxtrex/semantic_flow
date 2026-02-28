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
    # Critic fix Issue 8: classify segment content type
    # Values: "instructional" | "exercise" | "solution" | "reference_table"
    segment_type: str = "instructional"

class SectionScores(BaseModel):
    goal_focus: int
    text_readability: int
    pedagogical_clarity: int
    prerequisite_alignment: int
    fluidity_continuity: int
    structural_usability: int
    example_concreteness: int
    example_coherence: int
    business_relevance: int
    instructional_alignment: int

class SectionReasoning(BaseModel):
    goal_focus_rationale: str = ""
    text_readability_rationale: str = ""
    pedagogical_clarity_rationale: str = ""
    prerequisite_alignment_rationale: str = ""
    fluidity_continuity_rationale: str = ""
    structural_usability_rationale: str = ""
    example_concreteness_rationale: str = ""
    example_coherence_rationale: str = ""
    business_relevance_rationale: str = ""
    instructional_alignment_rationale: str = ""

class EvaluatedSegment(Segment):
    scores: SectionScores
    reasoning: SectionReasoning
    incomplete: bool = False

class CourseEvaluation(BaseModel):
    course_metadata: CourseMetadata
    overall_score: Dict[str, float]
    segments: List[EvaluatedSegment]
    evaluation_meta: Dict[str, Any]
