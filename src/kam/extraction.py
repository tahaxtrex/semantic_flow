"""KAM extraction stage.

Reimplements the parts of `semantic_kam/project1/run_extraction.py` we need
as a library — provider selection, per-segment extraction, cache lookup —
without the upstream's hard-coded `config/.env` load, file I/O for output,
or `sys.path.insert` shenanigans.

Public entry points:
    select_provider(model_id, ...) -> ExtractionProvider
    KAMExtractor.extract_segment(segment) -> ExtractionResult
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Protocol

from src.models import Segment
from vendor.kam.providers import (
    AnthropicProvider,
    ExtractionProvider,
    GeminiProvider,
    OpenAIProvider,
)
from vendor.kam.schema import ExtractionOutput

from src.kam.extraction_cache import ExtractionCache, PROMPT_VERSION, SCHEMA_VERSION

logger = logging.getLogger(__name__)


SUPPORTED_MODELS: dict[str, type[ExtractionProvider]] = {
    "gemini-2.5-flash": GeminiProvider,
    "gemini-2.5-pro": GeminiProvider,
    "claude-sonnet-4-6": AnthropicProvider,
    "claude-3-5-sonnet-20241022": AnthropicProvider,
    "gpt-4o-mini": OpenAIProvider,
    "gpt-4o": OpenAIProvider,
}

DEFAULT_MODEL = "gemini-2.5-flash"


def select_provider(
    model_id: str = DEFAULT_MODEL,
    *,
    api_key: Optional[str] = None,
    client: Optional[object] = None,
) -> ExtractionProvider:
    """Instantiate the right provider for a model id.

    Recognized model ids are listed in SUPPORTED_MODELS. The provider class
    is keyed off the prefix of the model id (gemini-, claude-, gpt-) so
    callers can pass minor revisions of a known model without code changes.
    """
    cls = SUPPORTED_MODELS.get(model_id)
    if cls is None:
        cls = _provider_class_for_prefix(model_id)
    if cls is None:
        raise ValueError(
            f"Unrecognized extractor model {model_id!r}. "
            f"Supported: {sorted(SUPPORTED_MODELS)} or any model id starting with "
            "'gemini-', 'claude-', or 'gpt-'."
        )
    kwargs = {"model": model_id, "api_key": api_key, "client": client}
    return cls(**{k: v for k, v in kwargs.items() if v is not None or k == "model"})


def _provider_class_for_prefix(model_id: str) -> Optional[type[ExtractionProvider]]:
    if model_id.startswith("gemini-"):
        return GeminiProvider
    if model_id.startswith("claude-"):
        return AnthropicProvider
    if model_id.startswith("gpt-"):
        return OpenAIProvider
    return None


@dataclass
class ExtractionResult:
    extraction: ExtractionOutput
    raw_payload: dict
    cache_hit: bool
    extractor_model: str
    extracted_at: datetime
    prompt_version: str
    schema_version: str


class KAMExtractor:
    """Per-segment extraction with caching.

    The provider is constructed lazily on first cache miss so a fully-cached
    run never instantiates an LLM client (or requires an API key).
    """

    def __init__(
        self,
        *,
        model_id: str = DEFAULT_MODEL,
        cache: Optional[ExtractionCache] = None,
        cache_dir: Path | str | None = None,
        cache_enabled: bool = True,
        provider: Optional[ExtractionProvider] = None,
        api_key: Optional[str] = None,
        client: Optional[object] = None,
    ):
        self.model_id = model_id
        self._provider = provider
        self._provider_kwargs = {"api_key": api_key, "client": client}
        if cache is None:
            cache_kwargs = {"enabled": cache_enabled}
            if cache_dir is not None:
                cache_kwargs["cache_dir"] = cache_dir
            cache = ExtractionCache(**cache_kwargs)
        self.cache = cache

    @property
    def provider(self) -> ExtractionProvider:
        if self._provider is None:
            self._provider = select_provider(self.model_id, **self._provider_kwargs)
        return self._provider

    def extract_segment(
        self,
        segment: Segment,
        *,
        domain_hint: Optional[str] = None,
        course_title: Optional[str] = None,
        chapter_title: Optional[str] = None,
    ) -> ExtractionResult:
        key = self.cache.make_key(
            segment_text=segment.text, extractor_model=self.model_id
        )
        entry = self.cache.get(key)
        if entry is not None:
            logger.debug("KAM cache hit for segment %s", segment.segment_id)
            return ExtractionResult(
                extraction=ExtractionOutput.model_validate(entry.payload),
                raw_payload=entry.payload,
                cache_hit=True,
                extractor_model=self.model_id,
                extracted_at=_parse_iso(entry.metadata.get("written_at")),
                prompt_version=entry.metadata.get("prompt_version", PROMPT_VERSION),
                schema_version=entry.metadata.get("schema_version", SCHEMA_VERSION),
            )

        logger.info(
            "KAM cache miss for segment %s; calling provider %s",
            segment.segment_id,
            self.model_id,
        )
        raw = self.provider.extract(
            text=segment.text,
            domain_hint=domain_hint,
            course_title=course_title,
            chapter_title=chapter_title,
        )
        # Hydrate to validate; reject malformed payloads at the boundary.
        extraction = ExtractionOutput.model_validate(raw)
        self.cache.put(key, raw, metadata={"extractor_model": self.model_id})
        return ExtractionResult(
            extraction=extraction,
            raw_payload=raw,
            cache_hit=False,
            extractor_model=self.model_id,
            extracted_at=datetime.now(timezone.utc),
            prompt_version=PROMPT_VERSION,
            schema_version=SCHEMA_VERSION,
        )


def _parse_iso(value: Optional[str]) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)
