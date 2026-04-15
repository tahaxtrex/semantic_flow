# Architecture Decision Records (ADRs)

## ADR-041: Top-Level-Only TOC Extraction

**Date:** 2026-04-15
**Status:** Accepted (Amends ADR-038)

**Context:**
`_extract_toc_heuristic()` was matching all lines in the TOC page with dotted leaders, including sub-section entries (`1.1 Compiled and interpreted languages … 1`, `1.2 Static types … 1`). For a book like Think OS (11 chapters, ~5 sub-sections each), this produced 55 entries in `toc_candidates`. The LLM metadata pipeline then returned those 55 entries as the TOC, which (a) corrupted the chapter-count reference used by Tier 1/2 validation and (b) would cause Tier 0 to attempt 55-chapter segmentation.

**Decision:**
Two-point fix, both mandatory:
1. `_extract_toc_heuristic()` — skip any parsed entry whose `chapter_number` field contains a dot. Only plain integers (`1`, `11`), `Chapter N` strings, and roman numerals remain.
2. `_METADATA_SYSTEM_PROMPT` rule 5 — explicitly instruct the LLM to include only top-level chapters and, if the TOC page shows only sub-sections, to infer chapter titles from the chapter headings found in the raw 15-page text body.

**Consequences:**
- The heuristic TOC is now empty for books whose printed TOC lists only sub-sections (the LLM becomes the sole chapter source, which is acceptable since it receives the raw text).
- Returning zero entries is strictly better than returning 55 wrong entries; downstream code handles an empty `toc_candidates` gracefully.
- Universal: the rule applies to all PDFs, not just Think OS.

**Linked Requirements:** ADR-038, TASK-062

---

## ADR-042: Tier 0 — Metadata-TOC-Driven Segmentation

**Date:** 2026-04-15
**Status:** Accepted (Amends ADR-039)

**Context:**
The metadata TOC was being used only as a validation reference (chapter count ±2) to decide whether Tier 1/2 results were trustworthy. The actual page boundaries were computed by the PDF bookmark outline, UNIT markers, or font heuristics — none of which are guaranteed to be present or accurate. When the metadata pipeline already extracted the correct chapter titles, there was no reason not to use them as primary segmentation drivers.

**Decision:**
Add Tier 0 to `SmartSegmenter.segment()`, evaluated before Tier 1. Implementation:
- `_extract_toc_metadata()`: iterates `course_metadata.toc` entries in order and calls `_find_chapter_start_page()` for each.
- `_detect_contents_pages()`: identifies TOC/Contents pages (dotted-leader lines + "Contents" heading) in the first 20 physical pages and marks them as skip pages.
- `_find_chapter_start_page()`: requires two conditions simultaneously — (1) at least 2 of the first 3 significant title words appear in the first 400 chars of the page, AND (2) the page opens with `chapter N …` or `N <word> …` (no leading page number). Condition 2 filters out running-header pages (`"74 Chapter 10. Condition variables 10.2…"`) which would otherwise be false positives.
- Chapter pages are found sequentially; each subsequent chapter must start after the previous. Falls through if fewer than 2 chapters are resolved.
- Output: `Frontmatter` block (pages before Chapter 1), one `Chapter N: Title` block per chapter, trailing pages (references, appendices) absorbed into the last chapter block and classified by segment type downstream.

**Consequences:**
- For PDFs where the metadata TOC is accurate and chapter headings are findable in the text, segmentation is structurally correct without needing bookmarks.
- Does not rely on `page_number` values from the metadata TOC, which may be wrong (LLM extraction errors, roman-numeral front-matter offsets). Title+heading-pattern matching is more robust than page-number arithmetic.
- If Tier 0 fails (metadata absent, or fewer than 2 chapters found), the existing Tier 1–3 cascade is unchanged.

**Linked Requirements:** ADR-039, TASK-063

---

## ADR-043: Criteria-Based Scoring (5 × 0/1/2 per Rubric)

**Date:** 2026-04-15
**Status:** Accepted (Amends ADR-036)

