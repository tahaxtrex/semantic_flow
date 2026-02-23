# Architecture Decision Records (ADRs)

## ADR-001: Deterministic Hybrid Segmentation Strategy

**Date:** 2026-02-22
**Status:** Accepted

**Context:**
The core research principle specifies deterministic, non-LLM segmentation to minimize costs and ensure absolute reproducibility (FR-003, FR-004). However, PDF parsing is notoriously messy with headers sometimes only acting as visual formatting.

**Options Considered:**
1. *Naive character splitting:* Guaranteed size, but splits mid-sentence and ignores logical boundaries.
2. *Strict Header splitting:* High logical accuracy, but can fail if headers are purely visual (no PDF outline) creating massive single chunks.
3. *Hybrid (Header -> Safe Chunk):* Attempt to split by structural headings. If a resulting segment exceeds a maximum length threshold, apply sentence-boundary-aware chunking.

**Decision:**
We chose Option 3 (Hybrid). We will rely on tools like `pdfplumber` to detect headings (or extract PDF outlines). If text blocks are unmanageably large, we will fall back to sentence-aware chunking.

**Consequences:**
Ensures text sections sent to LLMs perfectly respect context boundaries, preventing prompt truncation or mid-sentence logic loss.

**Linked Requirements:** FR-003, FR-004

---

## ADR-002: Active LLM Model Cascading & Hard Failure

**Date:** 2026-02-22
**Status:** Accepted

**Context:**
API rate limits, hallucinated unstructured outputs, and down-time can interrupt long batch evaluation scripts. 

**Decision:**
We will implement an active Model Cascading pattern (Claude -> Gemini). However, if both models fail on a segment (or encounter unrecoverable API errors), the system will intentionally *hard-fail*, printing the exact error and halting the pipeline, rather than recording `null` and continuing.

**Consequences:**
Prevents corrupted or partially-complete JSON files from being silently generated, ensuring data integrity at the cost of script restarts.

**Linked Requirements:** FR-006, NFR-003

---

## ADR-003: Standalone Metadata Extraction Workflow

**Date:** 2026-02-22
**Status:** Accepted

**Context:**
The roadmap explicitly decouples metadata extraction from evaluation. The CLI should natively fallback to a built-in extractor if necessary.

**Decision:**
The `Metadata Ingestor` component will blindly search the input directory for a `.json`, `.txt`, or `.html` file matching the PDF name. If found, it parses it. If not found, it invokes an internal `extract_metadata()` routine targeting the PDF text.

**Consequences:**
Maximizes flexibility, allowing precise manual metadata definition or easy automatic best-effort matching.

**Linked Requirements:** FR-001, FR-002

---

## ADR-004: Verbose JSON Output Strategy

**Date:** 2026-02-22
**Status:** Accepted

**Context:**
The format and size of the output JSON file define how easily the results can be audited. We had to choose between embedding the exact text of the segments evaluated, or just storing pointers/titles to save space.

**Decision:**
Embed the *full exact text* of every segment directly alongside its scores and LLM reasoning in the final JSON output.

**Consequences:**
Produces larger JSON files, but creates perfectly portable, self-contained artifacts that do not require the original PDF to be readable or auditable. This is critical for reproducible research datasets.

**Linked Requirements:** FR-007


**Linked Requirements:** FR-006, ADR-002

---

## ADR-005: Prioritize Accuracy Over Speed for PDF Parsing

**Date:** 2026-02-22
**Status:** Accepted

**Context:**
The system needs to parse deeply structured educational PDFs. We initially considered `PyMuPDF` for its execution speed. The user explicitly intervened to specify that the project focus is entirely on accuracy and research properties, overriding efficiency concerns.

**Options Considered:**
1. *PyMuPDF:* Very fast, lightweight, but can struggle with complex tabular or visually-nested bounding boxes compared to others.
2. *pdfplumber:* Slower, heavier on memory, but heavily optimized for absolute positional accuracy, bounding box intersection, and exact character extraction.

