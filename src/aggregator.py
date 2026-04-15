import logging
import datetime
from typing import List, Dict, Optional
from src.models import (
    CourseMetadata, EvaluatedSegment, CourseAssessment, CourseEvaluation,
    AssessmentTree, GateReport, RubricResult,
    ModuleScores, CourseScores,
)

logger = logging.getLogger(__name__)


class ScoreAggregator:
    """Aggregates Module Gate segment-level scores into a weighted average.

    Only 'instructional' segments contribute to the module_gate overall score.
    The course_gate assessment is passed through directly (it is already a holistic score).

    Also builds the ADR-024 AssessmentTree for the structured output.
    """

    # Module/Course Gate dimensions derived from Pydantic models — single source of truth.
    # ADR-028: instructional_alignment lives in CourseScores (moved from Module Gate).
    _MODULE_DIMENSIONS = list(ModuleScores.model_fields.keys())
    _COURSE_DIMENSIONS = list(CourseScores.model_fields.keys())

    def aggregate(
        self,
        metadata: CourseMetadata,
        segments: List[EvaluatedSegment],
        course_assessment: CourseAssessment,
        model_used: str = "Claude 3.5 Sonnet",
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

        # Module Gate overall: simple mean of all 5 dimension scores (ADR-028)
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
            f"fluidity_continuity={course_assessment.scores.fluidity_continuity}, "
            f"instructional_alignment={course_assessment.scores.instructional_alignment}. "
            f"Course Gate Overall: {course_assessment.overall_score}"
        )

        # --- Build ADR-024 AssessmentTree ---
        assessment_tree = self._build_assessment_tree(
            module_overall=module_overall,
            module_gate_score=module_gate_score,
            instructional_segments=instructional_segments,
            course_assessment=course_assessment,
        )

        return CourseEvaluation(
            course_metadata=metadata.model_dump() if hasattr(metadata, 'model_dump') else metadata,
            assessment=assessment_tree,
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

    # ─────────────────────────────────────────────────────────────────────────
    # ADR-024: AssessmentTree builder
    # ─────────────────────────────────────────────────────────────────────────

    def _build_assessment_tree(
        self,
        module_overall: Dict[str, float],
        module_gate_score: float,
        instructional_segments: List[EvaluatedSegment],
        course_assessment: CourseAssessment,
    ) -> AssessmentTree:
        """Construct the tree-structured view of both gate results.

        Module Gate rubric rationales: For each dimension, pick the longest (most
        detailed) rationale from any scored instructional segment. Surfaces the most
        substantiated justification rather than an arbitrary one.

        Course Gate rubric rationales: taken directly from CourseAssessment.reasoning.
        """
        # --- Module Gate rubrics ---
        module_rubrics: Dict[str, RubricResult] = {}

        for dim in self._MODULE_DIMENSIONS:
            score = module_overall.get(dim, 0.0)
            rationale_key = f"{dim}_rationale"

            # Collect (dim_score, rationale) from all instructional segments
            candidates = []
            for seg in instructional_segments:
                reasoning = getattr(seg, 'reasoning', None)
                if reasoning is None:
                    continue
                r = getattr(reasoning, rationale_key, "") or ""
                seg_score = getattr(seg.scores, dim, 0) if hasattr(seg, 'scores') else 0
                if r:
                    candidates.append((seg_score, r))

            if not candidates:
                module_rubrics[dim] = RubricResult(score=score, rationale="")
                continue

            candidates.sort(key=lambda x: x[0])
            n = len(candidates)
            score_range = f"{candidates[0][0]}–{candidates[-1][0]}"

            lowest_r = candidates[0][1][:220]
            highest_r = candidates[-1][1][:220]
            median_r = candidates[n // 2][1][:150] if n > 2 else ""

            parts = [f"Across {n} segment(s) (scores {score_range}/10):"]
            parts.append(f"Weakest: {lowest_r}")
            if median_r:
                parts.append(f"Typical: {median_r}")
            parts.append(f"Strongest: {highest_r}")

            module_rubrics[dim] = RubricResult(score=score, rationale=" | ".join(parts))

        module_gate_report = GateReport(
            overall_score=module_gate_score,
            rubrics=module_rubrics,
        )

        # --- Course Gate rubrics ---
        course_scores_dict   = course_assessment.scores.model_dump()
        course_reasoning_dict = course_assessment.reasoning.model_dump()
        course_rubrics: Dict[str, RubricResult] = {}

        for dim in self._COURSE_DIMENSIONS:
            score     = float(course_scores_dict.get(dim, 0))
            rationale = course_reasoning_dict.get(f"{dim}_rationale", "") or ""
            course_rubrics[dim] = RubricResult(score=score, rationale=rationale)

        course_gate_report = GateReport(
            overall_score=course_assessment.overall_score,
            rubrics=course_rubrics,
        )

        return AssessmentTree(
            module_gate=module_gate_report,
            course_gate=course_gate_report,
        )