**Context:**
Each rubric was scored as a single holistic integer (1–10). This made it impossible to identify which specific quality dimension caused a low score (e.g. "example present but uses placeholder data" vs "no example at all"). Rubric scores were also hard to calibrate consistently across LLM calls because the anchoring was purely band-based.

**Decision:**
Each rubric now has exactly 5 criteria (listed in `config/criterias.md` and `config/rubrics.yaml`). The LLM scores each criterion 0 (not present), 1 (partially present), or 2 (fully present). The rubric total = sum of the 5 criteria scores (range 0–10, replacing the old 1–10 scale).

Implementation:
- `config/rubrics.yaml`: `criteria:` list added to each rubric.
- `_format_rubrics_for_prompt()`: renders criteria as `C1: … | → Total = C1+C2+C3+C4+C5`.
- Module-gate scoring procedure: replaces the 3-step calibration with criteria-based instructions.
- `_MODULE_EVAL_TOOL` + `_COURSE_EVAL_TOOL`: `criteria_scores` added as a required field (`{c1..c5}` per rubric); `scores` range updated to `0-10`.
- `EvaluatedSegment.criteria_scores` and `CourseAssessment.criteria_scores`: new `Dict[str, Any]` fields, serialised automatically in the output JSON.
- `_CRITERION_SCHEMA` defined at module level (Python 3 comprehensions inside class bodies cannot access other class attributes).

**Consequences:**
- Output JSON now shows per-criterion breakdowns alongside totals — callers can identify the exact failure mode without reading the rationale text.
- The 0–10 range is preserved, so weights, aggregation, and downstream scoring logic are unchanged.
- The 3-step calibration anchors are retained in the prompt as cross-check references; they no longer drive the primary scoring path.
- Descriptions, weights, scoring guides, and evaluation questions in `rubrics.yaml` are unchanged.

**Linked Requirements:** ADR-036, TASK-064, TASK-065

---

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
**Status:** Superseded by ADR-037 (2026-04-10)

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
**Status:** Accepted (Amended by ADR-040, 2026-04-10 — preface promoted to distinct type)

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
**Status:** Superseded by ADR-038 (2026-04-10)

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
**Status:** Accepted (Amended by ADR-039, 2026-04-10 — tier counts validated against metadata TOC)
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
**Status:** Accepted (Amended by ADR-040, 2026-04-10 — prose-density gate on reference_table)
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

---

## ADR-029: Visual TOC Parser & Bold-Frequency Header Filter

**Date:** 2026-03-24
**Status:** Accepted
**Amends:** ADR-023 (adds a second tier between bookmark TOC and font-heuristic fallback)
**Addresses:** critic.v3.md Issues 1, 2, 12, 14

**Context:**
The MRCET Python Programming Notes PDF has no bookmark outline, so `_extract_toc()` returns `([], 142)`. However, pages 4-5 contain a clearly structured visual Table of Contents (`UNIT I ... 1`, `UNIT II ... 35`, etc.) that would give perfect 5-unit segmentation. Instead, the pipeline falls back to the font-heuristic path, where the `or is_bold` clause in `_extract_blocks_with_headers()` triggers on 497 bold lines (e.g. `"Output:"` appears 107 times), producing 19 mis-segmented fragments. Additionally, `_merge_short_blocks()` has no unit boundary awareness and can merge across UNIT boundaries.

**Decision:**
1. **Visual TOC parser:** Add `_extract_visual_toc()` as a second tier in the extraction hierarchy. Scan pages 2-8 for lines matching `r'(UNIT|Chapter|Module|Part)\s+[\dIVXivx]+.*?(\d{1,4})\s*$'`. If ≥2 entries with monotonically increasing page numbers are found, use them as segment boundaries.
2. **Bold-frequency filter:** In `_extract_blocks_with_headers()`, after collecting all bold lines, compute a frequency count. Bold text appearing on >5% of pages is a repeated label (not a heading) — exclude from header candidacy. Bold-only headers must additionally: be fully bold, NOT end with `:`, and not be a common label word (`"Example"`, `"Syntax"`, `"Note"`, `"Output"`).
3. **`bold_as_header` toggle:** Add `bold_as_header: bool = True` to `SmartSegmenter.__init__()` for PDFs that misuse bold.
4. **Content-based running header filter (Issue 14):** After extracting all lines, strip any text that appears on >30% of pages at a similar Y-position (within 5pt).
5. **Unit boundary merge barriers (Issue 12):** In `_merge_short_blocks()`, detect `UNIT\s+[–-]?\s*[IVX\d]+` patterns in block text/headings and treat them as absolute merge barriers.

