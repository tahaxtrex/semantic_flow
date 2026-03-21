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

## ADR-002: Single Model Binding & Hard Failure

**Date:** 2026-02-25
**Status:** Accepted (Replaces Previous Cascading Logic)

**Context:**
Originally, the system cascaded from Claude to Gemini mid-evaluation if Claude failed. However, mixing models destroys the scientific validity of the resulting mathematical average, as a Claude 6/10 is not equivalent to a Gemini 6/10.

**Decision:**
The `LLMEvaluator` now binds explicitly to the model requested by the user via the CLI `--model` flag. There is no mid-run fallback. If the selected model experiences an unrecoverable failure or API timeout, the script hard-fails and halts the pipeline immediately.

**Consequences:**
Ensures data purity and strict validity. Prevents corrupted or mixed-model JSON files from being generated, at the cost of script restarts on API downtime.

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

## ADR-007: Persistent Model Fallback After Consecutive Failures

**Date:** 2026-02-25
**Status:** Deprecated

**Context:**
Previously, the system would permanently route to Gemini after 2 consecutive Claude failures. With the acceptance of ADR-002 (Single Model Binding) to preserve research purity, all mid-run model switching mechanics were eliminated.

**Decision:**
This ADR is deprecated. The system relies entirely on the explicit initialization model flag.

**Consequences:**
API rate limits will intentionally stall the pipeline.

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

## ADR-009: Max-Characters Segment Cap (`max_chars`)

**Date:** 2026-02-25
**Status:** Accepted (Replaces Page-Count Cap)

**Context:**
Previously, ADR-009 mandated a hard cap of `page_count // 10`. This arbitrary heuristic forced completely unrelated chapters together if the document was short, destroying pedagogical contiguity.

**Decision:**
The `_merge_to_target` logic was deleted. Segments are now greedily merged using `_merge_short_blocks` *only* if their combined length remains under the `max_chars` ceiling (default 8000 characters).

**Consequences:**
- Segments are merged naturally based on content length rather than page fractions.
- Evaluation respects the textbook's true logical flow.

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

---

## ADR-011: Evaluator Batching and System Prompt Isolation

**Date:** 2026-02-25
**Status:** Accepted

**Context:**
Evaluating sections one-by-one was re-sending the massive grading rubric prompt on every API call. This incurred massive API token costs and overwhelmed the LLM's cognitive context with repeated boilerplate, leading to lower scoring fidelity.

**Decision:**
The `LLMEvaluator` now relies on `evaluate_batch`. It securely isolates the massive rubric string into the `system_prompt` payload, and batches up to 5 instructional segments into a single `user_prompt` array string. The LLM is instructed to return a strictly validated JSON array of score objects.

**Consequences:**
API costs are reduced by ~80% per book. Cognitive load on the models is significantly lower.

---

## ADR-012: Bypassing Non-Instructional Content

**Date:** 2026-02-25
**Status:** Accepted

**Context:**
Table of Contents, Prefaces, and Exercises were being evaluated on rubrics like "Instructional Flow" and "Prerequisite Alignment." These sections naturally scored `1/10` or `2/10`, artificially crushing the textbook's overall average score.

**Decision:**
`SmartSegmenter` now explicitly tags recognized exercises, solutions, and frontmatter (Preface, TOC, History) with distinct `segment_type` strings. `LLMEvaluator` bypasses any segment that is not `"instructional"`, generating a 0-score null result without hitting the API. The `ScoreAggregator` excludes these 0-score segments from the final weighted average.

**Consequences:**
Final course score remains pure to actual instructional pedagogy.

---

## ADR-013: Strict Pydantic Schema Validation

**Date:** 2026-02-25
**Status:** Accepted

**Context:**
The internal `SectionScores` and `SectionReasoning` schemas had trailing default `= 0` and `= ""` values. If the API returned truncated JSON, Pydantic silently filled in `0` scores without throwing an error, accepting garbage data.

**Decision:**
All defaults were removed from the Pydantic schemas. If the LLM omits a required reasoning string or score integer, Pydantic immediately throws a `ValidationError`.

