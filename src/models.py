from pydantic import BaseModel, Field, conint
from typing import Optional, Dict, List

class PedagogicalScores(BaseModel):
    """Pedagogical quality scores on a scale of 1 to 10."""
    goal_focus: conint(ge=1, le=10) = Field(..., description="Focus on core concepts vs fluff.")
    text_readability: conint(ge=1, le=10) = Field(..., description="Accessibility of language and terminology.")
    pedagogical_clarity: conint(ge=1, le=10) = Field(..., description="Avoidance of jargon and sentence appropriateness.")
    prerequisite_alignment: conint(ge=1, le=10) = Field(..., description="Foundational concept order and prerequisite clarity.")
    fluidity_continuity: conint(ge=1, le=10) = Field(..., description="Coherence of transitions and narrative progression.")
    structural_usability: conint(ge=1, le=10) = Field(..., description="Logical organization and macro-structural clarity.")
    example_concreteness: conint(ge=1, le=10) = Field(..., description="Real-world plausibility and relatability of examples.")
    example_coherence: conint(ge=1, le=10) = Field(..., description="Thematic consistency of examples across the segment.")

class CourseMetadata(BaseModel):
    """Unified metadata for a course, extracted from various sources."""
    title: Optional[str] = "Unknown Title"
    author: Optional[str] = "Unknown Author"
    description: Optional[str] = "No description provided."
    learning_outcomes: List[str] = Field(default_factory=list)
    prerequisites: List[str] = Field(default_factory=list)
    source: str = "embedded"  # e.g., 'url', 'pdf', 'json'

class EvaluationResult(BaseModel):
    """The final evaluation object returned by the LLM."""
    scores: PedagogicalScores
    overall_observations: str = Field(..., description="A brief (max 100 words) summary of pedagogical strengths and weaknesses.")

class SegmentMetadata(BaseModel):
    """Metadata for a processed segment."""
    course_id: str
    segment_index: int
    title: str
    start_page: int
    end_page: int
    char_count: int

class CourseReport(BaseModel):
    """Aggregated results for an entire course."""
    course_name: str
    average_scores: Dict[str, float]
    segment_evaluations: Dict[int, EvaluationResult]
