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
        Evaluates each instructional segment in batches on 5 rubrics (goal focus,
        readability, clarity, example concreteness, example coherence).
        Each segment also produces a 1-2 sentence content summary for use in
        the Course Gate. Cross-segment context is injected to detect repetition
        and non-progressive examples (ADR-030).

    Gate 2 — Course Gate:
        Executes a single capstone call once ALL Module Gate evaluations are done.
        Receives the course metadata, non-instructional segment text (TOC, Preface),
        ordered per-segment summaries with Module Gate scores, and a quality
        summary. Scores 5 holistic rubrics (prerequisite alignment, structural
        usability, business relevance, fluidity, instructional alignment).
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

        Gemini may return any of:
        - {"scores": {...}, "reasoning": {...}}               → ideal
        - [{"scores": {...}, ...}]                           → list-wrapped
        - {"submit_course_evaluation": {"scores": {...}}}   → key-wrapped
        - {"rubric_scores": [{"id": "x", "score": 7, "rationale": "..."}]} → list-of-rubric-objects
        """
        if isinstance(raw, list):
            raw = raw[0]
        if isinstance(raw, dict):
            # Ideal case: has 'scores' key directly
            if "scores" in raw:
                return raw
            # Gemini sometimes returns rubric_scores as a list of {id, score, rationale} objects
            if "rubric_scores" in raw and isinstance(raw["rubric_scores"], list):
                scores = {}
                reasoning = {}
                for item in raw["rubric_scores"]:
                    if isinstance(item, dict) and "id" in item:
                        rubric_id = item["id"]
                        scores[rubric_id] = item.get("score", 0)
                        reasoning[f"{rubric_id}_rationale"] = item.get("rationale", item.get("reasoning", ""))
                return {"scores": scores, "reasoning": reasoning}
            # Try to unwrap one level of nesting under known keys
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
    # MODULE GATE — Field Definitions (derived from models to avoid duplication)
    # -------------------------------------------------------------------------

    # Single source of truth: ModuleScores / CourseScores in models.py.
    # Adding a field there automatically updates these lists and the tool schemas.
    _MODULE_SCORE_FIELDS = list(ModuleScores.model_fields.keys())
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

    def _build_module_batch_prompts(
        self,
        metadata: CourseMetadata,
        segments: List[Segment],
        previous_summaries: Optional[List[str]] = None,
    ) -> Tuple[str, str]:
        # ADR-030: Build cross-segment context
        cross_segment_ctx = ""
        if previous_summaries:
            narrative = " | ".join(previous_summaries[-5:])  # last 5, truncated
            if len(narrative) > 500:
                narrative = narrative[:500] + "..."
            cross_segment_ctx = f"""\n\nCOURSE NARRATIVE SO FAR (previous segments covered):
{narrative}

Use this context to detect repetition, non-progressive examples, and redundant content
across segments. If a segment repeats topics already covered, note this in your rationale."""

        system_prompt = f"""
You are an expert pedagogical evaluator. Evaluate the provided course segments based strictly on the following MODULE rubrics.

COURSE CONTEXT (for reference only — do not score course-level structure here):
Title: {metadata.title}
Target Audience: {metadata.target_audience}
Learning Outcomes: {', '.join(metadata.learning_outcomes) if metadata.learning_outcomes else 'None specified'}

MODULE RUBRICS (score each segment on ONLY these 5 dimensions):
{self.module_rubrics_yaml}

SCORING PROCEDURE — Three-Step Calibration (apply for every rubric, every segment):
For each rubric dimension, follow these three steps IN ORDER before committing a score:

  Step 1 — IDENTIFY: Find 2-3 specific evidence pieces from the segment text relevant to this rubric.
           Quote short phrases. If no evidence exists, the score must be in band 1-3.

  Step 2 — ANCHOR to one of these bands based on evidence quality:
           1-3 (Poor):      Missing, wrong, or fundamentally inadequate
           4-6 (Adequate):  Present but generic, trivial, or incomplete
           7-8 (Good):      Solid, well-crafted, clearly effective
           9-10 (Excellent): Exceptional, publishable quality, best-in-class

  Step 3 — DIFFERENTIATE within the band. Pick the specific integer.
           A score of 7 vs 8 must be justified by a concrete quality difference.

