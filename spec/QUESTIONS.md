# Phase 0: Discovery Questions

## Q-001: Metadata Extraction
**Phase:** Discovery
**Date:** 2026-02-22
**Status:** Answered

**Question:** What specific metadata fields are required to be extracted from PDFs, JSON, or HTML (e.g., Title, Author, Target Audience, Subject, Prerequisites)?
**Answer:** Title, author, target audience, subject, source, description, prerequisite, learning outcomes.

## Q-002: Deterministic Segmentation Rules
**Phase:** Discovery
**Date:** 2026-02-22
**Status:** Answered

**Question:** How precisely should deterministic segmentation function? 
**Answer:** Primary strategy should be header-based (smartly distinguishing formatting headers from structural ones). Fallback to chunks of characters/pages but respecting sentence boundaries to avoid cutting mid-text.

## Q-003: LLM Integration
**Phase:** Discovery
**Date:** 2026-02-22
**Status:** Answered

**Question:** Which LLM provider or API will be used?
**Answer:** Primarily Claude 4.6 Sonnet (via API). Fallback to Gemini 2.5 Flash. API keys will be in `.env`.

## Q-004: Rubrics File
**Phase:** Discovery
**Date:** 2026-02-22
**Status:** Answered

**Question:** Where is `rubrics.yaml`?
**Answer:** It is/will be available in the `config/` folder.

## Q-005: Human Correlation Study Support
**Phase:** Discovery
**Date:** 2026-02-22
**Status:** Answered

**Question:** Does the CLI need to handle the human correlation study?
**Answer:** No. Focus only on building the automated V1 assessment tool that yields a JSON output. 

## Q-006: Execution Pipeline Boundaries
**Phase:** Discovery
**Date:** 2026-02-22
**Status:** Answered

**Question:** Does the CLI tool invoke the metadata script automatically?
**Answer:** Metadata extraction should be an independent step to ensure match, accepting JSON/HTML/TXT. If external metadata isn't provided, the main assessment script should automatically invoke a built-in PDF metadata extractor as a fallback.

## Q-007: Structure & Files Naming
**Phase:** Discovery
**Date:** 2026-02-22
**Status:** Answered

**Question:** Root folder vs `spec/` folder for spec docs?
**Answer:** Keep the spec md files (`guide.md`, `log.md`, `tasks.md`, `plan.md`) in the `spec/` folder.

---

# Phase 1: Requirements Questions

## Q-008: Evaluation Aggregation
**Phase:** Requirements
**Date:** 2026-02-22
**Status:** Answered

**Question:** How should the final "course-level" score be aggregated from the individual section scores? (e.g. simple mathematical average, or a final LLM summarization call?)
**Answer:** By mathematically averaging the section scores across the 10 dimensions.

## Q-009: API Failure Handling
**Phase:** Requirements
**Date:** 2026-02-22
**Status:** Answered

**Question:** How should the system handle a complete API failure (where both Claude and Gemini fail/timeout for a specific chunk)?
**Answer:** Crash gracefully: return an error message describing the actual error and stop the process entirely.

## Q-010: JSON Output Granularity
**Phase:** Requirements
**Date:** 2026-02-22
**Status:** Answered

**Question:** Should the final output JSON embed the exact text of every evaluated segment, or just the segment titles/IDs?
**Answer:** The exact text of every segment evaluated must be embedded in the final JSON output.

## Q-011: Addressing Critic Report Issues
**Phase:** Implementation
**Date:** 2026-02-25
**Status:** Answered

**Question:** How precisely are the major flaws identified in `CRITIC_REPORT.md` (e.g. model cascading, segmentation destruction, API costs) being addressed?
**Answer:** The pipeline was hardened by specifically targeting those issues: Removing model cascading entirely (single explicit model binding), dropping defaults from Pydantic schemas (forcing missing-field errors), using length-based (`max_chars`) grouping over arbitrary page fractions, executing batched API calls to save >70% overhead, and bypassing LLM evaluation for explicitly non-instructional text (frontmatter, exercises) to protect the overall course average.

## Q-012: Explicit Model Routing & Behaviors
**Phase:** Implementation
**Date:** 2026-02-25
**Status:** Answered

**Question:** How does the pipeline handle different models, and do Claude and Gemini use different operational approaches?
**Answer:** The user explicitly selects a provider via `--model claude` or `--model gemini`. The application utilizes the distinct `Anthropic()` or `google.genai.Client()` SDKs depending on the flag, and automatically fails if the respective key is missing. However, to maintain evaluation parity and comparability, both models receive identically constructed `system_prompt` and JSON array `user_prompt` payloads.

## Q-013: Standalone Metadata Extraction
**Phase:** Implementation
**Date:** 2026-02-25
**Status:** Answered

**Question:** Why does the metadata extraction module need to be independently executable?
**Answer:** To provide a safer "human in the loop" workflow. Inferred metadata (title, audience, prerequisites) can contain errors or missing fields. By extracting metadata to a reviewable JSON file first, the user can manually audit and correct it before it influences LLM evaluation scores. This prevents hallucinations caused by incorrect course context being injected into the grading prompt.