**Consequences:**
- PDFs with visual TOCs produce accurate unit-aligned segments.
- Bold-heavy PDFs no longer produce catastrophic over-segmentation.
- Running headers within the body zone are stripped content-wise, not just position-wise.
- Unit boundaries are preserved during merge.

**Linked Requirements:** FR-003, FR-004, ADR-023, critic.v3.md Issues 1, 2, 12, 14

---

## ADR-030: Cross-Segment Awareness & Three-Step Calibration in Module Gate

**Date:** 2026-03-24
**Status:** Accepted
**Amends:** ADR-021 (replaces binary above/below-midpoint anchor with three-step calibration)
**Addresses:** critic.v3.md Issues 8, 9, 13

**Context:**
The Module Gate evaluates each segment in isolation. Repetition across segments (e.g., segments 9 and 10 repeating function composition/recursion), recurring trivial variables (`a=5`, `"mrcet college"`), and non-progressive examples are invisible. Additionally, the binary scoring anchor ("is evidence above or below 5?") pushes 68% of scores to ≥7, causing systematic +1.65 inflation. The Course Gate receives only topic summaries, not quality signals, leading to business_relevance scoring +5 higher than manual assessment.

**Decision:**
1. **Cross-segment context (Issue 8):** For each segment after the first in a batch, inject "Previous segment covered: {prev_summary}" into the Module Gate prompt. For each batch after the first, include a brief "Course narrative so far" comprising previous segment summaries (truncated to ~500 chars).
2. **Three-step calibration (Issue 13):** Replace the binary "above/below 5" scoring procedure with: (1) IDENTIFY 2-3 specific evidence pieces, (2) ANCHOR to the band (1-3 poor, 4-6 adequate, 7-8 good, 9-10 excellent), (3) DIFFERENTIATE within band. Add explicit calibration anchors preventing inflation (e.g. trivial variables ≤6 on example_concreteness).
3. **Quality signals in Course Gate (Issue 9):** For each segment summary sent to the Course Gate, append Module Gate scores. Add a "MODULE GATE QUALITY SUMMARY" section with average scores, lowest-scoring segment, and detected repetition warnings.

**Consequences:**
- Repetition and non-progressive examples are detectable across segments.
- Scores spread across the full 1-10 range instead of clustering at 7-9.
- Course Gate has quality signals, not just topic summaries.
- Minor token increase per batch (~200-400 tokens for context summaries).

**Linked Requirements:** FR-005, ADR-011, ADR-021, critic.v3.md Issues 8, 9, 13

---

## ADR-031: Explicit Academic Audience Detection in Metadata

**Date:** 2026-03-24
**Status:** Accepted
**Addresses:** critic.v3.md Issue 5

**Context:**
The MRCET PDF explicitly states "B.TECH III YEAR – II SEM" and "III Year B. Tech CSE -II SEM", but the metadata pipeline detected "introduction" in the text (from "Introduction to Python"), mapped it to `level: "Introductory"`, then used the generic `level_audience_map` to produce `"Introductory college students or beginners with no prior background"`. This is flatly wrong — the students are third-year CS engineering students.

**Decision:**
1. Before inferring level from generic keywords, scan for explicit academic year/semester markers: `r'(\d+(?:st|nd|rd|th)\s+year|[IVX]+\s+year|year\s+[IVX\d]+|B\.?Tech|M\.?Tech|semester\s+[IVX\d]+)'`. If found, extract the year/semester and use it as the audience directly.
2. The level inference should not trigger on "introduction" when it appears as a topic name (`"Introduction to Python"`) rather than a course-level descriptor. Check context: if "introduction" is followed by "to [Subject]", it describes the subject, not the course level.
3. Add explicit academic audience regex patterns for university lecture notes.

