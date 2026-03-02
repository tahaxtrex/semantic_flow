import json
import logging
import os
import sys
import time
import yaml
from pathlib import Path
from pydantic import ValidationError

from typing import List, Tuple, Optional

from anthropic import Anthropic
from google import genai
from google.genai import types

from src.models import (
    Segment, CourseMetadata,
    EvaluatedSegment, ModuleScores, ModuleReasoning,
    CourseAssessment, CourseScores, CourseReasoning,
)

logger = logging.getLogger(__name__)

# Per-batch retry budget for the selected model.
MAX_RETRIES_PER_BATCH = 3
RETRY_BACKOFF_SECONDS = 5  # doubles each attempt: 5s → 10s → 20s


class LLMEvaluator:
    """Two-Gate pedagogical evaluator using Claude or Gemini.

    Gate 1 — Module Gate:
        Evaluates each instructional segment in batches on 6 rubrics (readability,
        clarity, examples, goal focus, instructional alignment). Each segment also
        produces a 1-2 sentence content summary for use in the Course Gate.

    Gate 2 — Course Gate:
        Executes a single capstone call once ALL Module Gate evaluations are done.
        Receives the course metadata, the non-instructional segment text (TOC, Preface),
        and the ordered list of per-segment summaries. Scores 4 holistic rubrics
        (prerequisite alignment, structural usability, business relevance, fluidity).
    """

    def __init__(self, config_path: Path, preferred_model: str = "claude"):
        self.config_path = Path(config_path)
        self.preferred_model = preferred_model.lower()
        self.module_rubrics_yaml, self.course_rubrics_yaml = self._load_rubrics()

        self.anthropic_key = os.getenv("ANTHROPIC_API_KEY")
        self.gemini_key = os.getenv("GEMINI_API_KEY")

        if self.preferred_model == "claude":
            if not self.anthropic_key:
                logger.critical("ANTHROPIC_API_KEY missing but Claude was requested.")
                raise ValueError("ANTHROPIC_API_KEY is required for Claude model")
            self.anthropic_client = Anthropic(api_key=self.anthropic_key)
            self.gemini_client = None
        elif self.preferred_model == "gemini":
            if not self.gemini_key:
                logger.critical("GEMINI_API_KEY missing but Gemini was requested.")
                raise ValueError("GEMINI_API_KEY is required for Gemini model")
            self.gemini_client = genai.Client(api_key=self.gemini_key)
            self.gemini_model = 'gemini-2.5-flash'
            self.anthropic_client = None
        else:
            raise ValueError(f"Unsupported model requested: {self.preferred_model}")

    def _load_rubrics(self) -> Tuple[str, str]:
        """Load and return (module_rubrics_yaml, course_rubrics_yaml) as YAML strings."""
        with open(self.config_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
        module_yaml = yaml.dump({"module_rubrics": data["module_rubrics"]}, sort_keys=False)
        course_yaml = yaml.dump({"course_rubrics": data["course_rubrics"]}, sort_keys=False)
        return module_yaml, course_yaml

    # -------------------------------------------------------------------------
    # SHARED UTILITIES
    # -------------------------------------------------------------------------

    def _retry_call(self, call_fn, model_name: str, batch_size: int):
        last_exc = None
        for attempt in range(1, MAX_RETRIES_PER_BATCH + 1):
            try:
                result = call_fn()
                if attempt > 1:
                    logger.info(
                        f"{model_name} succeeded on attempt {attempt}/{MAX_RETRIES_PER_BATCH} "
                        f"for batch of {batch_size} segments."
                    )
                return result
            except Exception as e:
                last_exc = e
                wait = RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1))  # 5, 10, 20
                if attempt < MAX_RETRIES_PER_BATCH:
                    err_str = str(e).lower()
                    retryable = (
                        isinstance(e, (ValidationError, json.JSONDecodeError))
                        or any(indicator in err_str for indicator in ['429', '503', 'rate limit', 'quota', 'service unavailable', 'overloaded'])
                    )
                    if not retryable:
                        logger.error(f"{model_name} failed with non-retryable error for batch: {e}")
                        raise e

                    logger.warning(
                        f"{model_name} attempt {attempt}/{MAX_RETRIES_PER_BATCH} failed handling batch. "
                        f"Retrying in {wait}s... Error: {e}"
                    )
                    time.sleep(wait)
                else:
                    logger.error(
                        f"{model_name} exhausted all {MAX_RETRIES_PER_BATCH} retries for batch."
                    )
        raise last_exc

    def _parse_json_result(self, text: str) -> list:
        text = text.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        return json.loads(text.strip())

    def _unwrap_gemini_list(self, raw) -> list:
        """Normalize Gemini response to a flat list of evaluation dicts.

        Gemini may return any of:
        - [{...}, {...}]            → bare list (ideal case)
        - {"evaluations": [{...}]} → wrapped under a known key
        - {"submit_module_evaluations": {"evaluations": [...]}}  → double-wrapped
        - a single dict {"segment_id": 1, ...} → wrap in list
        """
        if isinstance(raw, list):
            return raw
        if isinstance(raw, dict):
            # Try to find the list under any key
            for key in ("evaluations", "submit_module_evaluations", "items"):
                if key in raw:
                    val = raw[key]
                    if isinstance(val, list):
                        return val
                    if isinstance(val, dict) and "evaluations" in val:
                        return val["evaluations"]
            # If the dict itself looks like a single evaluation (has segment_id), wrap it
            if "segment_id" in raw:
                return [raw]
            # Last resort: return values() if they are dicts
            vals = [v for v in raw.values() if isinstance(v, (list, dict))]
            if vals and isinstance(vals[0], list):
                return vals[0]
        raise ValueError(f"Cannot unwrap Gemini list response. Got type={type(raw).__name__}: {str(raw)[:200]}")

    def _unwrap_gemini_object(self, raw) -> dict:
        """Normalize Gemini response to a single evaluation dict with 'scores' and 'reasoning'.

        Gemini may return:
        - {"scores": {...}, "reasoning": {...}}   → ideal
        - [{"scores": {...}, ...}]               → wrapped in a list
        - {"submit_course_evaluation": {"scores": {...}}} → double-wrapped
        """
        if isinstance(raw, list):
            raw = raw[0]
        if isinstance(raw, dict):
            if "scores" in raw:
                return raw
            # Try to unwrap one level of nesting
            for key in ("submit_course_evaluation", "evaluation", "result"):
                if key in raw and isinstance(raw[key], dict):
                    inner = raw[key]
                    if "scores" in inner:
                        return inner
            # If only one dict value, drill in
            dict_vals = {k: v for k, v in raw.items() if isinstance(v, dict)}
            if len(dict_vals) == 1:
                return next(iter(dict_vals.values()))
        raise ValueError(f"Cannot unwrap Gemini object response. Got type={type(raw).__name__}: {str(raw)[:200]}")

    # -------------------------------------------------------------------------
    # MODULE GATE — Field Definitions
    # -------------------------------------------------------------------------

    _MODULE_SCORE_FIELDS = [
        "goal_focus", "text_readability", "pedagogical_clarity",
        "example_concreteness", "example_coherence", "instructional_alignment",
    ]
    _MODULE_RATIONALE_FIELDS = [f"{f}_rationale" for f in _MODULE_SCORE_FIELDS]

    _MODULE_EVAL_TOOL = {
        "name": "submit_module_evaluations",
        "description": "Submit the Module Gate evaluations for all provided instructional segments.",
        "input_schema": {
            "type": "object",
            "properties": {
                "evaluations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "segment_id": {"type": "integer"},
                            "summary": {
                                "type": "string",
                                "description": "1-2 sentence content summary of this segment's topic and key concepts."
                            },
                            "scores": {
                                "type": "object",
                                "properties": {f: {"type": "integer", "minimum": 1, "maximum": 10} for f in _MODULE_SCORE_FIELDS},
                                "required": _MODULE_SCORE_FIELDS,
                            },
                            "reasoning": {
                                "type": "object",
                                "properties": {f: {"type": "string"} for f in _MODULE_RATIONALE_FIELDS},
                                "required": _MODULE_RATIONALE_FIELDS,
                            },
                        },
                        "required": ["segment_id", "summary", "scores", "reasoning"],
                    },
                }
            },
            "required": ["evaluations"],
        },
    }

    # -------------------------------------------------------------------------
    # MODULE GATE — Prompt Builder
    # -------------------------------------------------------------------------

    def _build_module_batch_prompts(self, metadata: CourseMetadata, segments: List[Segment]) -> Tuple[str, str]:
        system_prompt = f"""
You are an expert pedagogical evaluator. Evaluate the provided course segments based strictly on the following MODULE rubrics.

COURSE CONTEXT (for reference only — do not score course-level structure here):
Title: {metadata.title}
Target Audience: {metadata.target_audience}
Learning Outcomes: {', '.join(metadata.learning_outcomes) if metadata.learning_outcomes else 'None specified'}

MODULE RUBRICS (score each segment on ONLY these 6 dimensions):
{self.module_rubrics_yaml}

EXTRACTION NOTES (pipeline artifacts — do not penalise the course for these):
- Figures referenced in the text (e.g. "Fig. 1.1") are not available as images. Evaluate figure references positively as indicators of visual support.
- [FIGURE X.Y: caption] markers show a figure caption extracted from the PDF — treat as evidence of visual content.
- Text was extracted from PDF; minor formatting artifacts (e.g. [?] placeholders) are NOT a property of the course.
- [TABLE: ...] markers indicate a table was detected but could not be rendered as prose — treat as a structured reference element.
- [CODE] / [/CODE] blocks contain verbatim code examples extracted from a monospace font region.

For each segment, you must also provide a 1-2 sentence 'summary' of the segment's topic and key concepts.
This summary will be used as context in a subsequent holistic course-level evaluation.
"""
        user_prompt = "Score the following segments:\n\n"
        for s in segments:
            user_prompt += f"--- SEGMENT ID: {s.segment_id} ---\n"
            user_prompt += f"Heading: {s.heading or 'None'}\n"
            user_prompt += f"Text:\n{s.text}\n\n"

        return system_prompt.strip(), user_prompt.strip()

    # -------------------------------------------------------------------------
    # MODULE GATE — Execution
    # -------------------------------------------------------------------------

    def _make_incomplete_segment(self, segment: Segment) -> EvaluatedSegment:
        return EvaluatedSegment(
            segment_id=segment.segment_id,
            heading=segment.heading,
            text=segment.text,
            segment_type=segment.segment_type,
            scores=ModuleScores(
                goal_focus=0, text_readability=0, pedagogical_clarity=0,
                example_concreteness=0, example_coherence=0, instructional_alignment=0,
            ),
            reasoning=ModuleReasoning(),
            summary="",
            incomplete=True,
        )

    def _match_module_evaluations(self, data: list, segments: List[Segment]) -> List[EvaluatedSegment]:
        evals = []
        data_by_id = {item["segment_id"]: item for item in data if "segment_id" in item}

        for segment in segments:
            if segment.segment_id not in data_by_id:
                logger.warning(f"Missing evaluation for segment_id {segment.segment_id}; marking as incomplete.")
                evals.append(self._make_incomplete_segment(segment))
                continue

            item = data_by_id[segment.segment_id]
            try:
                scores = ModuleScores(**item.get("scores", {}))
                reasoning = ModuleReasoning(**item.get("reasoning", {}))
                summary = item.get("summary", "")
                incomplete = False
            except (ValidationError, Exception) as e:
                logger.warning(f"Partial/invalid module evaluation for segment {segment.segment_id}: {e}. Marking as incomplete.")
                scores = ModuleScores(
                    goal_focus=0, text_readability=0, pedagogical_clarity=0,
                    example_concreteness=0, example_coherence=0, instructional_alignment=0,
                )
                reasoning = ModuleReasoning()
                summary = ""
                incomplete = True

            evals.append(EvaluatedSegment(
                segment_id=segment.segment_id,
                heading=segment.heading,
                text=segment.text,
                segment_type=segment.segment_type,
                scores=scores,
                reasoning=reasoning,
                summary=summary,
                incomplete=incomplete,
            ))
        return evals

    def _call_claude_module_batch(self, system_prompt: str, user_prompt: str, segments: List[Segment]) -> List[EvaluatedSegment]:
        logger.info(f"[Module Gate] Evaluating batch of {len(segments)} segments via Claude")
        response = self.anthropic_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=8192,
            temperature=0.2,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            tools=[self._MODULE_EVAL_TOOL],
            tool_choice={"type": "tool", "name": "submit_module_evaluations"},
        )
        tool_block = next(b for b in response.content if b.type == "tool_use")
        data = tool_block.input["evaluations"]
        return self._match_module_evaluations(data, segments)

    def _call_gemini_module_batch(self, system_prompt: str, user_prompt: str, segments: List[Segment]) -> List[EvaluatedSegment]:
        logger.info(f"[Module Gate] Evaluating batch of {len(segments)} segments via Gemini")
        prompt = system_prompt + "\n\n" + user_prompt
        response = self.gemini_client.models.generate_content(
            model=self.gemini_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.2,
                response_mime_type="application/json"
            )
        )
        try:
            raw = self._parse_json_result(response.text)
            data = self._unwrap_gemini_list(raw)
        except Exception as e:
            logger.error(f"[Module Gate] Gemini JSON parse/unwrap failed: {e}. Raw response: {response.text[:500]}")
            raise
        return self._match_module_evaluations(data, segments)

    def evaluate_batch(self, metadata: CourseMetadata, segments: List[Segment]) -> List[EvaluatedSegment]:
        """MODULE GATE: Evaluate a batch of segments on the 6 Module rubrics.
        Non-instructional segments are passed through with 0 scores and no summary.
        Instructional segments get 6 scores + a content summary for the Course Gate.
        """
        results = []
        instructional_segments = []

        for segment in segments:
            if segment.segment_type != "instructional":
                logger.info(f"Bypassing Module Gate for non-instructional segment: {segment.segment_id} ({segment.segment_type})")
                results.append(EvaluatedSegment(
                    segment_id=segment.segment_id,
                    heading=segment.heading,
                    text=segment.text,
                    segment_type=segment.segment_type,
                    scores=ModuleScores(
                        goal_focus=0, text_readability=0, pedagogical_clarity=0,
                        example_concreteness=0, example_coherence=0, instructional_alignment=0,
                    ),
                    reasoning=ModuleReasoning(),
                    summary="",
                ))
            else:
                instructional_segments.append(segment)

        if not instructional_segments:
            return sorted(results, key=lambda x: x.segment_id)

        system_prompt, user_prompt = self._build_module_batch_prompts(metadata, instructional_segments)

        try:
            if self.anthropic_client:
                evals = self._retry_call(
                    lambda: self._call_claude_module_batch(system_prompt, user_prompt, instructional_segments),
                    "Claude",
                    len(instructional_segments)
                )
            elif self.gemini_client:
                evals = self._retry_call(
                    lambda: self._call_gemini_module_batch(system_prompt, user_prompt, instructional_segments),
                    "Gemini",
                    len(instructional_segments)
                )
            else:
                logger.critical("Fatal: Both clients are missing API keys.")
                raise ValueError("No API client configured.")
        except Exception as e:
            logger.error(
                f"Batch of {len(instructional_segments)} segments failed after all retries; "
                f"marking as incomplete. Error: {e}"
            )
            evals = [self._make_incomplete_segment(s) for s in instructional_segments]

        results.extend(evals)
        return sorted(results, key=lambda x: x.segment_id)

    # -------------------------------------------------------------------------
    # COURSE GATE — Field Definitions
    # -------------------------------------------------------------------------

    _COURSE_SCORE_FIELDS = [
        "prerequisite_alignment", "structural_usability",
        "business_relevance", "fluidity_continuity",
    ]
    _COURSE_RATIONALE_FIELDS = [f"{f}_rationale" for f in _COURSE_SCORE_FIELDS]

    _COURSE_EVAL_TOOL = {
        "name": "submit_course_evaluation",
        "description": "Submit the holistic Course Gate evaluation for the entire course.",
        "input_schema": {
            "type": "object",
            "properties": {
                "scores": {
                    "type": "object",
                    "properties": {f: {"type": "integer", "minimum": 1, "maximum": 10} for f in _COURSE_SCORE_FIELDS},
                    "required": _COURSE_SCORE_FIELDS,
                },
                "reasoning": {
                    "type": "object",
                    "properties": {f: {"type": "string"} for f in _COURSE_RATIONALE_FIELDS},
                    "required": _COURSE_RATIONALE_FIELDS,
                },
            },
            "required": ["scores", "reasoning"],
        },
    }

    # -------------------------------------------------------------------------
    # COURSE GATE — Prompt Builder
    # -------------------------------------------------------------------------

    def _build_course_prompts(
        self,
        metadata: CourseMetadata,
        evaluated_segments: List[EvaluatedSegment],
        non_instructional_segments: List[Segment],
    ) -> Tuple[str, str]:
        system_prompt = f"""
You are an expert pedagogical evaluator. You are performing a COURSE-LEVEL assessment.
Your job is to evaluate the overall structure, coherence and relevance of the course as a whole — NOT individual segments.

COURSE METADATA:
Title: {metadata.title}
Author: {metadata.author or 'Unknown'}
Target Audience: {metadata.target_audience}
Level: {metadata.level or 'Not specified'}
Prerequisites: {', '.join(metadata.prerequisites) if metadata.prerequisites else 'None specified'}
Learning Outcomes: {', '.join(metadata.learning_outcomes) if metadata.learning_outcomes else 'None specified'}
Description: {metadata.description or 'Not provided'}

COURSE RUBRICS (score the ENTIRE COURSE on ONLY these 4 dimensions):
{self.course_rubrics_yaml}
"""

        user_prompt = ""

        # Include non-instructional sections (TOC, Preface) as course structure evidence
        if non_instructional_segments:
            user_prompt += "## Course Structure Sections (TOC, Preface, etc.)\n"
            for seg in non_instructional_segments:
                user_prompt += f"\n### {seg.heading or seg.segment_type.upper()} (ID {seg.segment_id})\n"
                # Truncate long non-instructional segments to save tokens
                text = seg.text
                if len(text) > 1500:
                    text = text[:1500] + "\n[... truncated for brevity ...]"
                user_prompt += text + "\n"

        # Include condensed module summaries as the sequential course narrative
        instructional_with_summary = [
            s for s in evaluated_segments
            if s.segment_type == "instructional" and s.summary
        ]
        if instructional_with_summary:
            user_prompt += "\n## Sequential Module Summaries (in order)\n"
            user_prompt += "The following summaries represent each module/chapter of the course in order:\n\n"
            for seg in sorted(instructional_with_summary, key=lambda x: x.segment_id):
                heading_label = f"**{seg.heading}**" if seg.heading else f"Module {seg.segment_id}"
                user_prompt += f"- {heading_label}: {seg.summary}\n"

        user_prompt += "\n\nNow evaluate the course holistically on the 4 Course Gate rubrics."
        return system_prompt.strip(), user_prompt.strip()

    # -------------------------------------------------------------------------
    # COURSE GATE — Execution
    # -------------------------------------------------------------------------

    def _make_incomplete_course_assessment(self) -> CourseAssessment:
        return CourseAssessment(
            scores=CourseScores(
                prerequisite_alignment=0, structural_usability=0,
                business_relevance=0, fluidity_continuity=0,
            ),
            reasoning=CourseReasoning(),
            overall_score=0.0,
        )

    def _call_claude_course(self, system_prompt: str, user_prompt: str) -> CourseAssessment:
        logger.info("[Course Gate] Running capstone course evaluation via Claude")
        response = self.anthropic_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            temperature=0.2,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            tools=[self._COURSE_EVAL_TOOL],
            tool_choice={"type": "tool", "name": "submit_course_evaluation"},
        )
        tool_block = next(b for b in response.content if b.type == "tool_use")
        data = tool_block.input
        scores = CourseScores(**data["scores"])
        reasoning = CourseReasoning(**data["reasoning"])
        score_values = [v for v in data["scores"].values() if isinstance(v, (int, float))]
        overall = round(sum(score_values) / len(score_values), 2) if score_values else 0.0
        return CourseAssessment(scores=scores, reasoning=reasoning, overall_score=overall)

    def _call_gemini_course(self, system_prompt: str, user_prompt: str) -> CourseAssessment:
        logger.info("[Course Gate] Running capstone course evaluation via Gemini")
        prompt = system_prompt + "\n\n" + user_prompt
        response = self.gemini_client.models.generate_content(
            model=self.gemini_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.2,
                response_mime_type="application/json"
            )
        )
        try:
            raw = self._parse_json_result(response.text)
            data = self._unwrap_gemini_object(raw)
        except Exception as e:
            logger.error(f"[Course Gate] Gemini JSON parse/unwrap failed: {e}. Raw response: {response.text[:500]}")
            raise
        scores = CourseScores(**data["scores"])
        reasoning = CourseReasoning(**data.get("reasoning", {}))
        score_values = [v for v in data["scores"].values() if isinstance(v, (int, float))]
        overall = round(sum(score_values) / len(score_values), 2) if score_values else 0.0
        return CourseAssessment(scores=scores, reasoning=reasoning, overall_score=overall)

    def evaluate_course(
        self,
        metadata: CourseMetadata,
        evaluated_segments: List[EvaluatedSegment],
        non_instructional_segments: Optional[List[Segment]] = None,
    ) -> CourseAssessment:
        """COURSE GATE: Single capstone call evaluating the full course on 4 holistic rubrics.

        Args:
            metadata: Extracted course metadata.
            evaluated_segments: All EvaluatedSegment objects from the Module Gate (includes summaries).
            non_instructional_segments: Raw Segment objects for TOC/Preface to give structural context.

        Returns:
            CourseAssessment with 4 scores, rationales, and an overall_score.
        """
        non_instructional_segments = non_instructional_segments or []
        system_prompt, user_prompt = self._build_course_prompts(
            metadata, evaluated_segments, non_instructional_segments
        )

        try:
            if self.anthropic_client:
                return self._retry_call(
                    lambda: self._call_claude_course(system_prompt, user_prompt),
                    "Claude",
                    1  # Course Gate is always 1 call
                )
            elif self.gemini_client:
                return self._retry_call(
                    lambda: self._call_gemini_course(system_prompt, user_prompt),
                    "Gemini",
                    1
                )
            else:
                raise ValueError("No API client configured.")
        except Exception as e:
            logger.error(f"[Course Gate] Course evaluation failed after all retries: {e}")
            return self._make_incomplete_course_assessment()
