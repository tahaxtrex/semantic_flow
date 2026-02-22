import json
import logging
import os
import sys
import yaml
from pathlib import Path

from anthropic import Anthropic
from google import genai
from google.genai import types

from src.models import Segment, CourseMetadata, EvaluatedSegment, SectionScores, SectionReasoning

logger = logging.getLogger(__name__)

# After this many consecutive Claude failures the evaluator permanently switches to Gemini
# for the remainder of the run, avoiding repeated wasted API calls.
_MAX_CLAUDE_FAILURES = 2


class LLMEvaluator:
    """Evaluates pedagogical segments using Claude with a Gemini fallback.

    Failure tracking: if Claude fails _MAX_CLAUDE_FAILURES times in a row, the evaluator
    sets _claude_disabled=True and routes all subsequent segments directly to Gemini
    without attempting Claude again, avoiding repeated failed API calls per segment.
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
        prompt = f"""
You are an expert pedagogical evaluator. Evaluate the following course segment based strictly on the provided rubrics.

COURSE METADATA:
Title: {metadata.title}
Target Audience: {metadata.target_audience}
Prerequisites: {', '.join(metadata.prerequisites)}

RUBRICS:
{self.rubrics}

SEGMENT TEXT (Heading: {segment.heading or 'None'}):
{segment.text}

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

    def evaluate(self, metadata: CourseMetadata, segment: Segment) -> EvaluatedSegment:
        prompt = self._build_prompt(metadata, segment)

        # 1. Primary Model: Claude 4.6 Sonnet (per spec ADR-002)
        # Skip Claude entirely if it has already failed too many times this run.
        if not self._claude_disabled:
            try:
                result = self._call_claude(prompt, segment)
                self._claude_failure_count = 0  # reset streak on success
                return result
            except Exception as e:
                self._claude_failure_count += 1
                logger.error(f"Claude evaluation failed for Segment {segment.segment_id}: {e}")
                if self._claude_failure_count >= _MAX_CLAUDE_FAILURES:
                    logger.warning(
                        f"Claude has failed {self._claude_failure_count} consecutive time(s). "
                        "Permanently switching to Gemini for the remainder of this run (ADR-007)."
                    )
                    self._claude_disabled = True
                else:
                    logger.info(
                        f"Claude failure {self._claude_failure_count}/{_MAX_CLAUDE_FAILURES}. "
                        "Cascading to Gemini 2.5 Flash for this segment..."
                    )
        else:
            logger.info(f"Claude disabled this run. Routing Segment {segment.segment_id} directly to Gemini.")

        # 2. Fallback Model: Gemini 2.5 Flash
        try:
            return self._call_gemini(prompt, segment)
        except Exception as gemini_e:
            logger.error(f"Gemini evaluation ALSO failed for Segment {segment.segment_id}: {gemini_e}")
            logger.critical("Fatal API Error. Both models failed. Hard crashing per ADR-002.")
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
        # NOTE: Using the user's requested model string
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
            scores=scores,
            reasoning=reasoning
        )

    def _call_gemini(self, prompt: str, segment: Segment) -> EvaluatedSegment:
        if not self.gemini_client:
            raise ValueError("Gemini API key not configured")
            
        logger.info(f"Evaluating Segment {segment.segment_id} via Gemini")
        # Ensure Gemini outputs JSON exclusively
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
            scores=scores,
            reasoning=reasoning
        )
