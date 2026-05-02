"""KAM Adapter orchestrator.

Glues the extraction stage and the three v1 graph-deterministic validators
together into a single ModuleGraphReport for the Module Gate. No new
extraction or validation logic lives here; the adapter's only job is
ordering, wiring, and packaging.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, List, Optional, Tuple

from src.models import Segment

from .extraction import KAMExtractor
from .extraction_cache import PROMPT_VERSION
from .models import (
    ConceptEvent,
    EvidenceSpan,
    ExtractionMetadata,
    ModuleGraphReport,
)
from .spans import first_span
from .validators.cycle import run_v_cyc
from .validators.density import run_v_dens
from .validators.sequencing import ConceptKey, run_v_fwd_shallow

logger = logging.getLogger(__name__)


class KAMAdapter:
    """One adapter per pipeline run; owns a single KAMExtractor + cache."""

    def __init__(
        self,
        *,
        model_id: str = "gemini-2.5-flash",
        cache_enabled: bool = True,
        extractor: Optional[KAMExtractor] = None,
    ):
        self.model_id = model_id
        self.extractor = extractor or KAMExtractor(
            model_id=model_id, cache_enabled=cache_enabled
        )

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def process_segment(
        self,
        segment: Segment,
        *,
        domain_hint: Optional[str] = None,
        course_title: Optional[str] = None,
        chapter_title: Optional[str] = None,
    ) -> ModuleGraphReport:
        if segment.segment_type != "instructional":
            return self._empty_report(segment, skipped_reason="non_instructional")
        if not segment.text or not segment.text.strip():
            return self._empty_report(segment, skipped_reason="empty_segment")

        try:
            ex_res = self.extractor.extract_segment(
                segment,
                domain_hint=domain_hint,
                course_title=course_title,
                chapter_title=chapter_title,
            )
        except Exception as exc:
            logger.error(
                "KAM extraction failed for segment %s: %s", segment.segment_id, exc
            )
            return self._empty_report(segment, skipped_reason="extraction_failed")

        extraction = ex_res.extraction

        concept_events, concept_keys = self._build_concept_events(
            segment, extraction.concepts
        )
        prereq_edges = self._build_prereq_edges(extraction.relations_graph)

        cyc_res = run_v_cyc(segment_id=segment.segment_id, prereq_edges=prereq_edges)
        dens_res = run_v_dens(
            segment_id=segment.segment_id,
            segment_text=segment.text,
            concept_count=len(extraction.concepts),
            affected_concepts=[c.concept_id for c in concept_events],
        )
        fwd_res = run_v_fwd_shallow(
            segment_id=segment.segment_id,
            segment_text=segment.text,
            concepts=concept_keys,
            prereq_edges=prereq_edges,
        )

        findings = [*cyc_res.findings, *dens_res.findings, *fwd_res.findings]

        meta = ExtractionMetadata(
            extractor_model=self.model_id,
            extractor_prompt_version=PROMPT_VERSION,
            extracted_at=ex_res.extracted_at,
            cache_hit=ex_res.cache_hit,
            extraction_concept_count=len(extraction.concepts),
            extraction_relation_count=len(extraction.relations_graph),
            cycle_validator_timeout_hit=cyc_res.timeout_hit,
            extraction_confidence_min="medium",
        )
        return ModuleGraphReport(
            segment_id=segment.segment_id,
            concepts=concept_events,
            prereq_edges=prereq_edges,
            findings=findings,
            extraction_metadata=meta,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_concept_events(
        segment: Segment, concepts: List[Any]
    ) -> Tuple[List[ConceptEvent], List[ConceptKey]]:
        events: List[ConceptEvent] = []
        keys: List[ConceptKey] = []
        for c in concepts:
            aliases: Tuple[str, ...] = tuple(getattr(c, "aliases", []) or [])
            canonical = c.name
            keys.append(
                ConceptKey(
                    concept_id=c.concept_id,
                    canonical_name=canonical,
                    aliases=aliases,
                )
            )
            span = first_span(segment.text, canonical, aliases)
            if span is None:
                # Concept extracted but never appears in the raw text — likely
                # a hallucinated identifier. Skip it from concept_events but
                # keep it in `keys` so V-FWD-shallow's edge-walk can still
                # consider it (it'll harmlessly skip on missing spans).
                continue
            events.append(
                ConceptEvent(
                    concept_id=c.concept_id,
                    canonical_name=canonical,
                    segment_id=segment.segment_id,
                    first_span=EvidenceSpan(
                        segment_id=segment.segment_id,
                        span_start=span.start,
                        span_end=span.end,
                        span_text=segment.text[span.start : span.end],
                        confidence="medium",
                        source="post_hoc_substring_match",
                    ),
                )
            )
        return events, keys

    @staticmethod
    def _build_prereq_edges(relations_graph: List[Any]) -> List[Tuple[str, str]]:
        edges: List[Tuple[str, str]] = []
        for rel in relations_graph:
            if getattr(rel, "type", None) != "prerequisite":
                continue
            edges.append((rel.from_, rel.to))
        return edges

    def _empty_report(self, segment: Segment, *, skipped_reason: str) -> ModuleGraphReport:
        meta = ExtractionMetadata(
            extractor_model=self.model_id,
            extractor_prompt_version=PROMPT_VERSION,
            extracted_at=datetime.now(timezone.utc),
            cache_hit=False,
            extraction_concept_count=0,
            extraction_relation_count=0,
            cycle_validator_timeout_hit=False,
            extraction_confidence_min="low",
        )
        return ModuleGraphReport(
            segment_id=segment.segment_id,
            concepts=[],
            prereq_edges=[],
            findings=[],
            extraction_metadata=meta,
            skipped_reason=skipped_reason,  # type: ignore[arg-type]
        )
