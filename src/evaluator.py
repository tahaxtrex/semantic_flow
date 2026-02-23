import json
import logging
import os
import sys
import time
import yaml
from pathlib import Path

from anthropic import Anthropic
from google import genai
from google.genai import types

from src.models import Segment, CourseMetadata, EvaluatedSegment, SectionScores, SectionReasoning

logger = logging.getLogger(__name__)

# After this many consecutive segment-level Claude failures (after all retries exhausted)
# the evaluator permanently switches to Gemini for the remainder of the run.
_MAX_CLAUDE_FAILURES = 2

# Per-segment retry budget for each model before escalating.
MAX_RETRIES_PER_SEGMENT = 3
RETRY_BACKOFF_SECONDS = 5  # doubles each attempt: 5s → 10s → 20s


class LLMEvaluator:
    """Evaluates pedagogical segments using Claude with a Gemini fallback.

    Retry logic: each model gets MAX_RETRIES_PER_SEGMENT attempts on the SAME
    segment (with exponential back-off) before escalating to the other model or
    hard-failing. Claude is permanently disabled after _MAX_CLAUDE_FAILURES
    consecutive segment-level failures (i.e. all retries for a segment exhausted).
    """

    def __init__(self, config_path: Path):
        self.config_path = Path(config_path)
        self.rubrics = self._load_rubrics()

        self._claude_failure_count = 0
        self._claude_disabled = False

        self.anthropic_key = os.getenv("ANTHROPIC_API_KEY")
        self.gemini_key = os.getenv("GEMINI_API_KEY")

        if self.anthropic_key:
            self.anthropic_client = Anthropic(api_key=self.anthropic_key)
        else:
            self.anthropic_client = None
            logger.warning("ANTHROPIC_API_KEY missing. Primary model will fail.")

        if self.gemini_key:
            self.gemini_client = genai.Client(api_key=self.gemini_key)
            self.gemini_model = 'gemini-2.5-flash'
        else:
            self.gemini_client = None
            logger.warning("GEMINI_API_KEY missing. Fallback model will fail.")

    def _load_rubrics(self) -> str:
        with open(self.config_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
        return yaml.dump(data, sort_keys=False)

    def _build_prompt(self, metadata: CourseMetadata, segment: Segment) -> str:
        segment_type_note = ""
        if segment.segment_type != "instructional":
            segment_type_note = (
                f"\n- This segment is classified as type: **{segment.segment_type}**. "
                "It is not instructional narrative — adjust rubric scoring accordingly. "
                "Exercises and solutions do not require instructional flow or goal focus; "
                "reference tables do not require pedagogical clarity."
            )

        prompt = f"""
You are an expert pedagogical evaluator. Evaluate the following course segment based strictly on the provided rubrics.

COURSE METADATA:
Title: {metadata.title}
Target Audience: {metadata.target_audience}
Prerequisites: {', '.join(metadata.prerequisites) if metadata.prerequisites else 'None specified'}
Learning Outcomes: {', '.join(metadata.learning_outcomes) if metadata.learning_outcomes else 'None specified'}

RUBRICS:
{self.rubrics}

SEGMENT TEXT (Heading: {segment.heading or 'None'}):
{segment.text}

EXTRACTION NOTES (pipeline artifacts — do not penalise the course for these):
- Figures referenced in the text (e.g. "Fig. 1.1") are not available as images. Evaluate figure references positively as indicators of visual support.
- [FIGURE X.Y: caption] markers show a figure caption extracted from the PDF — treat as evidence of visual content.
- Text was extracted from PDF; minor formatting artifacts (e.g. [?] placeholders) are NOT a property of the course.
- [TABLE: ...] markers indicate a table was detected but could not be rendered as prose — treat as a structured reference element, not missing content.
- [CODE] / [/CODE] blocks contain verbatim code examples extracted from a monospace font region.{segment_type_note}

Your response must be ONLY valid JSON adhering to the following structure. Do not include markdown blocks or any other text.
{{
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
"""
        return prompt.strip()

    def _retry_call(self, call_fn, model_name: str, segment: Segment):
        """Call call_fn with per-segment retry + exponential back-off.

        Returns the result on success. Raises the last exception if all attempts fail.
        """
        last_exc = None
        for attempt in range(1, MAX_RETRIES_PER_SEGMENT + 1):
            try:
                result = call_fn()
                if attempt > 1:
                    logger.info(
                        f"{model_name} succeeded on attempt {attempt}/{MAX_RETRIES_PER_SEGMENT} "
                        f"for Segment {segment.segment_id}."
                    )
                return result
            except Exception as e:
                last_exc = e
                wait = RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1))  # 5, 10, 20
                if attempt < MAX_RETRIES_PER_SEGMENT:
                    logger.warning(
                        f"{model_name} attempt {attempt}/{MAX_RETRIES_PER_SEGMENT} failed for "
                        f"Segment {segment.segment_id}: {e}. Retrying in {wait}s..."
                    )
                    time.sleep(wait)
                else:
                    logger.error(
                        f"{model_name} exhausted all {MAX_RETRIES_PER_SEGMENT} retries for "
                        f"Segment {segment.segment_id}: {e}"
                    )
        raise last_exc

    def evaluate(self, metadata: CourseMetadata, segment: Segment) -> EvaluatedSegment:
        prompt = self._build_prompt(metadata, segment)

        # 1. Primary Model: Claude (skip if permanently disabled this run)
        if not self._claude_disabled:
            try:
                result = self._retry_call(
                    lambda: self._call_claude(prompt, segment),
                    "Claude",
                    segment
                )
                self._claude_failure_count = 0  # reset streak on segment success
                return result
            except Exception as e:
                self._claude_failure_count += 1
                logger.error(
                    f"Claude failed all retries for Segment {segment.segment_id}: {e}"
                )
                if self._claude_failure_count >= _MAX_CLAUDE_FAILURES:
                    logger.warning(
                        f"Claude has failed {self._claude_failure_count} consecutive segment(s). "
                        "Permanently switching to Gemini for the remainder of this run (ADR-007)."
                    )
                    self._claude_disabled = True
                else:
                    logger.info(
                        f"Claude segment failure {self._claude_failure_count}/{_MAX_CLAUDE_FAILURES}. "
                        "Cascading to Gemini 2.5 Flash for this segment..."
                    )
        else:
            logger.info(f"Claude disabled this run. Routing Segment {segment.segment_id} directly to Gemini.")

        # 2. Fallback Model: Gemini 2.5 Flash (also with per-segment retries)
        try:
            return self._retry_call(
                lambda: self._call_gemini(prompt, segment),
                "Gemini",
                segment
            )
        except Exception as gemini_e:
            logger.error(f"Gemini evaluation ALSO failed all retries for Segment {segment.segment_id}: {gemini_e}")
            logger.critical("Fatal API Error. Both models exhausted retries. Hard crashing per ADR-002.")
            sys.exit(1)

    def _parse_json_result(self, text: str) -> dict:
        text = text.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        return json.loads(text.strip())

    def _call_claude(self, prompt: str, segment: Segment) -> EvaluatedSegment:
        if not self.anthropic_client:
            raise ValueError("Anthropic API key not configured")

        logger.info(f"Evaluating Segment {segment.segment_id} via Claude")
        response = self.anthropic_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2500,
            temperature=0.2,
            messages=[{"role": "user", "content": prompt}]
        )

        raw_text = response.content[0].text
        data = self._parse_json_result(raw_text)

        scores = SectionScores(**data.get("scores", {}))
        reasoning = SectionReasoning(**data.get("reasoning", {}))

        return EvaluatedSegment(
            segment_id=segment.segment_id,
            heading=segment.heading,
            text=segment.text,
            segment_type=segment.segment_type,
            scores=scores,
            reasoning=reasoning
        )

    def _call_gemini(self, prompt: str, segment: Segment) -> EvaluatedSegment:
        if not self.gemini_client:
            raise ValueError("Gemini API key not configured")

        logger.info(f"Evaluating Segment {segment.segment_id} via Gemini")
        response = self.gemini_client.models.generate_content(
            model=self.gemini_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.2,
                response_mime_type="application/json"
            )
        )

        data = self._parse_json_result(response.text)
        scores = SectionScores(**data.get("scores", {}))
        reasoning = SectionReasoning(**data.get("reasoning", {}))

        return EvaluatedSegment(
            segment_id=segment.segment_id,
            heading=segment.heading,
            text=segment.text,
            segment_type=segment.segment_type,
            scores=scores,
            reasoning=reasoning
        )