CALIBRATION ANCHORS — scores MUST match these exemplars. If in doubt, score lower, not higher.

  goal_focus:
    3 — Long tangents into unrelated material; the segment's stated topic is buried or absent
    5 — Core topic present but padded with loosely related digressions (history, side-notes)
    8 — Stays on-topic throughout; every paragraph directly serves the stated learning goal

  text_readability:
    3 — Walls of code or dense paragraphs with no explanatory prose; impossible without an instructor
    5 — Readable but has occasional run-on sentences, unexplained acronyms, or abrupt transitions
    8 — Clear, well-paced prose; every code block is preceded or followed by plain-language explanation

  pedagogical_clarity:
    3 — New jargon introduced without any definition; inconsistent or contradictory terminology
    5 — Most terms eventually defined, but some are used pages before their introduction
    8 — Every new term defined on first use; notation is consistent from start to finish

  example_concreteness:
    3 — No examples at all, or purely abstract placeholders (a=1, foo, bar, x=0)
    5 — Trivial academic data only: a=5, x=[1,2,3], "hello world", print(42), dummy variables
    8 — Realistic, domain-grounded scenarios: student records, inventory system, sales data, employee payroll

  example_coherence:
    3 — Completely disconnected examples; each sub-section invents an unrelated new scenario
    5 — Examples are loosely themed but don't build on each other; narrative resets between topics
    8 — Examples share a consistent domain or running scenario that accumulates across the segment
{cross_segment_ctx}

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
        for i, s in enumerate(segments):
            user_prompt += f"--- SEGMENT ID: {s.segment_id} ---\n"
            user_prompt += f"Heading: {s.heading or 'None'}\n"
            # ADR-030: inject previous segment summary for cross-segment awareness
            if i > 0 and segments[i-1].segment_id in [seg.segment_id for seg in segments[:i]]:
                user_prompt += f"(Previous segment covered: see segment {segments[i-1].segment_id} above)\n"
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
                example_concreteness=0, example_coherence=0,
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
                    example_concreteness=0, example_coherence=0,
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
            model="sonnet-4-6",
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
                response_mime_type="application/json",
                response_schema=self._MODULE_EVAL_TOOL["input_schema"]
            )
        )
        try:
            raw = self._parse_json_result(response.text)
            data = self._unwrap_gemini_list(raw)
        except Exception as e:
            logger.error(f"[Module Gate] Gemini JSON parse/unwrap failed: {e}. Raw response: {response.text[:500]}")
            raise
        return self._match_module_evaluations(data, segments)

    def evaluate_batch(self, metadata: CourseMetadata, segments: List[Segment],
                       previous_summaries: Optional[List[str]] = None) -> List[EvaluatedSegment]:
        """MODULE GATE: Evaluate a batch of segments on the 5 Module rubrics.
        Non-instructional segments are passed through with 0 scores and no summary.
        Instructional segments get 5 scores + a content summary for the Course Gate.

        Args:
            previous_summaries: Summaries from previously evaluated batches
                for cross-segment context injection (ADR-030).
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
                        example_concreteness=0, example_coherence=0,
                    ),
                    reasoning=ModuleReasoning(),
                    summary="",
                ))
            else:
                instructional_segments.append(segment)

        if not instructional_segments:
            return sorted(results, key=lambda x: x.segment_id)

        system_prompt, user_prompt = self._build_module_batch_prompts(
            metadata, instructional_segments, previous_summaries
        )

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
    # COURSE GATE — Field Definitions (derived from models to avoid duplication)
    # -------------------------------------------------------------------------

    _COURSE_SCORE_FIELDS = list(CourseScores.model_fields.keys())  # ADR-016: includes instructional_alignment
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
        evaluated_segments: list,
        non_instructional_segments: list,
        is_partial_course: bool = False,
    ) -> Tuple[str, str]:
        partial_notice = ""
        if is_partial_course:
            partial_notice = """

> [!IMPORTANT]
> PARTIAL COURSE FILE: This evaluation covers a FRAGMENT of a larger course.
> The file does not contain the full Table of Contents or all course modules.
> DO NOT penalise scores for absent modules, an incomplete table of contents,
> or missing introductory material that exists in other file fragments.
> Evaluate ONLY the content and structure that IS present in this fragment.
"""
        system_prompt = f"""
You are an expert pedagogical evaluator. You are performing a COURSE-LEVEL assessment.
Your job is to evaluate the overall structure, coherence and relevance of the course as a whole — NOT individual segments.{partial_notice}