---

# Phase 0/1: Architecture Restructuring (Course vs Module Gates)

## Q-014: Scope of Course Gate
**Phase:** Architecture
**Date:** 2026-03-02
**Status:** Answered

**Question:** Should the Course Gate evaluate the course holistically at the very end (using extracted metadata, TOC, and aggregated insights), or does it evaluate batch-by-batch concurrently with the Module Gate?
**Answer:** All segments are assessed from a module rubric perspective first. Then, using a summary of all module assessments and their content, assess the whole course from a course rubric perspective at the very end.

## Q-015: Course Gate Context Limit
**Phase:** Architecture
**Date:** 2026-03-02
**Status:** Answered

**Question:** If the Course Gate runs holistically, how do we handle LLM token limits for massive courses? Do we pass a distilled summary of all modules, or just the metadata + table of contents?
**Answer:** Opt for the best architectural decision based on Answer 1: provide a distilled summary of what the modules have and take them all at once.

## Q-016: Rubric Mapping Validation
**Phase:** Architecture
**Date:** 2026-03-02
**Status:** Answered

**Question:** Is the following rubric split correct? 
- **Module Gate:** Goal Focus, Text Readability, Pedagogical Clarity, Example Concreteness, Example Coherence, Fluidity & Continuity.
- **Course Gate:** Prerequisite Alignment, Structural Usability, Business Relevance, Instructional Alignment.
**Answer:** No. Fluidity & Continuity should be in the Course Gate. Instructional Alignment should be in the Module Gate.
Corrected Split:
- **Module Gate:** Goal Focus, Text Readability, Pedagogical Clarity, Example Concreteness, Example Coherence, Instructional Alignment.
- **Course Gate:** Prerequisite Alignment, Structural Usability, Business Relevance, Fluidity & Continuity.

## Q-017: Overall Score Aggregation
**Phase:** Architecture
**Date:** 2026-03-02
**Status:** Answered

**Question:** How should the final "overall score" be computed? Should the JSON output two top-level scores (`course_score` and `module_average_score`), or mathematically blend them into one?
**Answer:** Have a score for each individual rubric and a designated overall score for *each* gate independently.

## Q-018: Evaluating Non-Instructional Segments
**Phase:** Architecture
**Date:** 2026-03-02
**Status:** Answered

**Question:** We currently bypass LLM evaluation for non-instructional segments (TOC, Preface, Exercises). Under the new two-gate system, should the Course Gate analyze these segments to assess Structural Usability and Alignment?
**Answer:** Yes.

## Q-019: Codebase Decoupling
**Phase:** Architecture
**Date:** 2026-03-02
**Status:** Answered

**Question:** Should we explicitly split `SectionScores` and `SectionReasoning` in `models.py` into distinct `CourseScores` and `ModuleScores` schema objects to strictly enforce the gate separation?
**Answer:** Yes.

## Q-020: Output Granularity for Course Gate
**Phase:** Architecture
**Date:** 2026-03-02
**Status:** Answered

**Question:** For the Module Gate, we track exact text per segment. For the Course Gate, does it yield one single evaluation object for the entire book, or does it still evaluate per-segment?
**Answer:** For course assessment, it should take just small summaries of what modules have and evaluate them all at once, yielding one single evaluation object for the entire course, not per-segment.

## Q-021: Execution Order
**Phase:** Architecture
**Date:** 2026-03-02
**Status:** Answered

**Question:** Does the Course Gate evaluation trigger only after the Module Gate finishes evaluating all chapters, acting as a final capstone LLM call?
**Answer:** Yes, it acts as an optimized, capstone LLM call.

## Q-022: Distinct System Prompts
**Phase:** Architecture
**Date:** 2026-03-02
**Status:** Answered

**Question:** Do we need completely separate system prompts and `rubrics.yaml` definitions for the Course Gate vs the Module Gate, stripping out irrelevant criteria from each?
**Answer:** Yes.

## Q-023: Delegating Context
**Phase:** Architecture
**Date:** 2026-03-02
**Status:** Answered

**Question:** If "Prerequisite Alignment" is moved completely to the Course Gate, can the Module Gate safely ignore prerequisites, saving significant prompt tokens per chunk?
**Answer:** Yes.

## Phase 2: Pipeline Hardening & Scanned PDF Support

## Q-024: OCR for Scanned PDFs
**Phase:** Pipeline Hardening
**Date:** 2026-03-03
**Status:** Answered

**Question:** Some course PDFs (e.g., scanned textbooks) contain page content as embedded images, not native text. pdfplumber returns 0 words for these pages. Should the segmenter fall back to OCR, or reject such PDFs with an error?
**Answer:** Add a silent per-page OCR fallback using Tesseract (via `pytesseract` + `pdf2image`). If tesseract is absent, skip the page gracefully and log a DEBUG message. No crash, no mandatory pre-processing step for users. System prereq: `sudo apt install tesseract-ocr poppler-utils`.

