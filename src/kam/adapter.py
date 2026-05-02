"""KAM Adapter orchestrator.

Wires the extraction stage and the graph-deterministic validators together
into a single ModuleGraphReport for the Module Gate.
"""

import logging
from typing import List, Optional

from src.models import Segment
from .extraction import KAMExtractor, select_provider
from .models import ModuleGraphReport, ConceptEvent, EvidenceSpan, ExtractionMetadata
from .validators.cycle import run_v_cyc
from .validators.density import run_v_dens
from .validators.sequencing import run_v_fwd_shallow
from .spans import find_spans, first_span

logger = logging.getLogger(__name__)

class KAMAdapter:
    def __init__(self, model_id: str = "gemini-2.5-flash", cache_enabled: bool = True):
        self.provider = select_provider(model_id)
        self.extractor = KAMExtractor(provider=self.provider, cache_enabled=cache_enabled)

    def process_segment(self, segment: Segment) -> ModuleGraphReport:
        """Runs the full KAM extraction and validation pipeline on a segment."""
        
        # 1. Skip non-instructional segments immediately
        if not segment.metadata.get("is_instructional", True):
            return self._empty_report(segment, skipped_reason="non_instructional")
            
        if not segment.text or not segment.text.strip():
            return self._empty_report(segment, skipped_reason="empty_segment")

        # 2. Extract Graph (hits cache or LLM)
        try:
            ex_res = self.extractor.extract_segment(segment)
        except Exception as e:
            logger.error(f"KAM extraction failed for segment {segment.id}: {e}")
            return self._empty_report(segment, skipped_reason="extraction_failed")

        payload = ex_res.payload
        
        # 3. Resolve Spans & Build ConceptEvents
        # We must find where the LLM's extracted concepts actually live in the raw text
        concept_events = []
        for c in payload.concepts:
            # KAM schema has name and aliases
            aliases = [c.name] + c.aliases
            spans = find_spans(segment.text, aliases)
            
            if not spans:
                continue # Concept extracted but not actually in text (hallucination)
                
            f_span = first_span(spans)
            
            ev_span = EvidenceSpan(
                segment_id=segment.id,
                span_start=f_span.start,
                span_end=f_span.end,
                span_text=f_span.text,
                confidence="medium", # Tier 2 downgrade per spec
                source="post_hoc_substring_match"
            )
            
            concept_events.append(ConceptEvent(
                concept_id=c.id,
                canonical_name=c.name,
                segment_id=segment.id,
                first_span=ev_span,
                intent="mentioned" # v1 default
            ))

        # 4. Build Edge List
        prereq_edges = []
        for rel in payload.relations_graph:
            if rel.type == "prerequisite":
                prereq_edges.append((rel.from_, rel.to))

        # 5. Run Graph-Deterministic Validators
        findings = []
        
        # V-CYC (Cycle Detection)
        cycle_findings, timeout_hit = run_v_cyc(prereq_edges, segment.id)
        findings.extend(cycle_findings)
        
        # V-DENS (Concept Density)
        dens_finding = run_v_dens(concept_events, segment.text, segment.id)
        if dens_finding:
            findings.append(dens_finding)
            
        # V-FWD-shallow (Sequencing)
        fwd_findings = run_v_fwd_shallow(concept_events, prereq_edges, segment.text, segment.id)
        findings.extend(fwd_findings)

        # 6. Package Metadata
        meta = ExtractionMetadata(
            extractor_model=self.provider.model,
            extractor_prompt_version="v1.0.0", # hardcoded for v1
            extracted_at=ex_res.timestamp,
            cache_hit=ex_res.cache_hit,
            extraction_confidence_min="medium",
            cycle_validator_timeout_hit=timeout_hit,
            extraction_concept_count=len(payload.concepts),
            extraction_relation_count=len(payload.relations_graph)
        )

        return ModuleGraphReport(
            segment_id=segment.id,
            concepts=concept_events,
            prereq_edges=prereq_edges,
            findings=findings,
            extraction_metadata=meta,
            skipped_reason=None
        )

    def _empty_report(self, segment: Segment, skipped_reason: str) -> ModuleGraphReport:
        """Helper to return an empty report for skipped segments."""
        import datetime
        meta = ExtractionMetadata(
            extractor_model=self.provider.model if hasattr(self, 'provider') else "unknown",
            extractor_prompt_version="v1.0.0",
            extracted_at=datetime.datetime.now(datetime.timezone.utc),
            cache_hit=False,
            extraction_confidence_min="low",
            cycle_validator_timeout_hit=False,
            extraction_concept_count=0,
            extraction_relation_count=0
        )
        return ModuleGraphReport(
            segment_id=segment.id,
            concepts=[],
            prereq_edges=[],
            findings=[],
            extraction_metadata=meta,
            skipped_reason=skipped_reason
        )