**Decision:**
We will exclusively use `pdfplumber` (and drop `PyMuPDF` if they conflict) to maximize the deterministic accuracy of the text extraction and header detection.

**Consequences:**
Processing a large course PDF will take longer and consume more memory locally, but the extracted segments will be noticeably higher quality, directly supporting the research goals.

**Linked Requirements:** FR-003
---

## ADR-006: Coarse Chapter-Level Segmentation Granularity

**Date:** 2026-02-22
**Status:** Accepted

**Context:**
The initial segmenter used `font_size > median + 1.5pt OR bold` as the header trigger, which caused every bold subheading, inline term emphasis, and table label to create a new segment. A ~40-page PDF produced 102 segments, making LLM evaluation impractically expensive and producing scores on incoherently short text slices.

**Options Considered:**
1. *Fine-grained (old behavior):* Every bold or slightly-larger line starts a segment. Produces many micro-segments; expensive and misaligned with pedagogical structure.
2. *Chapter-level only:* Only significantly larger text (>= 1.4x body median, < 80 chars) starts a new segment. Inline bold formatting is treated as body text.
3. *Fixed page-count windows:* Group every N pages regardless of structure. Ignores content boundaries.

**Decision:**
Option 2. Header detection now requires `max_font_size >= median * 1.4` AND `len(text) < 80`. Bold-alone formatting no longer triggers a split. Additionally, a `min_chars` merge pass combines blocks shorter than 600 chars into the following block, ensuring every emitted segment has enough content for meaningful LLM evaluation. The target granularity for a ~40-page PDF is 4–6 segments.

**Consequences:**
- Segment count drops from ~100 to ~5, reducing LLM API cost dramatically.
- Each segment contains a full logical chapter, giving the LLM sufficient context.
- Subsection-level pedagogy is evaluated holistically within a chapter, not in isolation.

**Linked Requirements:** FR-003, FR-004, ADR-001, ADR-005

---

## ADR-007: Persistent Model Fallback After Consecutive Claude Failures

**Date:** 2026-02-22
**Status:** Accepted

**Context:**
The original cascade logic tried Claude → Gemini independently for every segment. If Claude was rate-limited or unavailable, every segment would attempt Claude, fail, log an error, then fall back to Gemini — wasting time on N failed API calls for an N-segment run.

**Decision:**
`LLMEvaluator` now tracks consecutive Claude failures via `_claude_failure_count`. After `_MAX_CLAUDE_FAILURES = 2` consecutive failures, `_claude_disabled` is set to `True` for the rest of the run. All subsequent segments are routed directly to Gemini without attempting Claude. A success resets the counter. This is a run-scoped state (not persisted between invocations).

**Consequences:**
- Eliminates N−2 wasted Claude API calls when Claude is down.
- Gemini still hard-crashes on failure per ADR-002.
- Mixed-model runs (some Claude, some Gemini) may occur; callers should track `model_used` per segment if reproducibility is critical.

---

## ADR-008: Word-Level Text Reconstruction for Ligature-Encoded PDFs

**Date:** 2026-02-22
**Status:** Accepted

**Context:**
The first real evaluation run (Springer textbook PDF) produced `text_readability` scores averaging 2.5/10. Root cause analysis (critic.md Issue 1) traced this to `extract_text_lines(return_chars=True)` — pdfplumber's character-stream extraction concatenates words without spaces when fonts use ligature encoding (e.g. "OpenuptheWingIDE" instead of "Open up the Wing IDE"). This corrupted all extracted text before the LLM ever saw it.

**Options Considered:**
1. *`extract_text(use_text_flow=True)`:* Some improvement for flow, but still character-stream based and does not reliably insert word gaps for ligature fonts.
2. *Word-object reconstruction (`extract_words()` + explicit space join):* `extract_words()` uses pdfplumber's internal word-boundary detection (gap-based splitting), then joining with `' '.join()` guarantees spaces. Word objects also carry per-word `size` and `fontname` attributes needed for header detection and code-block annotation.
3. *OCR fallback:* Accurate but would require a heavy dependency (pytesseract/easyocr) and lose all font metadata.

