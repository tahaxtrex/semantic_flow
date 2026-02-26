import logging
import datetime
from typing import List, Dict
from src.models import CourseMetadata, EvaluatedSegment, CourseEvaluation

logger = logging.getLogger(__name__)

class ScoreAggregator:
    """Aggregates segment-level scores into a final course-level mathematical average.

    Only 'instructional' segments contribute to overall_score.
    Exercise, solution, and reference_table segments are stored in the output
    but excluded from the aggregate (critic.md Issue 8).
    """

    def aggregate(self, metadata: CourseMetadata, segments: List[EvaluatedSegment], model_used: str = "Claude 4.6 Sonnet") -> CourseEvaluation:
        logger.info(f"Aggregating scores for {len(segments)} segments.")

        overall_score: Dict[str, float] = {
            "goal_focus": 0.0,
            "text_readability": 0.0,
            "pedagogical_clarity": 0.0,
            "prerequisite_alignment": 0.0,
            "fluidity_continuity": 0.0,
            "structural_usability": 0.0,
            "example_concreteness": 0.0,
            "example_coherence": 0.0,
            "business_relevance": 0.0,
            "instructional_alignment": 0.0
        }

        # Separate instructional from non-instructional segments
        instructional_segments = [s for s in segments if getattr(s, 'segment_type', 'instructional') == 'instructional']
        non_instructional = [s for s in segments if getattr(s, 'segment_type', 'instructional') != 'instructional']

        if non_instructional:
            types_found = {getattr(s, 'segment_type', '?') for s in non_instructional}
            logger.info(
                f"Excluding {len(non_instructional)} non-instructional segment(s) from aggregate "
                f"(types: {', '.join(sorted(types_found))})."
            )

        if not segments:
            logger.warning("No segments provided for aggregation. Returning early.")
            return CourseEvaluation(
                course_metadata=metadata.model_dump() if hasattr(metadata, 'model_dump') else metadata,
                overall_score=overall_score,
                segments=[],
                evaluation_meta={
                    "model_used": model_used,
                    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "prompt_version": "1.1",
                    "note": "No segments evaluated"
                }
            )

        scoring_pool = instructional_segments if instructional_segments else segments
        if not instructional_segments:
            logger.warning("No instructional segments found — falling back to scoring all segments.")

        dimensions = list(overall_score.keys())
        total_weight = sum(len(segment.text) for segment in scoring_pool)
        
        if total_weight == 0:
            logger.warning("Total weight of segments is zero. Cannot mathematically aggregate properly.")
            num_scored = len(scoring_pool)
            for segment in scoring_pool:
                scores_dict = segment.scores.model_dump() if hasattr(segment.scores, 'model_dump') else segment.scores
                for dim in dimensions:
                    overall_score[dim] += scores_dict.get(dim, 0)
            
            if num_scored > 0:
                for dim in dimensions:
                    overall_score[dim] = round(overall_score[dim] / num_scored, 2)
        else:
            for segment in scoring_pool:
                scores_dict = segment.scores.model_dump() if hasattr(segment.scores, 'model_dump') else segment.scores
                weight = len(segment.text) / total_weight
                for dim in dimensions:
                    overall_score[dim] += scores_dict.get(dim, 0) * weight
                    
            for dim in dimensions:
                overall_score[dim] = round(overall_score[dim], 2)

        num_scored = len(scoring_pool)
        logger.info(
            f"Mathematical aggregation complete. "
            f"Scored {num_scored}/{len(segments)} instructional segments."
        )

        return CourseEvaluation(
            course_metadata=metadata.model_dump() if hasattr(metadata, 'model_dump') else metadata,
            overall_score=overall_score,
            segments=[s.model_dump() if hasattr(s, 'model_dump') else s for s in segments],
            evaluation_meta={
                "model_used": model_used,
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "prompt_version": "1.1",
                "total_segments": len(segments),
                "instructional_segments_scored": num_scored,
                "excluded_segments": len(segments) - num_scored
            }
        )
