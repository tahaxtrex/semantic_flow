import json
import logging
import os
import sys
import time
import yaml
from pathlib import Path
from pydantic import ValidationError

from typing import List, Tuple

from anthropic import Anthropic
from google import genai
from google.genai import types

from src.models import Segment, CourseMetadata, EvaluatedSegment, SectionScores, SectionReasoning

logger = logging.getLogger(__name__)

# Per-batch retry budget for the selected model.
MAX_RETRIES_PER_BATCH = 3
RETRY_BACKOFF_SECONDS = 5  # doubles each attempt: 5s → 10s → 20s


class LLMEvaluator:
    """Evaluates pedagogical segments using Claude or Gemini in batched mode.

    Features:
    - Batching: Evaluates multiple segments per API call to save on system prompt tokens.
    - System Prompt Isolation: Rubrics are isolated to the system prompt.
    - No Cascading: To preserve scientific validity, a run uses exactly one model 
      and fails loudly if it exhausts retries, rather than silently blending results.
    """

    def __init__(self, config_path: Path, preferred_model: str = "claude"):
        self.config_path = Path(config_path)
        self.rubrics = self._load_rubrics()
        self.preferred_model = preferred_model.lower()

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

    def _load_rubrics(self) -> str:
        with open(self.config_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
        return yaml.dump(data, sort_keys=False)

    def _build_batch_prompts(self, metadata: CourseMetadata, segments: List[Segment]) -> Tuple[str, str]:
        system_prompt = f"""
You are an expert pedagogical evaluator. Evaluate the provided course segments based strictly on the following rubrics.

COURSE METADATA:
Title: {metadata.title}
Target Audience: {metadata.target_audience}
Prerequisites: {', '.join(metadata.prerequisites) if metadata.prerequisites else 'None specified'}
Learning Outcomes: {', '.join(metadata.learning_outcomes) if metadata.learning_outcomes else 'None specified'}

RUBRICS:
{self.rubrics}

EXTRACTION NOTES (pipeline artifacts — do not penalise the course for these):
- Figures referenced in the text (e.g. "Fig. 1.1") are not available as images. Evaluate figure references positively as indicators of visual support.
- [FIGURE X.Y: caption] markers show a figure caption extracted from the PDF — treat as evidence of visual content.
- Text was extracted from PDF; minor formatting artifacts (e.g. [?] placeholders) are NOT a property of the course.
- [TABLE: ...] markers indicate a table was detected but could not be rendered as prose — treat as a structured reference element, not missing content.
- [CODE] / [/CODE] blocks contain verbatim code examples extracted from a monospace font region.

Your response must be ONLY a valid JSON array of evaluation objects, one for each segment provided. Do not include markdown blocks or any other text. The JSON array must adhere exactly to the following structure:
[
    {{
        "segment_id": <int>,
        "scores": {{
            "goal_focus": <int 1-10>,
            "text_readability": <int 1-10>,
            "pedagogical_clarity": <int 1-10>,
            "prerequisite_alignment": <int 1-10>,
            "fluidity_continuity": <int 1-10>,
            "structural_usability": <int 1-10>,
            "example_concreteness": <int 1-10>,
            "example_coherence": <int 1-10>,
            "business_relevance": <int 1-10>,
            "instructional_alignment": <int 1-10>
        }},
        "reasoning": {{
            "goal_focus_rationale": "...",
            "text_readability_rationale": "...",
            "pedagogical_clarity_rationale": "...",
            "prerequisite_alignment_rationale": "...",
            "fluidity_continuity_rationale": "...",
            "structural_usability_rationale": "...",
            "example_concreteness_rationale": "...",
            "example_coherence_rationale": "...",
            "business_relevance_rationale": "...",
            "instructional_alignment_rationale": "..."
        }}
    }}
]
"""
        user_prompt = "Score the following segments:\n\n"
        for s in segments:
            user_prompt += f"--- SEGMENT ID: {s.segment_id} ---\n"
            user_prompt += f"Heading: {s.heading or 'None'}\n"
            user_prompt += f"Text:\n{s.text}\n\n"

        return system_prompt.strip(), user_prompt.strip()

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

    def evaluate_batch(self, metadata: CourseMetadata, segments: List[Segment]) -> List[EvaluatedSegment]:
        results = []
        instructional_segments = []

        for segment in segments:
            if segment.segment_type != "instructional":
                logger.info(f"Bypassing LLM evaluation for non-instructional segment: {segment.segment_id} ({segment.segment_type})")
                results.append(EvaluatedSegment(
                    segment_id=segment.segment_id,
                    heading=segment.heading,
                    text=segment.text,
                    segment_type=segment.segment_type,
                    scores=SectionScores(
                        goal_focus=0, text_readability=0, pedagogical_clarity=0, prerequisite_alignment=0,
                        fluidity_continuity=0, structural_usability=0, example_concreteness=0,
                        example_coherence=0, business_relevance=0, instructional_alignment=0
                    ),
                    reasoning=SectionReasoning(
                        goal_focus_rationale="N/A", text_readability_rationale="N/A", pedagogical_clarity_rationale="N/A",
                        prerequisite_alignment_rationale="N/A", fluidity_continuity_rationale="N/A", structural_usability_rationale="N/A",
                        example_concreteness_rationale="N/A", example_coherence_rationale="N/A", business_relevance_rationale="N/A",
                        instructional_alignment_rationale="N/A"
                    )
                ))
            else:
                instructional_segments.append(segment)

        if not instructional_segments:
            return sorted(results, key=lambda x: x.segment_id)

        system_prompt, user_prompt = self._build_batch_prompts(metadata, instructional_segments)

        # Evaluate using strict model selection (no cascading between Claude and Gemini mid-run)
        try:
            if self.anthropic_client:
                evals = self._retry_call(
                    lambda: self._call_claude_batch(system_prompt, user_prompt, instructional_segments),
                    "Claude",
                    len(instructional_segments)
                )
            elif self.gemini_client:
                evals = self._retry_call(
                    lambda: self._call_gemini_batch(system_prompt, user_prompt, instructional_segments),
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

    def _parse_json_result(self, text: str) -> list:
        text = text.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        return json.loads(text.strip())

    _SCORE_FIELDS = [
        "goal_focus", "text_readability", "pedagogical_clarity", "prerequisite_alignment",
        "fluidity_continuity", "structural_usability", "example_concreteness",
        "example_coherence", "business_relevance", "instructional_alignment",
    ]
    _RATIONALE_FIELDS = [f"{f}_rationale" for f in _SCORE_FIELDS]

    _EVAL_TOOL = {
        "name": "submit_evaluations",
        "description": "Submit the pedagogical evaluations for all provided segments.",
        "input_schema": {
            "type": "object",
            "properties": {
                "evaluations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "segment_id": {"type": "integer"},
                            "scores": {
                                "type": "object",
                                "properties": {f: {"type": "integer", "minimum": 1, "maximum": 10} for f in _SCORE_FIELDS},
                                "required": _SCORE_FIELDS,
                            },
                            "reasoning": {
                                "type": "object",
                                "properties": {f: {"type": "string"} for f in _RATIONALE_FIELDS},
                                "required": _RATIONALE_FIELDS,
                            },
                        },
                        "required": ["segment_id", "scores", "reasoning"],
                    },
                }
            },
            "required": ["evaluations"],
        },
    }

    def _make_incomplete_segment(self, segment: Segment) -> EvaluatedSegment:
        return EvaluatedSegment(
            segment_id=segment.segment_id,
            heading=segment.heading,
            text=segment.text,
            segment_type=segment.segment_type,
            scores=SectionScores(
                goal_focus=0, text_readability=0, pedagogical_clarity=0,
                prerequisite_alignment=0, fluidity_continuity=0, structural_usability=0,
                example_concreteness=0, example_coherence=0, business_relevance=0,
                instructional_alignment=0,
            ),
            reasoning=SectionReasoning(),
            incomplete=True,
        )

    def _match_evaluations(self, data: list, segments: List[Segment]) -> List[EvaluatedSegment]:
        evals = []
        data_by_id = {item["segment_id"]: item for item in data if "segment_id" in item}

        for segment in segments:
            if segment.segment_id not in data_by_id:
                logger.warning(f"Missing evaluation for segment_id {segment.segment_id}; marking as incomplete.")
                evals.append(self._make_incomplete_segment(segment))
                continue

            item = data_by_id[segment.segment_id]
            try:
                scores = SectionScores(**item.get("scores", {}))
                reasoning = SectionReasoning(**item.get("reasoning", {}))
                incomplete = False
            except (ValidationError, Exception) as e:
                logger.warning(f"Partial/invalid evaluation for segment {segment.segment_id}: {e}. Marking as incomplete.")
                scores = SectionScores(
                    goal_focus=0, text_readability=0, pedagogical_clarity=0,
                    prerequisite_alignment=0, fluidity_continuity=0, structural_usability=0,
                    example_concreteness=0, example_coherence=0, business_relevance=0,
                    instructional_alignment=0,
                )
                reasoning = SectionReasoning()
                incomplete = True

            evals.append(EvaluatedSegment(
                segment_id=segment.segment_id,
                heading=segment.heading,
                text=segment.text,
                segment_type=segment.segment_type,
                scores=scores,
                reasoning=reasoning,
                incomplete=incomplete,
            ))
        return evals

    def _call_claude_batch(self, system_prompt: str, user_prompt: str, segments: List[Segment]) -> List[EvaluatedSegment]:
        logger.info(f"Evaluating batch of {len(segments)} segments via Claude")
        response = self.anthropic_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=8192,
            temperature=0.2,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            tools=[self._EVAL_TOOL],
            tool_choice={"type": "tool", "name": "submit_evaluations"},
        )

        tool_block = next(b for b in response.content if b.type == "tool_use")
        data = tool_block.input["evaluations"]
        return self._match_evaluations(data, segments)

    def _call_gemini_batch(self, system_prompt: str, user_prompt: str, segments: List[Segment]) -> List[EvaluatedSegment]:
        logger.info(f"Evaluating batch of {len(segments)} segments via Gemini")
        prompt = system_prompt + "\n\n" + user_prompt
        response = self.gemini_client.models.generate_content(
            model=self.gemini_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.2,
                response_mime_type="application/json"
            )
        )

        data = self._parse_json_result(response.text)
        return self._match_evaluations(data, segments)