## Q-025: Course Gate with No Instructional Content
**Phase:** Pipeline Hardening
**Date:** 2026-03-03
**Status:** Answered

**Question:** When a PDF contains only frontmatter/copyright pages and produces zero instructional segments, should the Course Gate still run (with only metadata as input)?
**Answer:** No. Scoring a course holistically from only its title and description produces misleadingly high scores (the LLM infers quality from publisher reputation, not actual content). The Course Gate should be skipped entirely, with all scores set to 0, and a clear WARNING logged.

## Q-026: Classic OCR vs AI-based OCR
**Phase:** Pipeline Hardening
**Date:** 2026-03-03
**Status:** Answered

**Question:** Should scanned PDF text extraction use classic Tesseract OCR or an AI vision model (e.g., Gemini multimodal)?
**Answer:** Classic Tesseract. It is deterministic, reproducible, free, and sufficient for clean 300 DPI textbook scans. AI LLM budget should be reserved exclusively for the evaluation gates where judgment is required.

---

# Phase 3: Critic v3 Cross-Validation Fixes

## Q-027: Rubric Disambiguation — Topic Coverage vs Professional Application
**Phase:** Critic v3 Fixes
**Date:** 2026-03-24
**Status:** Answered

**Question:** The `business_relevance` rubric lets the LLM equate "topics match learning outcomes" with "practically applicable." Should we separate topic coverage (already scored by `goal_focus`) from real-world professional application (which `business_relevance` should uniquely assess)?
**Answer:** Yes. `business_relevance` must be rewritten to explicitly require real-world professional tasks, case studies, and industry scenarios. Matching topics to learning outcomes is already scored by `goal_focus` — `business_relevance` should penalise purely syntactic/theoretical content even when it covers stated outcomes. See ADR-032.
**Impact:** ADR-032 (rubric sharpening), `config/rubrics.yaml`

## Q-028: Exercise Pattern False Positives on Numbered Headings
**Phase:** Critic v3 Fixes
**Date:** 2026-03-24
**Status:** Answered

**Question:** The exercise classifier pattern `r'^\d+[\.)]'` matches any heading starting with a number (e.g. `"1. Built-in functions"`). Should the heading-based exercise check require exercise-specific keywords after the number?
**Answer:** Yes. Numbering alone is insufficient evidence. Require an imperative verb or exercise keyword after the number. See ADR-034.
**Impact:** ADR-034, `src/segmenter.py`

---

# Phase 4: Metadata & Segmentation Overhaul

## Q-029: Metadata LLM Provider Policy
**Phase:** Metadata Refactor
**Date:** 2026-04-10
**Status:** Answered

**Question:** The current `AIMetadataExtractor` has a Claude→Gemini fallback chain. With the move to a single-call, strict-JSON metadata pipeline (ADR-038), should we keep both providers, use Gemini only, or use Claude only?
**Answer:** **Gemini primary, Claude fallback.** Gemini 2.5 Flash is cheaper and its native `response_schema` + `response_mime_type="application/json"` give stronger structural guarantees for the new schema. Claude Sonnet 4.6 is retained as a fallback for the case where `GEMINI_API_KEY` is missing or the Gemini call fails. The existing Claude+Gemini skeleton in `AIMetadataExtractor` is kept but refactored in-place for the new single-call schema; `_ai_extract_list_fields()` and the two-call path are removed entirely.
**Impact:** ADR-038, `src/metadata.py`

## Q-030: Backwards Compatibility for Legacy `prerequisites` / `learning_outcomes` Fields
**Phase:** Metadata Refactor
**Date:** 2026-04-10
**Status:** Answered

**Question:** ADR-038 replaces the flat `prerequisites: List[str]` and `learning_outcomes: List[str]` fields on `CourseMetadata` with `_stated` / `_inferred` pairs. Should we retain the old fields as aliases (backwards compat) or delete them outright and migrate every consumer?
**Answer:** **Replace outright.** Retain nothing. Every downstream consumer (`src/evaluator.py`, `src/exporter.py`, `src/main.py`, tests) is updated in a single pass to read `prerequisites_stated + prerequisites_inferred` (and similarly for outcomes) where a union is needed. This keeps the schema clean and avoids dual-read code paths that would silently mask future bugs.
**Impact:** ADR-038, `src/metadata.py`, `src/evaluator.py`, `src/exporter.py`, `src/main.py`, `tests/test_metadata.py`

## Q-031: Segmenter `max_words` Default
**Phase:** Segmentation Refactor
**Date:** 2026-04-10
**Status:** Answered

**Question:** ADR-037 replaces the hard `max_chars = 8000` cap with a soft `max_words` ceiling. What default value should `max_words` carry?
**Answer:** **30,000 words** (≈40,000 tokens at ~1.3 tokens/word). This is a conservative proxy — well under the 200k context window of Claude Opus 4.6 and Gemini 2.5 Flash, but large enough that no ordinary chapter ever hits the ceiling. Pathological 40k-word "everything" chapters still get chunked safely at paragraph boundaries.
**Impact:** ADR-037, `src/segmenter.py`