**Consequences:**
- PDFs with explicit year/semester markers get correct target audience.
- "Introduction to X" no longer triggers false Introductory level inference.
- All prompts use correct audience context, preventing score inflation.

**Linked Requirements:** FR-001, FR-002, critic.v3.md Issue 5

---

## ADR-032: Rubric Description Sharpening (Business Relevance & Example Concreteness)

**Date:** 2026-03-24
**Status:** Accepted
**Addresses:** critic.v3.md Issues 6, 7, 11

**Context:**
`business_relevance` scored 8 (pipeline) vs 3 (manual) because the rubric description lets the LLM pattern-match topic coverage against learning outcomes. `example_concreteness` averaged 8.15 vs 6 because the LLM interprets "concrete" as "actual code that runs" rather than "code that solves a real-world problem." Additionally, `instructional_alignment` has an invalid `1.0j` weight (Python complex literal; YAML reads as string).

**Decision:**
1. Rewrite `business_relevance` description to explicitly require real-world professional tasks, projects, case studies, or industry scenarios. Add scoring anchors that punish purely syntactic content.
2. Sharpen `example_concreteness` scoring guide: mid-band explicitly calls out trivial variables (`a=5, x=[1,2,3]`); high-band requires realistic scenarios.
3. Fix `instructional_alignment` weight from `1.0j` to `1.0`.

**Consequences:**
- LLM can no longer conflate topic coverage with applied relevance.
- Trivial code examples are correctly anchored at mid-range, not high.
- Weight parsing no longer risks crash from complex number literal.

**Linked Requirements:** NFR-002, critic.v3.md Issues 6, 7, 11

---

## ADR-033: Single CourseMetadata Source of Truth

**Date:** 2026-03-24
**Status:** Accepted
**Addresses:** critic.v3.md Issue 10

**Context:**
`src/models.py` defines `CourseMetadata` with `title: Optional[str] = None`, while `src/metadata.py` defines its own `CourseMetadata` with `title: str = "Unknown"`. `MetadataIngestor` returns `metadata.py`'s version; the evaluator imports `models.py`'s version. The semantic mismatch means field-presence checks behave differently depending on which class produced the object.

**Decision:**
Delete `CourseMetadata` from `src/models.py`. All imports should use `src/metadata.py`'s `CourseMetadata` as the single source of truth. The `"Unknown"` sentinel convention is preferred because it avoids `None`-handling in f-strings.

**Consequences:**
- Single schema definition; no more semantic mismatch.
- `models.py` imports `CourseMetadata` from `metadata.py` and re-exports it.
- Existing imports via `from src.models import CourseMetadata` continue to work.

**Linked Requirements:** critic.v3.md Issue 10

---

## ADR-034: Exercise Classifier Overhaul & Frontmatter Boilerplate Detection

**Date:** 2026-03-24
**Status:** Accepted
**Addresses:** critic.v3.md Issues 3, 4

**Context:**
Segment 5 of the MRCET PDF (`"1. Built-in functions - Functions that are built into Python."`) starts with `"1."`, matching the exercise pattern `r'^\d+[\.)]'`. The heading match alone classifies the entire segment (6 pages of instructional content) as `exercise`, silently dropping it. Separately, institutional boilerplate (accreditation text, syllabus listing) is not detected as frontmatter because the heading `"(Autonomous Institution – UGC, Govt. of India)"` matches no frontmatter pattern.

**Decision:**
1. **Exercise classifier fix (Issue 3):** Require exercise-pattern headings to ALSO contain exercise-specific keywords: `r'^\d+[\.)\s]\s*(exercise|practice|problem|question|write\s+a\s+program)'`. Headings like `"1. Built-in functions"` no longer trigger. Body-text exercise patterns exclude matches inside `[CODE]...[/CODE]` blocks. Numbered explanatory lists are distinguished from numbered problems.
2. **Frontmatter boilerplate detection (Issue 4):** Add patterns to `_FRONTMATTER_PATTERNS` for institutional markers: `UGC|AICTE|JNTUH|affiliated to|accredited by|autonomous institution`, `syllabus|course code|credit hours`. Add a content-based heuristic: if a segment contains both UNIT markers and textbook references, classify as frontmatter. Check the first 500 characters for institutional markers.

