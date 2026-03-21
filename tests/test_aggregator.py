import pytest
from src.models import (
    EvaluatedSegment, ModuleScores, ModuleReasoning,
    CourseMetadata, CourseAssessment, CourseScores, CourseReasoning,
)
from src.aggregator import ScoreAggregator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _course_assessment(pa=7, su=8, br=6, fc=7, ia=7, overall=7.0) -> CourseAssessment:
    return CourseAssessment(
        scores=CourseScores(
            prerequisite_alignment=pa,
            structural_usability=su,
            business_relevance=br,
            fluidity_continuity=fc,
            instructional_alignment=ia,  # ADR-028
        ),
        reasoning=CourseReasoning(),
        overall_score=overall,
    )


def _seg(sid: int, text: str, segment_type: str = "instructional",
         incomplete: bool = False, **scores) -> EvaluatedSegment:
    # Module Gate now scores 5 rubrics (instructional_alignment moved to Course Gate — ADR-028)
    defaults = dict(
        goal_focus=5, text_readability=5, pedagogical_clarity=5,
        example_concreteness=5, example_coherence=5,
    )
    defaults.update(scores)
    return EvaluatedSegment(
        segment_id=sid,
        text=text,
        segment_type=segment_type,
        incomplete=incomplete,
        scores=ModuleScores(**defaults),
        reasoning=ModuleReasoning(),
    )


# ---------------------------------------------------------------------------
# Test 1: Weighted average is character-length proportional
# ---------------------------------------------------------------------------

def test_weighted_average_proportional():
    """Segment with 2x more text gets 2x the weight in the final average."""
    aggregator = ScoreAggregator()
    metadata = CourseMetadata(title="Test", source="test.pdf")

    # text_a is 10 chars (weight 1/3), text_b is 20 chars (weight 2/3)
    seg_a = _seg(1, "a" * 10, goal_focus=9)   # 9 with weight 1/3
    seg_b = _seg(2, "b" * 20, goal_focus=3)   # 3 with weight 2/3
    # Expected: 9*(10/30) + 3*(20/30) = 3.0 + 2.0 = 5.0

    result = aggregator.aggregate(metadata, [seg_a, seg_b], _course_assessment())
    assert result.module_gate["goal_focus"] == pytest.approx(5.0, abs=0.05)


# ---------------------------------------------------------------------------
# Test 2: Non-instructional segments are excluded from module gate average
# ---------------------------------------------------------------------------

def test_non_instructional_excluded_from_average():
    """Non-instructional segment scores (even nonzero) must NOT affect the average."""
    aggregator = ScoreAggregator()
    metadata = CourseMetadata(title="Test", source="test.pdf")

    # Both segments have equal text length so weights are equal.
    # If exercise were included, goal_focus average would be (8 + 2) / 2 = 5.0
    # If excluded, it should be 8.0 exactly.
    instructional = _seg(1, "A" * 100, goal_focus=8, text_readability=8,
                         pedagogical_clarity=8, example_concreteness=8,
                         example_coherence=8)
    exercise = _seg(2, "B" * 100, segment_type="exercise", goal_focus=2,
                    text_readability=2, pedagogical_clarity=2,
                    example_concreteness=2, example_coherence=2)

    result = aggregator.aggregate(metadata, [instructional, exercise], _course_assessment())

    assert result.module_gate["goal_focus"] == pytest.approx(8.0, abs=0.01)
    assert result.evaluation_meta["instructional_segments_scored"] == 1
    assert result.evaluation_meta["excluded_segments"] == 1
    assert result.evaluation_meta["total_segments"] == 2


# ---------------------------------------------------------------------------
# Test 3: Incomplete instructional segments are also excluded
# ---------------------------------------------------------------------------

def test_incomplete_instructional_excluded():
    """Instructional segments marked incomplete=True must not affect the average.

    This exercises the `not getattr(s, 'incomplete', False)` guard in aggregate().
    """
    aggregator = ScoreAggregator()
    metadata = CourseMetadata(title="Test", source="test.pdf")

    good = _seg(1, "G" * 100, goal_focus=10, text_readability=10,
                pedagogical_clarity=10, example_concreteness=10,
                example_coherence=10)
    bad = _seg(2, "B" * 100, incomplete=True, goal_focus=0, text_readability=0,
               pedagogical_clarity=0, example_concreteness=0,
               example_coherence=0)

    result = aggregator.aggregate(metadata, [good, bad], _course_assessment())

    # Only the good (complete) segment should contribute: all 10s → overall 10
    assert result.module_gate["goal_focus"] == pytest.approx(10.0, abs=0.01)
    # Incomplete segment is counted in total but NOT in instructional_segments_scored
    # (it is instructional by type but excluded by the incomplete flag)
    assert result.evaluation_meta["instructional_segments_scored"] == 1


# ---------------------------------------------------------------------------
# Test 4: All-instructional-incomplete → module gate zeros, no crash
# ---------------------------------------------------------------------------

def test_all_incomplete_gives_zeros():
    aggregator = ScoreAggregator()
    metadata = CourseMetadata(title="Test", source="test.pdf")

    seg = _seg(1, "X" * 100, incomplete=True, goal_focus=8, text_readability=8,
               pedagogical_clarity=8, example_concreteness=8,
               example_coherence=8)

    result = aggregator.aggregate(metadata, [seg], _course_assessment())
    assert result.module_gate["goal_focus"] == 0.0


# ---------------------------------------------------------------------------
# Test 5: Course gate assessment is passed through unchanged
# ---------------------------------------------------------------------------

