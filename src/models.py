from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

class CourseMetadata(BaseModel):
    title: str = "Unknown"
    author: str = "Unknown"
    target_audience: str = "Unknown"
    subject: str = "Unknown"
    source: str = "Unknown"
    description: str = "Unknown"
    prerequisites: List[str] = Field(default_factory=list)
    learning_outcomes: List[str] = Field(default_factory=list)

class Segment(BaseModel):
    segment_id: int
    heading: Optional[str] = None
    text: str

class SectionScores(BaseModel):
    goal_focus: int = 0
    text_readability: int = 0
    pedagogical_clarity: int = 0
    prerequisite_alignment: int = 0
    fluidity_continuity: int = 0
    structural_usability: int = 0
    example_concreteness: int = 0
    example_coherence: int = 0
    business_relevance: int = 0
    instructional_alignment: int = 0

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

class CourseEvaluation(BaseModel):
    course_metadata: CourseMetadata
    overall_score: Dict[str, float]
    segments: List[EvaluatedSegment]
    evaluation_meta: Dict[str, str]