**Decision:**
Option 2. `_extract_blocks_with_headers()` now calls `page.extract_words(extra_attrs=["size", "fontname"])` and groups words by Y-baseline via the new `_words_to_lines()` helper. Words are joined with explicit spaces.

**Consequences:**
- Word boundaries are correctly reconstructed for ligature-encoded fonts.
- Per-word `size` and `fontname` attributes are preserved for header detection and code annotation.
- Slight increase in processing complexity (Y-grouping pass), but negligible at chapter granularity.

**Linked Requirements:** FR-003, ADR-005

---

## ADR-009: Page-Count-Based Hard Segment Cap (`page_count // 10`)

**Date:** 2026-02-22
**Status:** Accepted

**Context:**
After the ADR-006 coarse-segmentation fix, the Springer PDF still produced 14 segments for a 40-page chapter. The `min_chars=600` merge pass was insufficient because many sections exceeded the threshold individually. The user's stated requirement is **≤ 1 segment per 10 pages**.

**Options Considered:**
1. *Further tighten `header_threshold` multiplier:* Raises the bar for what counts as a header, but is brittle — some PDFs may legitimately have many large-font section headers.
2. *`page_count // 10` hard cap with greedy smallest-pair merge:* Directly encodes the user's rule as an explicit constraint. Merging the shortest adjacent pair at each step minimises information loss by keeping the most similar-length segments.
3. *Fixed absolute maximum (e.g. 6 segments):* Doesn't scale to longer PDFs; arbitrary.

**Decision:**
Option 2. `segment()` now computes `max_segments = max(1, page_count // 10)` after extraction and calls the new `_merge_to_target(raw_blocks, max_segments)`. The old `_merge_short_blocks()` method is replaced entirely by this target-driven merge.

**Consequences:**
- A 40-page PDF always produces ≤ 4 segments, a 100-page PDF ≤ 10, etc.
- `_merge_short_blocks` is removed from the active code path (the `min_chars` parameter is retained on the constructor for backward compatibility but is unused).
- Merge heuristic (shortest-pair) is information-preserving under the constraint.

**Linked Requirements:** FR-003, FR-004, ADR-001, ADR-006

---

## ADR-010: Y-Coordinate Page Crop + `find_tables()` for Header/Footer Stripping and Table Detection

**Date:** 2026-02-22
**Status:** Accepted

**Context:**
The Springer PDF includes running page headers (chapter title repeated on every page) and running footers (page numbers + section name). These were extracted as body text, inflating segment content with repeated boilerplate and causing the LLM to see misleading structural repetition (critic.md Issues 3, 4). Additionally, table content was being extracted as free-form text, producing structurally garbled lines (Issue 9).

**Options Considered:**
1. *Regex filtering of repeated boilerplate lines:* Requires identifying the exact repeated string per document; brittle and font-agnostic only in appearance.
2. *Y-coordinate crop (`within_bbox` top 10% / bottom 8%):* Font-agnostic positional filter. Running headers/footers are almost universally in the top or bottom margin. Combined with `find_tables()` for structured table detection.
3. *PDF outline / bookmark-based extraction:* Not all PDFs have bookmarks; not reliable enough as primary strategy.

**Decision:**
Option 2. Each page is cropped via `page.within_bbox((0, H*0.10, W, H*0.92))` before word extraction. Tables within the body region are detected via `body.find_tables()`; words overlapping detected table bounding boxes are excluded from body text and replaced with `[TABLE: col1 | col2 | ...]` annotations. Springer chapter-number marginalia (oversized standalone digits in the margin) are stripped in `_words_to_lines()`.

**Consequences:**
- Running headers and footers are eliminated without document-specific configuration.
- Tables produce a compact structural annotation instead of garbled free-form text.
- The 10% / 8% crop boundaries may occasionally clip the first or last line of body text on unusually-formatted PDFs; this is acceptable given the significant boilerplate reduction.

**Linked Requirements:** FR-003, ADR-001, ADR-005