**Consequences:**
- Numbered instructional headings no longer trigger false exercise classification.
- Institutional boilerplate and syllabus pages are correctly classified as frontmatter.
- 6+ pages of instructional content are no longer silently dropped.

**Linked Requirements:** ADR-012, critic.v3.md Issues 3, 4

---

## ADR-035: UNIT Marker Scan Replaces Visual TOC Tier

**Date:** 2026-03-25
**Status:** Accepted
**Amends:** ADR-029 (Visual TOC Parser)

**Context:**
ADR-029 introduced a visual TOC parser (Tier 2) that scanned pages 2–8 for printed table-of-contents lines matching `(UNIT|Chapter|Module|Part) <number> ... <page_num>`. This failed on MRCET-style PDFs where each unit has an explicit `UNIT I` / `UNIT – II` stamp on the first page of its section (spread throughout the document, not condensed on a TOC page). The result was fall-through to font-heuristics, producing 19 messy fragments instead of 5 clean unit segments.

**Decision:**
Replace `_extract_visual_toc()` with `_extract_unit_markers()`:
- Scans **every page** (not just pages 2–8) for `UNIT\s*[-–]?\s*([IVX]+|\d+)` in the first 200 characters.
- Converts Roman numerals to integers and deduplicates (first occurrence per unit number wins).
- Validates monotonic page order; rejects if non-monotonic.
- Extracts text for each unit range using the new shared `_extract_page_range_text()` helper.
- Pages before the first UNIT marker are collected as a `Frontmatter` block.

Add `_extract_page_range_text()` as a shared helper used by both Tier 1 (TOC) and Tier 2 (UNIT markers), eliminating ~100 lines of duplicate body-crop / table / code-marker logic.

**Consequences:**
- MRCET-style PDFs now produce 6 clean segments (Frontmatter + 5 Units) instead of 19 font-heuristic fragments.
- Visual TOC scanner is removed; the `_VISUAL_TOC_LINE_RE` constant is no longer used.
- PDFs without UNIT markers fall through to font-heuristics as before.

**Linked Requirements:** ADR-001, ADR-029, critic.v3.md Issues 1, 2

---

## ADR-036: Rubric Evaluation Questions Schema & Grammar Check in text_readability

**Date:** 2026-03-25
**Status:** Accepted
**Amends:** ADR-032 (Rubric Description Sharpening)

**Context:**
LLM rationales for Module Gate scores were inconsistent — the same evidence produced different scores across runs because the model lacked a structured checklist to work through. The `text_readability` rubric mentioned "grammatically correct" in passing but did not explicitly penalise grammar/spelling errors in its anchors or prompt.

**Decision:**
1. **`evaluation_questions` field (all 10 rubrics):** Add a YAML list of 4–5 evaluator questions per rubric. Questions are concrete, evidence-seeking, and binary (answerable ↑/↓). Example for `example_concreteness`: *"Do examples use realistic, domain-grounded data rather than trivial placeholders (a=5, x=[1,2,3])?"*
2. **Grammar check in `text_readability`:** Updated description to explicitly include grammatical errors, spelling mistakes, and poorly constructed sentences as penalisable. Low-band anchor rewritten: *"frequent grammatical errors, typos, or ambiguous phrasing blocks comprehension"*.
3. **`instructional_alignment` anti-inflation:** Added `WARNING — Anti-inflation rule` paragraph to description: topic-only coverage anchors at 6 (mid). Added `top: 9-10` band. Matches `business_relevance` treatment.
4. **`_format_rubrics_for_prompt()` in `evaluator.py`:** Rubrics are no longer embedded as raw YAML. Each rubric renders as a structured prompt section: heading → description → scoring guide bullets → numbered evaluation checklist.
5. **Module Gate `SCORING PROCEDURE`:** Step 1 renamed from "IDENTIFY" to "CHECKLIST" — instructs the LLM to work through the rubric's evaluation questions, mark ↑/↓ per question, then anchor to a band.

**Consequences:**
- Consistent, question-anchored rationales across all batches.
- Grammar and spelling errors are now a named scoring factor, not an implicit consideration.
- Rubric YAML is the single source of truth for both description and evaluation procedure.
- Adding/editing a rubric question requires only editing `rubrics.yaml`; `evaluator.py` picks it up automatically.