COURSE METADATA:
Title: {metadata.title}
Author: {metadata.author or 'Unknown'}
Target Audience: {metadata.target_audience}
Level: {metadata.level or 'Not specified'}
Prerequisites: {', '.join(metadata.prerequisites) if metadata.prerequisites else 'None specified'}
Learning Outcomes: {', '.join(metadata.learning_outcomes) if metadata.learning_outcomes else 'None specified'}
Description: {metadata.description or 'Not provided'}

COURSE RUBRICS (score the ENTIRE COURSE on ONLY these dimensions):
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
            user_prompt += "\n## Sequential Module Summaries (in order, with Module Gate quality scores)\n"
            user_prompt += "The following summaries represent each module/chapter of the course in order.\n"
            user_prompt += "Module Gate scores are provided for context — use them to calibrate your assessment.\n\n"
            for seg in sorted(instructional_with_summary, key=lambda x: x.segment_id):
                heading_label = f"**{seg.heading}**" if seg.heading else f"Module {seg.segment_id}"
                # ADR-030 (critic.v3 Issue 9): append Module Gate scores to each summary
                scores_dict = seg.scores.model_dump() if hasattr(seg.scores, 'model_dump') else {}
                score_str = ", ".join(f"{k}={v}" for k, v in scores_dict.items()) if scores_dict else ""
                user_prompt += f"- {heading_label}: {seg.summary}"
                if score_str:
                    user_prompt += f" [Module Gate: {score_str}]"
                user_prompt += "\n"

            # ADR-030: MODULE GATE QUALITY SUMMARY section
            all_scores = []
            for seg in instructional_with_summary:
                scores_dict = seg.scores.model_dump() if hasattr(seg.scores, 'model_dump') else {}
                seg_avg = sum(scores_dict.values()) / max(len(scores_dict), 1) if scores_dict else 0
                all_scores.append((seg.segment_id, seg.heading, seg_avg, scores_dict))

            if all_scores:
                overall_avg = sum(s[2] for s in all_scores) / len(all_scores)
                lowest = min(all_scores, key=lambda s: s[2])
                user_prompt += f"\n## MODULE GATE QUALITY SUMMARY\n"
                user_prompt += f"- Average Module Gate score across {len(all_scores)} segments: {overall_avg:.1f}/10\n"
                user_prompt += f"- Lowest-scoring segment: ID {lowest[0]} ({lowest[1] or 'untitled'}) — avg {lowest[2]:.1f}\n"

                # Pass weakest segment's actual text and per-dimension rationales so the
                # Course Gate can reason about evidence quality, not just numeric scores.
                lowest_seg = next(
                    (s for s in instructional_with_summary if s.segment_id == lowest[0]), None
                )
                if lowest_seg:
                    user_prompt += (
                        f"- Weakest segment text sample:\n"
                        f"  {lowest_seg.text[:600].strip()}\n"
                    )
                    reasoning = getattr(lowest_seg, 'reasoning', None)
                    if reasoning:
                        reasoning_dict = (
                            reasoning.model_dump() if hasattr(reasoning, 'model_dump') else {}
                        )
                        rationale_lines = []
                        for dim in self._MODULE_SCORE_FIELDS:
                            r = reasoning_dict.get(f'{dim}_rationale', '') or ''
                            if r:
                                rationale_lines.append(f"  {dim}: {r[:200]}")
                        if rationale_lines:
                            user_prompt += "- Weakest segment per-dimension rationales:\n"
                            user_prompt += "\n".join(rationale_lines) + "\n"

                # Top 3 strongest segments as contrast
                top3 = sorted(all_scores, key=lambda s: s[2], reverse=True)[:3]
                user_prompt += "- Top 3 strongest segments:\n"
                for seg_id, heading, avg, _ in top3:
                    user_prompt += f"  ID {seg_id} ({heading or 'untitled'}): avg {avg:.1f}\n"

                # Detect repetition: segments with very similar summaries
                summaries_text = [s.summary.lower() for s in instructional_with_summary if s.summary]
                repeated_topics = []
                for i_s in range(len(summaries_text)):
                    for j_s in range(i_s + 1, len(summaries_text)):
                        # Simple word overlap check
                        words_a = set(summaries_text[i_s].split())
                        words_b = set(summaries_text[j_s].split())
                        if len(words_a) > 3 and len(words_b) > 3:
                            overlap = len(words_a & words_b) / min(len(words_a), len(words_b))
                            if overlap > 0.6:
                                seg_a = instructional_with_summary[i_s].segment_id
                                seg_b = instructional_with_summary[j_s].segment_id
                                repeated_topics.append(f"Segments {seg_a} and {seg_b}")
                if repeated_topics:
                    user_prompt += f"- ⚠️ Potential content repetition detected: {'; '.join(repeated_topics[:5])}\n"

        user_prompt += f"\n\nNow evaluate the course holistically on the Course Gate rubrics listed above."
        return system_prompt.strip(), user_prompt.strip()

    # -------------------------------------------------------------------------
    # COURSE GATE — Execution
    # -------------------------------------------------------------------------

    def _make_incomplete_course_assessment(self) -> CourseAssessment:
        return CourseAssessment(
            scores=CourseScores(
                prerequisite_alignment=0, structural_usability=0,
                business_relevance=0, fluidity_continuity=0,
                instructional_alignment=0,  # ADR-016
            ),
            reasoning=CourseReasoning(),
            overall_score=0.0,
        )

    def _detect_partial_course(
        self,
        non_instructional_segments: list,
        evaluated_segments: list,
    ) -> bool:
        """Return True when the file appears to be a fragment of a larger course.

        Heuristic: a file is considered partial when BOTH of the following hold:
        1. No non-instructional segment contains obvious TOC/preface text.
        2. No instructional segment heading suggests a first chapter/module
           (e.g. "Chapter 1", "Module 1", "Introduction").

        When True, a disclaimer is injected into the Course Gate prompt so the
        LLM does not penalise missing introductory material or module gaps that
        live in separate PDF files (critic.v2.md Issue 3).
        """
        import re as _re
        _TOC_SIGNALS = _re.compile(
            r'\b(table of contents|preface|introduction|chapter\s*1|module\s*1)\b',
            _re.IGNORECASE,
        )
        for seg in non_instructional_segments:
            if _TOC_SIGNALS.search(getattr(seg, 'text', '') or ''):
                return False
            if _TOC_SIGNALS.search(getattr(seg, 'heading', '') or ''):
                return False
        _FIRST_CHAPTER_RE = _re.compile(
            r'^(chapter\s*1\b|module\s*1\b|unit\s*1\b|introduction\b)',
            _re.IGNORECASE,
        )
        for seg in evaluated_segments:
            if getattr(seg, 'segment_type', '') == 'instructional':
                if _FIRST_CHAPTER_RE.match(getattr(seg, 'heading', '') or ''):
                    return False
        return True

    def _call_claude_course(self, system_prompt: str, user_prompt: str) -> CourseAssessment:
        logger.info("[Course Gate] Running capstone course evaluation via Claude")
        response = self.anthropic_client.messages.create(
            model="sonnet-4-6",
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
                response_mime_type="application/json",
                response_schema=self._COURSE_EVAL_TOOL["input_schema"]
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
    ) -> Tuple[CourseAssessment, bool]:
        """COURSE GATE: Single capstone call evaluating the full course on holistic rubrics.

        Args:
            metadata: Extracted course metadata.
            evaluated_segments: All EvaluatedSegment objects from the Module Gate (includes summaries).
            non_instructional_segments: Raw Segment objects for TOC/Preface to give structural context.

        Returns:
            Tuple of (CourseAssessment, is_partial_course).
            is_partial_course is True when the file appears to be a fragment of a larger course.
        """
        non_instructional_segments = non_instructional_segments or []
        is_partial = self._detect_partial_course(non_instructional_segments, evaluated_segments)
        if is_partial:
            logger.info(
                "[Course Gate] Partial-course file detected — injecting fragment disclaimer into prompt."
            )
        system_prompt, user_prompt = self._build_course_prompts(
            metadata, evaluated_segments, non_instructional_segments, is_partial
        )

        try:
            if self.anthropic_client:
                assessment = self._retry_call(
                    lambda: self._call_claude_course(system_prompt, user_prompt),
                    "Claude",
                    1  # Course Gate is always 1 call
                )
            elif self.gemini_client:
                assessment = self._retry_call(
                    lambda: self._call_gemini_course(system_prompt, user_prompt),
                    "Gemini",
                    1
                )
            else:
                raise ValueError("No API client configured.")
        except Exception as e:
            logger.error(f"[Course Gate] Course evaluation failed after all retries: {e}")
            return self._make_incomplete_course_assessment(), is_partial

        return assessment, is_partial