**Consequences:**
Invalid outputs are reliably caught and piped back into the evaluator's exponential backoff retry loop.

---

## ADR-014: Explicit Model Routing & Approach

**Date:** 2026-02-25
**Status:** Accepted

**Context:**
Following ADR-002, the app needs to be run explicitly. Users may have different local key permissions or prefer different models based on context window limits.

**Decision:**
The `--model` flag dictates behavior at the initializer level. If `claude` (default) is requested, `Anthropic` SDK is initialized; if `gemini` is requested, `google.genai` SDK is used. If the required API key for the requested model is not found in `.env`, the pipeline crashes immediately on boot. Both use the same systemic prompting mechanisms, just wired through different SDKs.

**Consequences:**
Complete transparency in model invocation. No silent key overrides.

---

## ADR-015: Disconnected Metadata Review Workflow

**Date:** 2026-02-25
**Status:** Accepted

**Context:**
Previously, metadata extraction was tightly coupled to the evaluation run. The system would auto-scan the PDF's directory for sibling files, or silently infer metadata from the PDF itself and immediately feed it into the LLM. There was no opportunity to review, correct, or enrich the metadata before it influenced the evaluation scores.

**Decision:**
`metadata.py` is now independently executable via `python3 -m src.metadata --pdf <path> --output <path.json>`. This produces a human-reviewable JSON file. Users can edit incorrect fields (title, prerequisites, audience) and then pass the corrected file into the evaluator with `--metadata <path.json>`. The original auto-scan behavior is fully preserved when no `--metadata` flag is supplied.

**Consequences:**
- Enables a safer "extract → review → evaluate" workflow.
- Prevents LLM hallucinations caused by incorrect inferred metadata.
- Does not interfere with the original auto-scan pipeline.

**Linked Requirements:** FR-001, FR-002, FR-009

---

## ADR-016: Two-Gate Assessment Architecture (Course vs. Module)

**Date:** 2026-03-02
**Status:** Accepted

**Context:**
The evaluation criteria previously applied all 10 rubrics uniformly to every instructional segment. However, in the context of educational structures, rubrics logically apply to different domains: "Modules" (the actual content people read) vs "Courses" (how the modules are structured, related, and overall metadata alignment). The user directed a restructure into two distinct gates to reflect this reality.

**Decision:**
The pedagogical evaluation will be divided into two gates:
1. **Module Gate**: Evaluates the atomic content chapters for quality (Goal Focus, Text Readability, Pedagogical Clarity, Example Concreteness, Example Coherence, Instructional Alignment).
2. **Course Gate**: Evaluates the holistic structure and alignment of the entire program (Prerequisite Alignment, Structural Usability, Business Relevance, Fluidity & Continuity).

**Consequences:**
- The codebase will need distinct pathways and Pydantic schemas for Module-level and Course-level evaluations.
- The `rubrics.yaml` will be structurally divided.
- LLM prompts will become more targeted, reducing cognitive load by removing irrelevant rubrics from chunk-level evaluations.

**Linked Requirements:** (Pending Phase 1 requirement generation for Two-Gate Architecture)

---

## ADR-017: Capstone Course Gate Execution

**Date:** 2026-03-02
**Status:** Accepted

**Context:**
With the split of course vs. module assessment (ADR-016), we must decide how the LLM accesses the entire textbook to grade the Course Gate rubrics without blowing past token limits or incurring massive API costs.

