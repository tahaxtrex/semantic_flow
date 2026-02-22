import logging
import datetime
from typing import List, Dict
from src.models import CourseMetadata, EvaluatedSegment, CourseEvaluation

logger = logging.getLogger(__name__)

class ScoreAggregator:
    """Aggregates segment-level scores into a final course-level mathematical average."""
    
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
        
        if not segments:
            logger.warning("No segments provided for aggregation. Returning early.")
            return CourseEvaluation(
                course_metadata=metadata.model_dump() if hasattr(metadata, 'model_dump') else metadata,
                overall_score=overall_score,
                segments=[],
                evaluation_meta={
                    "model_used": model_used,
                    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "prompt_version": "1.0",
                    "note": "No segments evaluated"
                }
            )
            
        # Sum all quantitative scores
        dimensions = overall_score.keys()
        for segment in segments:
            # Safely unpack the Pydantic model dict
            scores_dict = segment.scores.model_dump() if hasattr(segment.scores, 'model_dump') else segment.scores
            for dim in dimensions:
                # Provide fallback 0 if missing
                overall_score[dim] += scores_dict.get(dim, 0)
                
        # Calculate mathematical averages across all returned segments
        num_segments = len(segments)
        for dim in dimensions:
            average = overall_score[dim] / num_segments
            overall_score[dim] = round(average, 2)
            
        logger.info("Mathematical aggregation complete.")
        
        return CourseEvaluation(
            course_metadata=metadata.model_dump() if hasattr(metadata, 'model_dump') else metadata,
            overall_score=overall_score,
            segments=[s.model_dump() if hasattr(s, 'model_dump') else s for s in segments],
            evaluation_meta={
                "model_used": model_used,
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "prompt_version": "1.0"
            }
        )