**Linked Requirements:** ADR-021, ADR-032, critic.v3.md Issues 6, 7, 11

---

## ADR-037: Soft Word-Count Segmentation Ceiling

**Date:** 2026-04-10
**Status:** Accepted
**Supersedes:** ADR-009 (Max-Characters Segment Cap)

**Context:**
ADR-009 enforced a hard `max_chars = 8000` ceiling on every segment produced by `SmartSegmenter`. That ceiling was set when the target LLMs had ~16k-token context windows. Modern Claude Opus 4.6 and Gemini 2.5 Flash both accept 200k+ tokens and can score a full chapter as a single coherent pedagogical unit. Under the old cap, any chapter longer than ~1,500 words was split mid-content, corrupting the Module Gate signal — `instructional_flow` and `conceptual_continuity` cannot be scored on a fragment. The natural unit for Module Gate scoring is the chapter.

**Options Considered:**
1. *Keep the 8k character cap:* Safe but destroys the assessment signal on any non-trivial chapter.
2. *Remove all ceilings:* Simple but leaks a pathological 40k-word "everything" chapter straight into the LLM prompt with no safety net.
3. *Soft word-count ceiling, only triggered on oversized segments:* Chapters flow through untouched; pathological megachapters still get safely chunked at paragraph boundaries.

**Decision:**
- Replace `max_chars` with `max_words` as a *soft* guidance ceiling, default **30,000 words** (≈40,000 tokens — a conservative proxy well under the 200k context limit).
- A single chapter/segment is *never* split unless it exceeds `max_words`.
- `_merge_short_blocks` still runs — natural chapter boundaries are preserved and short adjacent sections can combine up to the soft ceiling.
- `_chunk_text()` only triggers on segments exceeding `max_words`.
- `max_chars` remains as an accepted kwarg for backwards compat but is coerced to a word-count internally (approximation: 5 chars/word) and a deprecation warning is logged.

**Consequences:**
- Typical 2k–5k-word chapters pass through as one segment — Module Gate scores reflect the pedagogical arc of the chapter, not arbitrary fragments.
- Rare megachapters are still safely chunked via the existing paragraph → sentence → word fallback.
- Backwards compatible for any external caller still passing `max_chars`.

**Linked Requirements:** FR-003, FR-004

---

## ADR-038: Two-Phase Heuristic+LLM Metadata Pipeline

**Date:** 2026-04-10
**Status:** Accepted
**Supersedes:** ADR-022 (Focused AI Extraction for Learning Outcomes & Prerequisites)

**Context:**
The regex + targeted-AI approach from ADR-022 has three failure modes that directly corrupt Course Gate scoring:

1. **Prose-embedded outcomes are missed.** Many textbooks (e.g. Think OS, SICP, many undergraduate introductions) state their learning goals and prerequisites inside a preface paragraph rather than under a labeled "Learning Objectives" bullet list. `_ai_extract_list_fields()` expects heading synonyms and reliably returns `learning_outcomes = []` for these books.
2. **Author/publisher are occasionally pulled from body text.** The regex pass is not strictly scoped to the first 3 pages in all code paths; a "By Charles Darwin" sentence in a chapter body can pollute the author field.
3. **No TOC parser.** Segmentation tiers (ADR-023) currently have no reference chapter count to validate against, so a spuriously over-segmented or under-segmented PDF is accepted silently.

Additionally, the current schema does not distinguish *stated* (explicitly labeled) from *inferred* (read from prose) learning outcomes and prerequisites, so the Course Gate cannot weight explicit vs. implicit pedagogical structure.

**Options Considered:**
1. *Keep ADR-022's two-call approach (full pass + focused list-fields pass):* Doesn't solve prose-embedded outcomes; two calls per PDF; no TOC.
2. *LLM-only, single call, no heuristics:* Loses the font-heuristic title and the cover-only author scoping — reintroduces hallucination risk for author/publisher.
3. *Heuristic phase (no LLM) + single LLM enrichment call with strict JSON schema + validator:* Deterministic parts stay deterministic; LLM is scoped to fields that genuinely need it; structural constraints (cover-only author, word-count validator) make corruption impossible.