**Decision:**
1. The **Module Gate** executes first, traversing the textbook segment-by-segment in batches exactly as it does now, but scoring only the 6 Module rubrics.
2. During Module execution, we ask the LLM to provide a 1-2 sentence "content summary" of each segment.
3. Once all modules are evaluated, the **Course Gate** executes exactly once as a capstone call.
4. The Course Gate is provided with: Course Metadata, the concatenated list of segment summaries (forming a sequential narrative of the book's flow), and the bypassed Non-Instructional segments (TOC, preface).
5. The Course Gate returns a single set of 4 scores and rationales for the entire textbook.

**Consequences:**
- Very low token cost for the Course evaluation (only summaries and TOC are sent).
- Perfectly isolates the holistic rubrics from the atomic ones in `models.py`.
- Generates two completely distinct overall scores (`course_average_score` and `module_average_score`) to avoid polluting data.

---

## ADR-018: Tesseract OCR Fallback for Scanned PDFs

**Date:** 2026-03-03
**Status:** Accepted

**Context:**
Real-world course PDFs (e.g., scanned OpenStax textbooks) embed pages as images rather than native text. `pdfplumber` returns 0 words for such pages, causing the segmenter to silently produce a single unusable block. The pipeline needs to handle mixed-mode PDFs (some pages native, some scanned) without requiring the user to pre-process the file.

**Options Considered:**
1. *Reject scanned PDFs with a clear error:* Simple, but blocks valid research inputs.
2. *Pre-processing step (ocrmypdf):* Clean, but requires users to run a separate command and store an intermediate file.
3. *Per-page silent fallback to Tesseract:* Transparent, no extra steps, graceful degradation when tesseract is absent.

**Decision:**
Option 3. For each page, after `pdfplumber` extraction, if the word list is empty, `_ocr_page()` renders the page at 300 DPI via `pdf2image` (Poppler-backed) and runs `pytesseract.image_to_string()`. The result is appended to the current text block. OCR is a one-time lazy check: if `tesseract` binary is absent, a DEBUG message is logged and the page is skipped without crashing.

**Consequences:**
- System dependency: `tesseract-ocr` + `poppler-utils` must be installed (`sudo apt install tesseract-ocr poppler-utils`).
- Python dependencies: `pytesseract>=0.3.10`, `pdf2image>=1.17.0` added to `requirements.txt`.
- OCR text has no font metadata, so header detection does not apply; content is appended as body text within the current block.
- Quality of OCR output depends on scan quality; clean 300 DPI scans are expected to produce usable evaluation input.

**Linked Requirements:** FR-003, ADR-001

---

## ADR-019: Skip Course Gate When No Instructional Segments Exist

**Date:** 2026-03-03
**Status:** Accepted

**Context:**
When a PDF contains only frontmatter/copyright pages (or when all pages are unreadable scanned images with tesseract unavailable), the Module Gate produces zero instructional segments and zero content summaries. Previously, the Course Gate capstone call was still executed, passing only course metadata (title, description). Gemini would infer scores from the publisher name ("OpenStax = high quality") and produce misleadingly high holistic scores (e.g. 8.25/10).

**Decision:**
In `main.py`, before executing the Course Gate, check `has_instructional = any(s.segment_type == "instructional" for s in segments)`. If `False`, skip the LLM capstone call entirely, log a `WARNING`, and populate the `CourseAssessment` with `_make_incomplete_course_assessment()` (all zeros). This makes the output honest: if we cannot assess module content, we cannot assess course holism.

**Consequences:**
- Saves one Gemini API call per content-empty PDF.
- Output is truthful: `course_gate.overall_score = 0.0` clearly signals no assessment was possible.
- Human reviewers can see from `evaluation_meta.excluded_segments` and `instructional_segments_scored: 0` that the PDF was unreadable.

---

## ADR-020: Enforce Gemini Response Schema via `response_schema`

**Date:** 2026-03-03
**Status:** Accepted

**Context:**
Gemini's `application/json` response mode does not guarantee any specific JSON structure by default. Observed failure modes include: returning `{"evaluations": [...]}` instead of `[...]`, returning `{"rubric_scores": [{id, score, rationale}]}` instead of `{"scores": {...}, "reasoning": {...}}`, and omitting rationale fields entirely. While `_unwrap_gemini_list` / `_unwrap_gemini_object` provide post-hoc normalization, preventing malformed output upstream is preferable.

**Decision:**
Pass the existing Claude tool `input_schema` dict as `response_schema` in `types.GenerateContentConfig` for both Module Gate and Course Gate Gemini calls. This instructs the Gemini API to constrain its output to match the schema before returning. The `_unwrap_*` helpers are retained as a defense-in-depth layer.

**Consequences:**
- Fewer JSON parse/unwrap failures in practice.
- Both Claude (tool-use) and Gemini (response_schema) now share the same schema definition — single source of truth.
- If Gemini rejects a schema (e.g. unsupported field type), the error will surface as a structured API error rather than a silent malformed response.

---

## ADR-021: Chain-of-Thought Scoring Procedure in Module Gate Prompt

**Date:** 2026-03-06
**Status:** Accepted

**Context:**
CRITIC_REPORT Issue #5 identified that the Module Gate's zero-shot prompting approach (asking the LLM to simultaneously evaluate all 6 rubrics) caused regression-to-the-mean (scores clustering around 5–6) and hallucinated rationales.

**Options Considered:**
1. *Split into two separate API calls (structural vs content rubrics):* Eliminates cognitive overload entirely but doubles the API call count per batch, significantly increasing cost and latency. Conflicts with ADR-011 (batching philosophy).
2. *Chain-of-Thought (CoT) SCORING PROCEDURE injection:* Add a step-by-step reasoning instruction to the existing system prompt that guides the LLM to first identify specific textual evidence and reason about the midpoint threshold before committing a score. Zero additional API cost.

**Decision:**
Option 2. A `SCORING PROCEDURE` block was added to `_build_module_batch_prompts()` in `evaluator.py`. For each rubric, the LLM is instructed to: (1) identify specific textual evidence relevant to the rubric, (2) reason whether the score should be above or below the midpoint (5), then (3) finalise the score and rationale. The output schema, API call structure, and batch size are unchanged.

**Consequences:**
- Scores are expected to be more discriminating and less clustered around the midpoint.
- Rationales are expected to reference specific evidence from the segment text.
- No increase in API cost or latency.

**Linked Requirements:** FR-005, ADR-011


---

## ADR-022: Focused AI Extraction for Learning Outcomes & Prerequisites

**Date:** 2026-03-06
**Status:** Accepted

**Context:**
The regex-based extractor in `metadata.py` reliably finds fields like title and author but frequently misses `learning_outcomes` and `prerequisites`. These fields are critical for the Course Gate's `prerequisite_alignment` rubric yet are named inconsistently in PDFs ("Objectives", "Goals", "Must Knows", "What You Will Learn", etc.). This gap directly degrades evaluation accuracy (FR-001, Q-001).

**Options Considered:**
1. *Expand regex patterns:* Cannot enumerate all possible heading synonyms exhaustively across publishers.
2. *Require structured metadata file:* Shifts burden to user; contradicts the goal of fully automated extraction.
3. *Targeted AI pass always active for list fields:* Minimal prompt (~300 tokens) that enumerates all heading synonyms. Runs only when fields are empty after regex pass.

**Decision:**
Option 3. Add `_ai_extract_list_fields()` to `MetadataIngestor`, triggered automatically after the regex pass whenever `learning_outcomes` or `prerequisites` remain empty. Uses `_LIST_FIELDS_SYSTEM_PROMPT` which explicitly lists every known heading synonym. Reuses the existing Claude→Gemini fallback hierarchy. Silent skip if no API keys configured.

**Consequences:**
- One additional small API call per PDF (only when needed). Cost: ~500 tokens.
- Fields are now reliably extracted regardless of heading style used by the publisher.
- Does not overwrite fields already populated by the regex pass.

**Linked Requirements:** FR-001, Q-001, Q-013

---

## ADR-023: TOC-Based Segmentation (Three-Tier Hierarchy)

**Date:** 2026-03-06
**Status:** Accepted
**Amends:** ADR-001 (adds a new first tier above the hybrid strategy)

**Context:**
The existing font-heuristic segmenter (`_extract_blocks_with_headers`) treats every font-size jump as a potential chapter boundary. This creates two problems: (1) sub-headings and callout boxes generate spurious new segments, splitting chapters mid-content; (2) chapters can be merged or cut at wrong boundaries. Most well-structured PDFs contain an embedded bookmark outline (TOC) that encodes the exact chapter structure with page numbers. This is the authoritative source.

**Options Considered:**
1. *Improve font-heuristic thresholds:* Fragile; publisher-specific tuning required.
2. *Use only TOC:* Falls back to nothing on PDFs without outlines (scanned, older textbooks).
3. *TOC-first with font-heuristic fallback:* Accurate for well-structured PDFs; fully backwards-compatible.

**Decision:**
Option 3. `segment()` now calls `_extract_toc()` first. If the PDF outline has ≥2 entries, page ranges between consecutive TOC entries are used to extract text for each chapter segment. Falls back to `_extract_blocks_with_headers()` if no usable outline is found. Both paths feed the same `_merge_short_blocks` → `_chunk_text` → `_classify_segment` pipeline. `_flatten_outline()` handles nested/recursive PDF outlines and normalizes 0-based/1-based page numbering. The TOC path applies the same body crop, CID replacement, table annotation, figure annotation, OCR fallback, and code-block markers as the heuristic path.

**Consequences:**
- Chapters are now accurately delimited for PDFs with embedded outlines.
- Text is more readable: each segment is a complete, self-contained chapter.
- No regression for PDFs without outlines (fallback is unchanged).
- `_extract_toc()` is a pure read-only method that does not alter any state.

**Linked Requirements:** FR-003, FR-004, Q-002

---

## ADR-024: Tree-Structured Assessment Output

**Date:** 2026-03-06
**Status:** Accepted

**Context:**
The current `module_gate: Dict[str, Any]` flat dict in `CourseEvaluation` does not express the hierarchy of scores. Both gate results (module and course) needed a structured, human-readable format that clearly shows overall gate scores alongside per-rubric breakdowns, including the rationale for each rubric. The previous response to Q-017 specified independent overall scores per gate; this ADR operationalises the shape of those scores in the output JSON.

**Options Considered:**
1. *Flat dict (current):* No hierarchy; rationales not included.
2. *Replace flat dict with tree:* Breaking change. Downstream consumers that parse `module_gate.goal_focus` directly would need updating.
3. *Add tree alongside flat dict:* Backwards compatible. Old field `module_gate` retained; new field `assessment` added.

**Decision:**
Option 3. Added three new Pydantic models to `models.py`:
- `RubricResult(score: float, rationale: str)` — leaf node.
- `GateReport(overall_score: float, rubrics: Dict[str, RubricResult])` — one gate.
- `AssessmentTree(module_gate: GateReport, course_gate: GateReport)` — root.

`CourseEvaluation` gains `assessment: AssessmentTree` as its primary structured output. The flat `module_gate: Dict[str, Any]` is retained for backwards compatibility.

`ScoreAggregator._build_assessment_tree()` populates the tree. Module gate rubric rationales use the longest single rationale from any scored segment for each dimension (surfaces most substantiated justification). Course gate rubric scores and rationales come directly from `CourseAssessment`.

**Consequences:**
- Output JSON now has a clear tree structure for easy human reading and downstream parsing.
- The `assessment` field is the canonical output; `module_gate` is a backwards-compat alias.
- Three new Pydantic models; no changes to evaluation logic.

**Linked Requirements:** Q-017, Q-010

---

## ADR-025: Running Page Header Rejection in Segment Classifier

**Date:** 2026-03-13
**Status:** Accepted
**Addresses:** critic.v2.md Issue 1 (CRITICAL)

**Context:**
The OpenStax running header `"Access. The future of education."` appears at the top of many pages in split PDFs. Because it is rendered in a large or bold font, the font-heuristic path occasionally picks it up as a chapter heading. When `_classify_segment()` then receives this string as the heading, the frontmatter regex matches and classifies the resulting segment as `frontmatter`, zeroing out its scores silently. This was confirmed to affect at least 3 segments in `firstpart.pdf`.

**Decision:**
Added `_KNOWN_RUNNING_HEADERS` frozenset to `segmenter.py`. At the start of `_classify_segment()`, if `heading.lower().strip()` is found in this frozenset, heading is set to `None` before any pattern matching runs. The set currently covers OpenStax variants; extending it requires only adding a string.

**Consequences:**
- Eliminates false frontmatter classification for known publisher running headers.
- The segment is evaluated normally as `instructional` because the text contains genuine educational content.
- 9 new unit tests exercise this behavior.

**Linked Requirements:** critic.v2.md Issue 1

---

## ADR-026: Glossary, Summary, and Assessment Segment Types

**Date:** 2026-03-13
**Status:** Accepted
**Amends:** ADR-012 (extends the non-instructional bypass list)
**Addresses:** critic.v2.md Issue 2 (HIGH)

**Context:**
End-of-chapter "Key Terms" glossary sections, "Summary" bullet lists, and "Check Your Understanding" question banks were being evaluated as instructional content. This skews scores: glossaries artificially lower `example_concreteness`; question banks inflate `goal_focus` and (now Course Gate) `instructional_alignment`. All three should be bypassed identically to `exercise`, `solution`, and `frontmatter`.

**Decision:**
Added three detection mechanisms to `_classify_segment()` in `segmenter.py`:
- **`glossary`**: heading matches `^(key\s+terms?|glossary)$`.
- **`summary`**: heading matches `^(summary|chapter summary|module summary|section summary)$`.
- **`assessment`**: body has ≥3 lettered option lines (`a. …`) AND ≥2 numbered question lines. No heading required.

All three types pass through `evaluate_batch()` with zero scores and no summary.

**Consequences:**
- Glossaries, summaries, and assessment banks no longer distort Module Gate averages.
- Zero added API cost: these segments are bypassed without an LLM call.

**Linked Requirements:** critic.v2.md Issue 2, ADR-012

---

## ADR-027: Partial-Course File Detection in Course Gate

**Date:** 2026-03-13
**Status:** Accepted
**Addresses:** critic.v2.md Issue 3 (HIGH)

**Context:**
Large textbooks are often split into multiple PDF files. The Course Gate evaluates each file independently, but rubrics like `fluidity_continuity` and `structural_usability` penalise for missing introductory material, no Table of Contents, and module gaps — all of which exist in sibling files.

**Decision:**
Added `_detect_partial_course()` to `LLMEvaluator`. Returns `True` when:
1. No non-instructional segment contains TOC/preface signals, AND
2. No instructional segment heading begins with a first-chapter pattern.

When `True`, a "PARTIAL COURSE FILE" disclaimer is prepended to the Course Gate system prompt, telling the LLM not to penalise for absent modules or missing introductory material. `evaluate_course()` now returns `(CourseAssessment, is_partial_course: bool)`.

**Consequences:**
- Partial files receive fair scores on continuity rubrics.
- The heuristic is conservative; complete single-file courses are not flagged.
- No additional API cost.

**Linked Requirements:** critic.v2.md Issue 3

---

## ADR-028: Instructional Alignment Moved to Course Gate (ADR-016 Correction)

**Date:** 2026-03-13
**Status:** Accepted
**Amends:** ADR-016
**Addresses:** critic.v2.md Issue 4 (MEDIUM)

**Context:**
`instructional_alignment` was listed in the Module Gate in ADR-016, but its definition ("are learning content, activities, and stated outcomes aligned across the course") is inherently a cross-module, holistic property. You cannot assess whether all modules collectively deliver on the course's learning objectives by reading a single segment.

**Decision:**
Removed `instructional_alignment` from `ModuleScores`, `ModuleReasoning`, `_MODULE_SCORE_FIELDS`, and the Module Gate tool schema. Added it to `CourseScores`, `CourseReasoning`, `_COURSE_SCORE_FIELDS`, and the Course Gate tool schema. The `rubrics.yaml` definition is relocated from `module_rubrics` to `course_rubrics` with an updated cross-module framing.

The Module Gate now scores 5 rubrics; the Course Gate scores 5 rubrics.

**Consequences:**
- Module Gate no longer scores a rubric requiring whole-course visibility.
- Course Gate overall_score is now the mean of 5 rubrics (previously 4).
- All Pydantic schemas, field lists, tool schemas, and rubrics.yaml are consistent.

**Linked Requirements:** critic.v2.md Issue 4, ADR-016, Q-016
