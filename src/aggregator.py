import logging
import datetime
from typing import List, Dict
from src.models import CourseMetadata, EvaluatedSegment, CourseAssessment, CourseEvaluation

logger = logging.getLogger(__name__)


class ScoreAggregator:
    """Aggregates Module Gate segment-level scores into a weighted average.

    Only 'instructional' segments contribute to the module_gate overall score.
    The course_gate assessment is passed through directly (it is already a holistic score).
    """

    # Module Gate dimensions — must match ModuleScores fields in models.py
    _MODULE_DIMENSIONS = [
        "goal_focus", "text_readability", "pedagogical_clarity",
        "example_concreteness", "example_coherence", "instructional_alignment",
    ]

    def aggregate(
        self,
        metadata: CourseMetadata,
        segments: List[EvaluatedSegment],
        course_assessment: CourseAssessment,
        model_used: str = "Claude claude-sonnet-4-6",
    ) -> CourseEvaluation:
        logger.info(f"Aggregating Module Gate scores for {len(segments)} segments.")

        # --- Module Gate: Character-length weighted average over instructional segments --
        instructional_segments = [
            s for s in segments
            if getattr(s, 'segment_type', 'instructional') == 'instructional'
            and not getattr(s, 'incomplete', False)
        ]
        non_instructional = [
            s for s in segments
            if getattr(s, 'segment_type', 'instructional') != 'instructional'
        ]

        if non_instructional:
            types_found = {getattr(s, 'segment_type', '?') for s in non_instructional}
            logger.info(
                f"Excluding {len(non_instructional)} non-instructional segment(s) from Module Gate aggregate "
                f"(types: {', '.join(sorted(types_found))})."
            )

        module_overall: Dict[str, float] = {dim: 0.0 for dim in self._MODULE_DIMENSIONS}

        if not segments:
            logger.warning("No segments provided for aggregation.")
        elif not instructional_segments:
            logger.warning("No complete instructional segments found — Module Gate scores will be 0.")
        else:
            scoring_pool = instructional_segments
            total_weight = sum(len(s.text) for s in scoring_pool)

            if total_weight == 0:
                logger.warning("Total text weight is zero — falling back to simple average.")
                n = len(scoring_pool)
                for seg in scoring_pool:
                    scores_dict = seg.scores.model_dump() if hasattr(seg.scores, 'model_dump') else {}
                    for dim in self._MODULE_DIMENSIONS:
                        module_overall[dim] += scores_dict.get(dim, 0)
                for dim in self._MODULE_DIMENSIONS:
                    module_overall[dim] = round(module_overall[dim] / n, 2)
            else:
                for seg in scoring_pool:
                    scores_dict = seg.scores.model_dump() if hasattr(seg.scores, 'model_dump') else {}
                    weight = len(seg.text) / total_weight
                    for dim in self._MODULE_DIMENSIONS:
                        module_overall[dim] += scores_dict.get(dim, 0) * weight
                for dim in self._MODULE_DIMENSIONS:
                    module_overall[dim] = round(module_overall[dim], 2)

        # Module Gate overall: simple mean of all 6 dimension scores
        dim_scores = [v for v in module_overall.values()]
        module_gate_score = round(sum(dim_scores) / len(dim_scores), 2) if dim_scores else 0.0
        module_overall["overall_score"] = module_gate_score

        logger.info(
            f"Module Gate aggregation complete. "
            f"Scored {len(instructional_segments)}/{len(segments)} instructional segments. "
            f"Module Gate Overall: {module_gate_score}"
        )
        logger.info(
            f"Course Gate scores: prerequisite_alignment={course_assessment.scores.prerequisite_alignment}, "
            f"structural_usability={course_assessment.scores.structural_usability}, "
            f"business_relevance={course_assessment.scores.business_relevance}, "
            f"fluidity_continuity={course_assessment.scores.fluidity_continuity}. "
            f"Course Gate Overall: {course_assessment.overall_score}"
        )

        return CourseEvaluation(
            course_metadata=metadata.model_dump() if hasattr(metadata, 'model_dump') else metadata,
            module_gate=module_overall,
            course_gate=course_assessment,
            segments=[s.model_dump() if hasattr(s, 'model_dump') else s for s in segments],
            evaluation_meta={
                "model_used": model_used,
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "prompt_version": "2.0",
                "total_segments": len(segments),
                "instructional_segments_scored": len(instructional_segments),
                "excluded_segments": len(segments) - len(instructional_segments),
            }
        )
