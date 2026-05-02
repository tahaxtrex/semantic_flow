"""Google Gemini provider for KAM knowledge extraction.

NEW in v1 (not in upstream semantic_kam). Mirrors the existing Gemini integration
in semantic_flow/src/evaluator.py:73,491-499 — same SDK, same call shape, same
GenerateContentConfig usage.

Default model is gemini-2.5-flash for the brute-force extraction phase: large
context window, low cost, structured-output support via response_schema.

If `use_schema=True` (default), the provider hands the full ExtractionOutput
Pydantic model as `response_schema`. Some deeply-aliased fields can confuse
Gemini's schema converter; on schema-conversion failure the provider falls
back to plain JSON-mode and logs a warning. This keeps the brute-force run
working while still preferring strict structured output when it succeeds.
"""

import json
import logging
import os
from typing import Optional

from google import genai
from google.genai import types

from ..prompts import MASTER_EXTRACTION_PROMPT, build_extraction_request
from ..schema import ExtractionOutput

logger = logging.getLogger(__name__)


class GeminiProvider:
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gemini-2.5-flash",
        client: Optional[genai.Client] = None,
        use_schema: bool = True,
    ):
        if client is not None:
            self.client = client
            self.api_key = None
        else:
            self.api_key = api_key or os.getenv("GEMINI_API_KEY")
            if not self.api_key:
                raise ValueError("GEMINI_API_KEY not found in environment")
            self.client = genai.Client(api_key=self.api_key)
        self.model = model
        self.use_schema = use_schema

    def extract(
        self,
        text: str,
        domain_hint: Optional[str] = None,
        course_title: Optional[str] = None,
        chapter_title: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: int = 8192,
    ) -> dict:
        user_message = build_extraction_request(
            text=text,
            domain_hint=domain_hint,
            course_title=course_title,
            chapter_title=chapter_title,
        )
        prompt = MASTER_EXTRACTION_PROMPT + "\n\n" + user_message
        logger.info("Gemini extraction: model=%s input_chars=%d", self.model, len(text))

        config_kwargs = {
            "temperature": temperature,
            "response_mime_type": "application/json",
            "max_output_tokens": max_tokens,
        }
        if self.use_schema:
            config_kwargs["response_schema"] = ExtractionOutput

        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(**config_kwargs),
            )
        except Exception as e:
            if self.use_schema and self._is_schema_conversion_error(e):
                logger.warning(
                    "Gemini rejected response_schema=ExtractionOutput (%s); "
                    "retrying with plain JSON mode.",
                    e,
                )
                config_kwargs.pop("response_schema", None)
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=types.GenerateContentConfig(**config_kwargs),
                )
            else:
                raise

        result_text = response.text
        if "```json" in result_text:
            result_text = result_text.split("```json")[1].split("```")[0].strip()
        elif "```" in result_text:
            result_text = result_text.split("```")[1].split("```")[0].strip()

        extracted_data = json.loads(result_text)
        logger.info(
            "Gemini extraction complete: concepts=%d meaning_units=%d skeleton_cards=%d",
            len(extracted_data.get("concepts", [])),
            len(extracted_data.get("meaning_units", [])),
            len(extracted_data.get("skeleton_cards", [])),
        )
        return extracted_data

    @staticmethod
    def _is_schema_conversion_error(exc: Exception) -> bool:
        msg = str(exc).lower()
        return any(
            marker in msg
            for marker in ("schema", "response_schema", "unsupported", "invalid argument")
        )

    def estimate_cost(self, text: str) -> dict:
        input_tokens = len(MASTER_EXTRACTION_PROMPT + text) // 4
        output_tokens = 8192
        if "flash" in self.model:
            input_cost_per_1k, output_cost_per_1k = 0.000075, 0.0003
        else:
            input_cost_per_1k, output_cost_per_1k = 0.00125, 0.005
        input_cost = (input_tokens / 1000) * input_cost_per_1k
        output_cost = (output_tokens / 1000) * output_cost_per_1k
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "input_cost_usd": round(input_cost, 6),
            "output_cost_usd": round(output_cost, 6),
            "total_cost_usd": round(input_cost + output_cost, 6),
        }
