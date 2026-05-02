"""Anthropic Claude provider for KAM knowledge extraction.

Vendored from semantic_kam/project1/llm_providers/anthropic_provider.py.
Patches vs upstream:
  - Relative import for prompts (vendored package layout).
  - Optional injectable Anthropic client so callers can share connection pool / rate-limit state.
  - Print statements replaced with logging.
"""

import json
import logging
import os
from typing import Optional

from anthropic import Anthropic

from ..prompts import MASTER_EXTRACTION_PROMPT, build_extraction_request

logger = logging.getLogger(__name__)


class AnthropicProvider:
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "claude-sonnet-4-6",
        client: Optional[Anthropic] = None,
    ):
        if client is not None:
            self.client = client
            self.api_key = None
        else:
            self.api_key = api_key or os.getenv("CLAUDE_API_KEY") or os.getenv("ANTHROPIC_API_KEY")
            if not self.api_key:
                raise ValueError("CLAUDE_API_KEY or ANTHROPIC_API_KEY not found in environment")
            self.client = Anthropic(api_key=self.api_key)
        self.model = model

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
        logger.info("Anthropic extraction: model=%s input_chars=%d", self.model, len(text))
        response = self.client.messages.create(
            model=self.model,
            system=MASTER_EXTRACTION_PROMPT,
            messages=[{"role": "user", "content": user_message}],
            temperature=temperature,
            max_tokens=max_tokens,
        )

        result_text = response.content[0].text
        if "```json" in result_text:
            result_text = result_text.split("```json")[1].split("```")[0].strip()
        elif "```" in result_text:
            result_text = result_text.split("```")[1].split("```")[0].strip()

        extracted_data = json.loads(result_text)
        logger.info(
            "Anthropic extraction complete: concepts=%d meaning_units=%d skeleton_cards=%d",
            len(extracted_data.get("concepts", [])),
            len(extracted_data.get("meaning_units", [])),
            len(extracted_data.get("skeleton_cards", [])),
        )
        return extracted_data

    def estimate_cost(self, text: str) -> dict:
        input_tokens = len(MASTER_EXTRACTION_PROMPT + text) // 4
        output_tokens = 8192
        input_cost = (input_tokens / 1000) * 0.003
        output_cost = (output_tokens / 1000) * 0.015
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "input_cost_usd": round(input_cost, 4),
            "output_cost_usd": round(output_cost, 4),
            "total_cost_usd": round(input_cost + output_cost, 4),
        }