**Decision:**
Option 3. The metadata pipeline becomes two explicit phases plus a validator:

1. **Heuristic phase** (no LLM) — `_extract_heuristic_metadata(pdf_path)` returns an intermediate dict:
   - `raw_text_15`: full text of first 15 pages (for the LLM)
   - `cover_text`: first 3 pages only (for author/publisher extraction)
   - `title`: via existing `_extract_title_by_font()` scoped to cover pages
   - `author_candidate` / `publisher_candidate`: via `_AUTHOR_PATTERNS` / `_PUBLISHER_PATTERNS` matched *only* against `cover_text`
   - `toc_candidates`: new `_extract_toc_heuristic()` — detects dotted leaders, indented hierarchies, and page-number patterns; produces `List[{chapter_number, title, page_number}]`
2. **LLM enrichment phase** — a single focused call returning strict JSON matching the new `CourseMetadata` schema:
   - `title`, `author`, `publisher`
   - `level` ∈ `{introductory, intermediate, advanced, undergraduate_introductory, undergraduate_advanced, graduate}`
   - `target_audience` (string)
   - `prerequisites_stated: List[str]` / `prerequisites_inferred: List[str]`
   - `learning_outcomes_stated: List[str]` / `learning_outcomes_inferred: List[str]`
   - `toc: List[TOCEntry]`
   - `draft_notes: str`
   Prompt rules (enforced via system prompt):
   - "If unsure, return empty string or empty list. Never invent content."
   - "Author and publisher must appear in the cover text block. Do not extract them from the raw 15-page text."
   - "`_stated` fields mean explicitly labeled (e.g. section titled 'Learning Objectives'); `_inferred` fields mean implied by preface/introduction prose."
   Provider policy: **Gemini 2.5 Flash primary, Claude Sonnet 4.6 fallback** (per Q-029). Uses `response_mime_type="application/json"` + Pydantic `response_schema` on Gemini, and strict JSON system prompt on Claude.
3. **Validation + merge phase** — `CourseMetadata` Pydantic v2 model enforces:
   - `level` field validator coerces invalid values to `""`.
   - `author` / `publisher` field validators coerce values with more than 6 words to `""` (structural defense against body-text corruption).
   - `_merge_heuristic_and_llm(heuristic, llm_meta)` fills any still-empty field with the heuristic candidate.

**Consequences:**
- Exactly one LLM call per PDF (down from up to two under ADR-022).
- Author/publisher corruption from body text is structurally impossible — the prompt scopes to `cover_text`, the validator caps word count at 6, and the heuristic fallback also uses cover-only extraction.
- Downstream consumers now see both *stated* and *inferred* lists. Course Gate can weigh explicit vs. implicit pedagogical structure.
- Legacy `prerequisites` / `learning_outcomes` fields on `CourseMetadata` are **deleted outright** (Q-030). One-pass migration of `evaluator.py`, `exporter.py`, `main.py`, and tests.
- `AIMetadataExtractor` is refactored in-place to issue the new single-call schema; `_ai_extract_list_fields()`, `_LIST_FIELDS_SYSTEM_PROMPT`, and the legacy two-call path are deleted.
- A calibration reference `examples/metadata_reference_thinkos.json` (Think OS v0.7.4 by Allen B. Downey) anchors "correctly extracted metadata" for future prompt tuning.

**Linked Requirements:** FR-001, FR-002, Q-029, Q-030

---

## ADR-039: TOC-Validated Segmentation Count

**Date:** 2026-04-10
**Status:** Accepted
**Amends:** ADR-023 (Three-Tier TOC Hierarchy)

**Context:**
ADR-023 established the three-tier segmentation fallback (PDF bookmark outline → UNIT marker scan → font-heuristic). Each tier silently accepts whatever count it produces. In practice we see two failure modes:

1. **Spurious matches.** The UNIT scan occasionally fires on a section heading that happens to contain the word "UNIT" in the body, producing 14 blocks on a 10-chapter book.
2. **Missed matches.** A PDF outline with nested sub-chapters can produce 3 top-level blocks on a 10-chapter book if `_flatten_outline()` is confused by the hierarchy.

