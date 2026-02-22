import pytest
from src.models import EvaluatedSegment, SectionScores, SectionReasoning, CourseMetadata
from src.aggregator import ScoreAggregator

def test_mathematical_average():
    aggregator = ScoreAggregator()
    metadata = CourseMetadata(title="Test", source="test.pdf")
    
    seg1 = EvaluatedSegment(
        segment_id=1, text="...",
        scores=SectionScores(goal_focus=8, text_readability=6),
        reasoning=SectionReasoning()
    )
    seg2 = EvaluatedSegment(
        segment_id=2, text="...",
        scores=SectionScores(goal_focus=4, text_readability=10),
        reasoning=SectionReasoning()
    )
    
    result = aggregator.aggregate(metadata, [seg1, seg2], "test-model")
    
    assert result.overall_score["goal_focus"] == 6.0
    assert result.overall_score["text_readability"] == 8.0
    # Unset scores default to 0.0
    assert result.overall_score["pedagogical_clarity"] == 0.0