def test_course_gate_passthrough():
    aggregator = ScoreAggregator()
    metadata = CourseMetadata(title="Test", source="test.pdf")
    ca = _course_assessment(pa=9, su=4, br=7, fc=6, overall=6.5)

    result = aggregator.aggregate(metadata, [_seg(1, "text")], ca)

    assert result.course_gate.overall_score == 6.5
    assert result.course_gate.scores.prerequisite_alignment == 9
    assert result.course_gate.scores.structural_usability == 4


# ---------------------------------------------------------------------------
# Test 6: Empty segment list → all zeros, no crash
# ---------------------------------------------------------------------------

def test_empty_segments_zero_scores():
    aggregator = ScoreAggregator()
    result = aggregator.aggregate(
        CourseMetadata(title="Test", source="test.pdf"),
        [],
        _course_assessment()
    )
    assert result.module_gate["goal_focus"] == 0.0
    assert result.module_gate["overall_score"] == 0.0
    assert result.evaluation_meta["total_segments"] == 0
    assert result.evaluation_meta["instructional_segments_scored"] == 0


# ---------------------------------------------------------------------------
# Test 7: module_gate overall_score is the mean of all 6 dimension scores
# ---------------------------------------------------------------------------

def test_module_gate_overall_is_mean_of_dimensions():
    """overall_score must equal the straight mean of the 6 weighted dimension scores."""
    aggregator = ScoreAggregator()
    metadata = CourseMetadata(title="Test", source="test.pdf")

    # Single segment, all scores identical → overall must equal that score
    seg = _seg(1, "text " * 10, goal_focus=6, text_readability=6,
               pedagogical_clarity=6, example_concreteness=6,
               example_coherence=6)
    result = aggregator.aggregate(metadata, [seg], _course_assessment())

    dims = ["goal_focus", "text_readability", "pedagogical_clarity",
            "example_concreteness", "example_coherence"]
    dim_mean = sum(result.module_gate[d] for d in dims) / 5
    assert result.module_gate["overall_score"] == pytest.approx(dim_mean, abs=0.01)
    assert result.module_gate["overall_score"] == pytest.approx(6.0, abs=0.01)

# ---------------------------------------------------------------------------
# Tests 8-10: AssessmentTree structure (ADR-024)
# ---------------------------------------------------------------------------

def test_assessment_tree_structure():
    """CourseEvaluation.assessment must be an AssessmentTree with both gates."""
    from src.models import AssessmentTree, GateReport
    aggregator = ScoreAggregator()
    metadata = CourseMetadata(title="Test", source="test.pdf")
    seg = _seg(1, "content " * 50, goal_focus=7, text_readability=6,
               pedagogical_clarity=8, example_concreteness=5,
               example_coherence=7)
    result = aggregator.aggregate(metadata, [seg], _course_assessment(pa=8, su=7, br=6, fc=9))

    assert hasattr(result, 'assessment')
    tree = result.assessment
    assert isinstance(tree, AssessmentTree)
    mg = tree.module_gate
    assert isinstance(mg, GateReport)
    assert isinstance(mg.overall_score, float)
    # Module Gate now has 5 rubrics (instructional_alignment moved to Course Gate — ADR-028)
    for dim in ["goal_focus", "text_readability", "pedagogical_clarity",
                "example_concreteness", "example_coherence"]:
        assert dim in mg.rubrics
    cg = tree.course_gate
    assert isinstance(cg, GateReport)
    # Course Gate now has 5 rubrics (instructional_alignment added — ADR-028)
    for dim in ["prerequisite_alignment", "structural_usability",
                "business_relevance", "fluidity_continuity", "instructional_alignment"]:
        assert dim in cg.rubrics


def test_assessment_tree_module_scores_match_flat_dict():
    """Tree module rubric scores must exactly match the flat module_gate dict."""
    aggregator = ScoreAggregator()
    metadata = CourseMetadata(title="Test", source="test.pdf")
    seg = _seg(1, "text " * 30, goal_focus=8, text_readability=7,
               pedagogical_clarity=9, example_concreteness=6,
               example_coherence=8)
    result = aggregator.aggregate(metadata, [seg], _course_assessment())
    flat = result.module_gate
    tree_mg = result.assessment.module_gate
    for dim in ["goal_focus", "text_readability", "pedagogical_clarity",
                "example_concreteness", "example_coherence"]:
        assert tree_mg.rubrics[dim].score == pytest.approx(flat[dim], abs=0.01)
    assert tree_mg.overall_score == pytest.approx(flat["overall_score"], abs=0.01)


def test_assessment_tree_course_scores_match_assessment():
    """Tree course rubric scores must match CourseAssessment scores."""
    aggregator = ScoreAggregator()
    metadata = CourseMetadata(title="Test", source="test.pdf")
    ca = _course_assessment(pa=9, su=5, br=7, fc=8, overall=7.25)
    result = aggregator.aggregate(metadata, [_seg(1, "text")], ca)
    cg = result.assessment.course_gate
    assert cg.rubrics["prerequisite_alignment"].score == pytest.approx(9.0, abs=0.01)
    assert cg.rubrics["structural_usability"].score == pytest.approx(5.0, abs=0.01)
    assert cg.rubrics["business_relevance"].score == pytest.approx(7.0, abs=0.01)
    assert cg.rubrics["fluidity_continuity"].score == pytest.approx(8.0, abs=0.01)
    assert cg.overall_score == pytest.approx(7.25, abs=0.01)