Either way, the pipeline runs to completion with bad segmentation and the user has no signal that anything went wrong. With ADR-038 now producing a reliable parsed TOC in metadata, we finally have a ground-truth chapter count we can validate against.

**Decision:**
- `SmartSegmenter.__init__` gains an optional `course_metadata: Optional[CourseMetadata] = None` parameter.
- `segment()` reads `course_metadata.toc` (if non-empty) as a **reference chapter count** `toc_chapter_count`.
- After Tier 1 or Tier 2 produces its block list, count top-level chapters. If `abs(detected_count - toc_chapter_count) <= 2`, accept. Otherwise, log a WARNING and fall through to the next tier.
- Tier 3 (font-heuristic) always logs its result compared to `toc_chapter_count` but does not fall through further (it is the last tier — the user is informed but the pipeline continues).
- If `course_metadata` is `None` or `toc` is empty, behavior is unchanged (backwards compatible).
- A ±2 tolerance accounts for legitimate front-matter / back-matter that may or may not appear in the TOC (preface, appendix, index).

**Consequences:**
- Silent mis-segmentations on well-structured PDFs are now surfaced as warnings.
- `src/main.py` must pass the `CourseMetadata` it already extracts into `SmartSegmenter(...)` (one-line wiring change).
- Zero impact on PDFs without a parseable TOC.

**Linked Requirements:** FR-003, ADR-023, ADR-038

---

## ADR-040: Preface as Distinct Segment + Prose-Density Reference-Table Check

**Date:** 2026-04-10
**Status:** Accepted
**Amends:** ADR-012 (Non-Instructional Bypass), ADR-026 (Glossary/Summary/Assessment types)

**Context:**
Two `_classify_segment()` bugs surfaced during Course Gate calibration:

1. **Preface loses signal.** `_FRONTMATTER_PATTERNS` currently maps `preface` / `foreword` / `introduction-before-ch1` → `frontmatter`, which is bypassed entirely. But Course Gate's `structural_usability` and `prerequisite_alignment` need access to the preface — it is where a textbook states its goals, its audience, and its assumed prerequisites in prose. Collapsing it into `frontmatter` (alongside the copyright page) destroys that signal.
2. **Reference-table over-tagging.** The `reference_table` heuristic fires on `text.count("[TABLE:") >= 4` with no prose-density check. A prose-heavy instructional chapter on (say) SQL, which legitimately contains 5 syntax tables, gets misclassified as a reference table and silently bypassed by the Module Gate — its rubrics never get scored.

**Decision:**
1. **New segment type: `preface`.**
   - Classification rule: heading matches `^(preface|foreword|introduction|about\s+this\s+book)\b` (case-insensitive) AND the segment's page position is before the first detected chapter (tracked via the active TOC/UNIT output or `course_metadata.toc`).
   - `evaluator.py` routes `preface` segments into **Course Gate context** (fed to `structural_usability` and `prerequisite_alignment`), but assigns them a zero Module Gate score (they are not scored on `instructional_flow` etc.).
   - `EvaluatedSegment` docstring in `models.py` updated to list `preface` in the allowed `segment_type` values.
2. **Prose-density check on `reference_table`.**
   - New helper `_compute_prose_density(text: str) -> float` — strips `[TABLE:…]` annotations, `[CODE]...[/CODE]` regions, and bullet/numbered-list lines, then computes `prose_chars / len(text)`.
   - In `_classify_segment()`, the `reference_table` check now runs only when `prose_density <= 0.60`. Above that threshold the segment is classified as `instructional` regardless of table marker count.

**Consequences:**
- `preface` segments appear in the output JSON as a clearly-labeled type, improving audit clarity and giving Course Gate rubrics a cleaner evidence source.
- Prose-heavy chapters containing reference tables are no longer silently bypassed — Module Gate scores them as instructional content.
- One new branch in `evaluator.py`'s `classify_segment_for_gate()` logic: `preface` → Course Gate context.
- Zero API cost impact (the preface was already being read into the pipeline — it is just routed differently now).

**Linked Requirements:** ADR-012, ADR-026
