## 2026-03-25 — UNIT Marker Segmentation, Rubric Checklist Format & Anti-Inflation Fixes

**Type:** Feature (x1) + Refinement (x3) + Bug Fix (x1)

**What changed:**
- `src/segmenter.py`: Replaced `_extract_visual_toc()` (scanned pages 2–8 for a printed TOC table) with `_extract_unit_markers()` (scans all pages for live `UNIT I / UNIT II` markers in the top 200 chars). Added `_extract_page_range_text()` shared helper used by both TOC and UNIT paths (DRY). Resolves 19-fragment fragmentation on MRCET-style PDFs — now produces ~6 clean unit-aligned segments.
- `config/rubrics.yaml`: Added `evaluation_questions` field to all 10 rubrics (5 Module + 5 Course). Questions serve as a structured checklist the LLM works through before anchoring a score. Added explicit grammar/typo penalisation to `text_readability` description and scoring guide. Sharpened `instructional_alignment` with anti-inflation anchors: topic-only coverage anchors at 6 (mid), matching the `business_relevance` treatment.
- `src/evaluator.py`: Replaced raw `yaml.dump` rubric embedding with `_format_rubrics_for_prompt()` static method. Rubrics now render as structured sections (description + scoring guide bands + numbered evaluation checklist). Module Gate `SCORING PROCEDURE Step 1` updated from "IDENTIFY evidence" to "CHECKLIST — work through each rubric question, mark ↑/↓".

**Why it changed:**
Post-run analysis of `course_evaluation.json` revealed the MRCET PDF still produced 19 segments (visual TOC scanner missed MRCET's inline UNIT markers) and `instructional_alignment` was inflating to 8 on surface-level topic coverage. Rubric `evaluation_questions` added to provide consistent, question-anchored rationales.

**Affected artifacts:**
- ADR-029 → Amended by ADR-035 (UNIT marker scan supersedes visual TOC tier)
- ADR-032 → Amended by ADR-036 (evaluation_questions format + grammar check)
- TASK-052, TASK-053 → New (Completed)

---

## 2026-03-24 — Critic v3 Cross-Validation: Planning & ADRs

**Type:** Refinement (x6) + Bug Fix (x3)

**What changed:**
- `spec/DECISIONS.md`: Appended ADR-029 (Visual TOC parser & bold-frequency header filter), ADR-030 (cross-segment awareness & 3-step calibration), ADR-031 (explicit academic audience detection), ADR-032 (rubric description sharpening), ADR-033 (single CourseMetadata source of truth), ADR-034 (exercise classifier overhaul & frontmatter boilerplate).
- `spec/QUESTIONS.md`: Appended Q-027 (rubric disambiguation) and Q-028 (exercise pattern false positives).
- `spec/TASKS.md`: Added TASK-045 through TASK-051 covering all 16 critic.v3 issues.
- `spec/CHANGELOG.md`: This entry.

**Why it changed:**
Manual human cross-validation against the MRCET Python Programming Notes PDF revealed 16 issues across segmentation, metadata extraction, rubric definitions, and evaluator prompts, producing a Module Gate average inflation of +1.65 and a catastrophic +5 error on `business_relevance`.

**Affected artifacts:**
- ADR-023 → Amended by ADR-029 (visual TOC tier)
- ADR-021 → Amended by ADR-030 (3-step calibration replaces binary anchor)
- ADR-029 through ADR-034 → New
- Q-027, Q-028 → New (Answered)
- TASK-045 through TASK-051 → New (Pending)



**Type:** Feature Addition (x2) + Refinement (x1)

**What changed:**
- `src/metadata.py`: Added `_LIST_FIELDS_SYSTEM_PROMPT` and `_ai_extract_list_fields()`. Auto-triggers an AI pass after the regex stage to extract `learning_outcomes` and `prerequisites` under any heading name.
- `src/segmenter.py`: Added `_extract_toc()` and `_flatten_outline()`. `segment()` now uses the PDF bookmark outline first; falls back to font-heuristic. Chapters precisely delimited.
- `src/models.py`: Added `RubricResult`, `GateReport`, `AssessmentTree`. `CourseEvaluation` gains `assessment: AssessmentTree`.
- `src/aggregator.py`: Added `_build_assessment_tree()` populating the full tree at aggregation time.
- `tests/test_aggregator.py`: 3 new AssessmentTree tests. Suite: 45/45 passing.
- `spec/DECISIONS.md`: ADR-022, ADR-023, ADR-024 appended.
- `spec/TASKS.md`: TASK-040 through TASK-044 added (all complete).

**Why it changed:**
Prerequisites and learning outcomes were missed when PDFs used non-standard headings. TOC-based segmentation prevents chapters being split at wrong boundaries. The tree output makes assessment results human-readable with a clear rubric hierarchy.

**Affected artifacts:**
- ADR-022, ADR-023, ADR-024 -> New
- TASK-040 through TASK-044 -> Complete

## 2026-03-06 — Test Suite Fix & Chain-of-Thought Module Gate Prompting

**Type:** Bug Fix / Accuracy Improvement

**What changed:**
- `tests/test_aggregator.py`: Rewrote entirely. Updated imports from stale `SectionScores`/`SectionReasoning` to `ModuleScores`/`ModuleReasoning`. Updated `aggregate()` call to include required `course_assessment` argument. Expanded from 1 broken test to 4 comprehensive tests covering weighted averages, non-instructional segment exclusion, course gate passthrough, and empty segment edge cases.
- `src/evaluator.py`: Added `SCORING PROCEDURE` Chain-of-Thought block to the Module Gate system prompt in `_build_module_batch_prompts()`. For each rubric, the LLM is guided to identify specific textual evidence, reason above/below the midpoint, then finalise the score — before populating the rationale. No schema or API call count changes.
- `spec/DECISIONS.md`: Added ADR-021 (Chain-of-Thought scoring procedure).
- `spec/TASKS.md`: Added TASK-038 and TASK-039 (both complete).
- `spec/CRITIC_REPORT.md`: Marked Issue #5 (Output Overload) as [FIXED].

**Why it changed:**
- `test_aggregator.py` was broken by the Two-Gate refactor (TASK-038). The test suite failed to collect, masking any regressions.
- CRITIC_REPORT Issue #5 (zero-shot cognitive overload) was the last unfixed issue. A CoT prompt injection resolves it without splitting API calls or adding cost.

**Affected artifacts:**
- TASK-038 → Complete
- TASK-039 → Complete
- ADR-021 → New
- CRITIC_REPORT Issue #5 → [FIXED]

## 2026-03-04 — Minor Refinements: Metadata Cap, Example Output & .gitignore

**Type:** Minor Tweak / Developer Experience
**What changed:**
- `src/metadata.py`: Raised `contributing_authors` cap from 15 to 17 — accommodates textbooks with larger author/contributor lists (e.g. OpenStax community books).
- `data/output/output.json`: Added a committed example output file showing the expected JSON schema produced by the pipeline (`module_gate`, `course_gate`, `segments` with per-rubric scores, summaries, and rationales). Serves as a reference for consumers of the evaluation output.
- `data/output/.gitkeep`: Updated to reference `output.json` to make the example file's purpose explicit.
- `.gitignore`: Added `!data/output/output.json` exception rule so the example output file is tracked in git despite the blanket `data/output/**` ignore pattern.
**Why it changed:** Users forking the repo need a concrete example of what the pipeline produces. The `.gitignore` exception ensures the example is always present in the repo without committing real evaluation artefacts.
**Impact:** No functional change to the evaluation pipeline.

## 2026-03-03 — Pipeline Hardening: OCR Fallback, Scanned PDF Support & Gemini Schema Enforcement
**Type:** Bug Fix / Feature Addition
**What changed:**
- `src/segmenter.py`: Added OCR fallback (`_check_ocr_available`, `_ocr_page`) using `pytesseract` + `pdf2image`. When pdfplumber returns 0 words for a page (scanned image), the page is rendered at 300 DPI and Tesseract is invoked automatically. Graceful no-op if tesseract is not installed. Also expanded copyright/license page detection (`_is_copyright_page`) with keyword density heuristic — pages with 4+ legal markers (©, Creative Commons, ISBN, OpenStax…) are classified as `frontmatter`.
- `src/evaluator.py`: Fixed two Gemini JSON parsing bugs — added `_unwrap_gemini_list` and `_unwrap_gemini_object` helpers to normalize all Gemini response shapes (bare array, `{"evaluations":[...]}` wrapping, `{"rubric_scores":[{id,score}]}` list-of-objects, etc.). Added `response_schema` to `GenerateContentConfig` for both Module Gate and Course Gate Gemini calls to enforce strict JSON compliance upstream.
- `src/main.py`: Course Gate is now skipped when no instructional segments are found — avoids meaningless scores based purely on metadata title/description. An incomplete `CourseAssessment` (all zeros) is used instead, with a WARNING log. Also fixed: `--input` now accepts a direct `.pdf` file path in addition to a directory.
- `requirements.txt`: Added `pytesseract>=0.3.10` and `pdf2image>=1.17.0`.
**Why it changed:** Real-world course PDFs (e.g. scanned OpenStax textbooks) are image-based from page 6 onwards. The pipeline was silently returning a single frontmatter segment with all-zero scores, misrepresenting the course. Gemini's non-deterministic JSON shapes also required robust unwrapping and schema enforcement.
**Impact:**
- Scanned PDFs now produce real segmented, evaluated output (requires `sudo apt install tesseract-ocr poppler-utils`).
- Pipeline is more honest: no-content PDFs emit a clear warning instead of inflated Course Gate scores.
- Gemini evaluations are structurally guaranteed via `response_schema`.

## 2026-03-02 — Project Scoping: Two-Gate Assessment Architecture
**Type:** Architecture Redesign / Scope change
**What changed:** 
- `spec/DECISIONS.md`: Added ADR-016 accepting the split of evaluation into distinct Course and Module gates.
- `spec/QUESTIONS.md`: Logged Q-014 through Q-023 to clarify execution, aggregation, and codebase decoupling parameters before implementation.
- `spec/task.md`: Tasks drafted to divide `rubrics.yaml`, rewrite `evaluator.py`, and separate `models.py`.
**Why it changed:** User identified that evaluating course-level metrics (e.g. Structure, Prerequisites) on an atomic, segment-by-segment basis was pedagogically inaccurate. Assessment needed to mirror real-world program design natively.
**Impact:** 
- The entire evaluation script will be decoupled into two stages.
- `rubrics.yaml` will be restructured.
- Phase 0/1 Discovery initiated to define the exact runtime mechanics.

## 2026-02-25 — Multi-Issue Resolution & Pipeline Hardening
**Type:** Bug fixes & Architecture Redesign
**What changed:** 
- Addressed `CRITIC_REPORT.md` issues across text parsing, aggregation, and evaluator workflows.
- `src/metadata.py`: Implemented a 15-page strict hard stop on native PDF scans to prevent endless recursive loops on missing metadata.
- `src/segmenter.py`: Overhauled segmentation grouping rule. Dropped arbitrary `page_count` heuristics in favor of strict `max_chars` proximity merging. Added `_FRONTMATTER_PATTERNS` to detect and bypass TOC, preface, and source lists. Implemented full table serialization `[TABLE: col | col]`. 
- `src/aggregator.py`: Overrode unweighted mathematical averages in favor of character-length weighted averaging, shielding scores from short one-sentence segments.
- `src/evaluator.py`: Restructured the pipeline into `evaluate_batch()`. Isolated massive grading rubrics to `system_prompt`. Batched 5 user segments together. Replaced mid-run model cascading with a strict, explicitly chosen model (`--model` flag).
- `src/models.py`: Dropped default `= 0` initialization to leverage strict Pydantic missing-field exceptions.
- `README.md` & `spec/`: Synced documentation logic to new architectural constraints.
**Why it changed:** All changes were necessitated by fundamental flaws raised during evaluation quality reviews, notably the corruption of scientific validity by model cascading, unweighted bias towards short text segments, schema vulnerability to truncated JSON outputs, and astronomical API evaluation costs.
**Impact:** 
- Massive structural overhaul to parsing logic.
- Pipeline execution limits hard-coded.
- API cost overhead dramatically slashed by >70% via JSON array batching and metadata bypass logic.

## 2026-02-22 — Extraction Pipeline Fix & Segment Cap Enforcement

**Type:** Bug fix / Correctness improvement
**What changed:**
- `src/segmenter.py`: Added `_words_to_lines()` — word-object-based line reconstruction that correctly inserts spaces for ligature-encoded fonts (fixes word concatenation bug, critic.md Issue 1). Rewrote `_extract_blocks_with_headers()` to: (a) crop each page to body region (`within_bbox` top 10% / bottom 8%) stripping running headers/footers; (b) detect tables via `find_tables()` and annotate as `[TABLE: ...]` markers; (c) use `_words_to_lines()` instead of `extract_text_lines(return_chars=True)`; (d) annotate consecutive monospace lines with `[CODE]`/`[/CODE]` markers; (e) return `(blocks, page_count)` tuple. Added `_merge_to_target()` — greedy smallest-pair merge that enforces a hard segment cap. Updated `segment()` to compute `max_segments = max(1, page_count // 10)` and use `_merge_to_target()` instead of `_merge_short_blocks()`. Removed `_merge_short_blocks()` from main path (`min_chars` parameter retained for backward compat).
- `src/evaluator.py`: Added 4-line extraction disclaimer block in `_build_prompt()` after the segment text, instructing the LLM not to penalise the course for pipeline artifacts (figures, ligature encoding, table markers, code markers).
- `spec/DECISIONS.md`: Added ADR-008 (word-level reconstruction), ADR-009 (page-count segment cap), ADR-010 (Y-crop + table detection).
- `spec/TASKS.md`: Marked TASK-016 complete; added TASK-017 through TASK-022.

**Why it changed:**
- `text_readability` scores were averaging 2.5/10 due to word concatenation from ligature-encoded fonts.
- The segmenter was producing 14 segments for a 40-page PDF; user requires ≤ 1 segment per 10 pages.
- Running headers/footers were being extracted as body text, polluting segment content.

**Impact:**
- Word boundaries now correctly reconstructed for ligature-encoded PDFs.
- Segment count hard-capped at `page_count // 10` (≤ 4 for 40-page PDFs).
- LLM no longer penalises extraction artifacts as course quality issues.

## 2026-02-22 — Segmentation Overhaul & Evaluator Fallback Fix

**Type:** Bug fix / Performance improvement
**What changed:**
- `src/segmenter.py`: Replaced loose header heuristic (`size > median + 1.5 OR bold`) with a strict chapter-level rule (`size >= median * 1.4 AND len < 80 chars`). Bold-alone formatting no longer triggers a new segment. Added `_merge_short_blocks()` to consolidate blocks shorter than `min_chars` (600). Raised `max_chars` from 3500 → 8000. Added page-number filtering.
- `src/evaluator.py`: Added `_claude_failure_count` / `_claude_disabled` state. After `_MAX_CLAUDE_FAILURES = 2` consecutive Claude failures the evaluator permanently routes to Gemini for the rest of the run, avoiding N wasted API calls.
- `spec/DECISIONS.md`: Added ADR-006 (Coarse Segmentation) and ADR-007 (Persistent Fallback).
- `spec/TASKS.md`: Added TASK-015 and TASK-016.

**Why it changed:**
- A ~40-page PDF was producing 102 segments (every bold subheading = new segment), making full evaluation impractically expensive.
- Claude was being retried on every segment even when it was clearly rate-limited or unavailable.

**Impact:**
- Expected segment count for a ~40-page PDF: 4–6 (down from ~100).
- Claude failure handling is now O(1) wasted calls instead of O(N).

## 2026-02-22 — Documentation Refactoring
**Type:** Documentation fix
**What changed:** 
- Moved `spec/GUIDE.md` to the root directory and renamed it to `README.md`.
**Why it changed:** User requested that the evaluation tool's manual be available as the primary README in the root.
**Impact:** 
- `spec/GUIDE.md` is now `README.md`
# Project Log & Changelog

## 2026-02-22 — Phase 3 & 4 Completion
**Type:** Scope change & Task Generation
**What changed:** 
- User answered remaining Phase 1 questions (averaging scores, hard crash on fail, verbose JSON exports).
- Updated `spec/DECISIONS.md` adding ADR-004 and revising ADR-002.
- Finalized `spec/SPEC.md` including execution flow, JSON schema, error handling, and testing strategies.
- Generated `spec/TASKS.md` explicitly linked to the spec requirements.
**Why it changed:** Proceeding into Phase 3 and 4 of `GEMINI.md` standard.
**Impact:** 
- The project is now fully specified.
- 14 atomic executable tasks have been written.
- Ready to move to **Phase 5 (Implementation)**.

## 2026-02-22 — Dimension Count Corrected
**Type:** Documentation fix
**What changed:** 
- Updated `spec/SPEC.md`, `spec/TASKS.md`, and project tracker to note 10 dimensions instead of 8.
**Why it changed:** User observed that the codebase and config actually evaluate 10 dimensions, but the spec documentation incorrectly stated 8.
**Impact:** 
- Documentation alignment

## 2026-02-22 — Model Selection Updated
**Type:** Requirements revision
**What changed:** 
- Updated `spec/SPEC.md` and `spec/QUESTIONS.md` to specify `Claude 4.6 Sonnet` as the primary model and `Claude 3.5 Sonnet`.
**Why it changed:** User instruction to use Sonnet 4.6 primarily.
**Impact:** 
- Requirements affected: FR-006

## 2026-02-22 — PDF Parsing Strategy Revised
**Type:** Decision revision
**What changed:** 
- Added ADR-005 to prioritize `pdfplumber` over `PyMuPDF`.
- Updated `spec/SPEC.md` and `spec/TASKS.md` to reflect the library change.
**Why it changed:** User instruction to focus on accuracy and research properties over speed/efficiency.
**Impact:** 
- Tasks affected: TASK-001, TASK-005
- ADRs affected: ADR-005 (new)

## 2026-02-22 — Phase 1 Requirements Initiated
**Type:** Scope change & Setup
**What changed:** 
- Converted Phase 0 answers into formal requirements in `spec/SPEC.md`.
- Created Architecture Decision Records inside `spec/DECISIONS.md`.
- Consolidated all planning markdown files inside the `spec/` directory as requested in Q7.
**Why it changed:** Proceeding into Phase 1 of `GEMINI.md` standard.
**Impact:** Established Hybrid Segmentation Strategy (ADR-001). Established Model Cascading pattern for LLMs (ADR-002). Formalized 8 Functional Requirements (FR-001 to FR-008).

## 2026-02-22 — Initial Discovery Phase Commenced
**Type:** Setup
**What changed:** Project documentation initialized based on User trigger `PLANNER`.
**Why it changed:** Requested by user following `GEMINI.md` logic.
**Impact:** Initialized `spec/PROJECT.md` and `spec/QUESTIONS.md`. Prompted 7 clarifying questions.

## 2026-03-13 — Critic v2 Fixes: Segmenter Classification, Rubric Realignment, Fragment Awareness

**Type:** Bug Fix (x3) + Architecture Correction (x1)

**What changed:**

- `src/segmenter.py`:
  - Added `_KNOWN_RUNNING_HEADERS` frozenset. Known publisher running headers (e.g. "Access. The future of education.") are now stripped from the heading field before classification — preventing false `frontmatter` tagging and score zeroing (ADR-025 / critic.v2 Issue 1).
  - Added `_GLOSSARY_HEADING_PATTERNS`, `_SUMMARY_HEADING_PATTERNS`, `_ASSESSMENT_OPTION_RE`, `_NUMBERED_QUESTION_RE`. New segment types `glossary`, `summary`, and `assessment` are now returned by `_classify_segment()` and bypassed by the Module Gate evaluator with zero scores and no LLM call (ADR-026 / critic.v2 Issue 2).

- `src/evaluator.py`:
  - Added `_detect_partial_course()` heuristic method. Detects PDF files that are fragments of larger courses (no TOC, no Chapter 1 heading) and injects a "PARTIAL COURSE FILE" disclaimer into the Course Gate system prompt (ADR-027 / critic.v2 Issue 3).
  - `evaluate_course()` now returns `(CourseAssessment, is_partial_course: bool)` tuple.
  - Removed `instructional_alignment` from `_MODULE_SCORE_FIELDS` and Module Gate tool schema. Added it to `_COURSE_SCORE_FIELDS` and Course Gate tool schema (ADR-028 / critic.v2 Issue 4).
  - Updated `_make_incomplete_course_assessment()` to include `instructional_alignment=0`.

- `src/models.py`:
  - Removed `instructional_alignment` from `ModuleScores` and `ModuleReasoning`.
  - Added `instructional_alignment` to `CourseScores` and `CourseReasoning`.

- `src/main.py`:
  - Updated `evaluate_course()` call to unpack `(course_assessment, is_partial_course)` tuple.
  - Logs a clear INFO message when a partial-course file is detected.

- `config/rubrics.yaml`:
  - Removed `instructional_alignment` from `module_rubrics`.
  - Added `instructional_alignment` to `course_rubrics` with cross-module framing.

- `tests/test_segmenter.py`:
  - Added 9 new tests: 2 for running header rejection (Issue 1), 7 for glossary/summary/assessment classification (Issue 2).
  - Suite: **39/39 passing**.

- `spec/DECISIONS.md`: ADR-025 through ADR-028 appended.
- `spec/CHANGELOG.md`: This entry.
- `spec/QUESTIONS.md`: Q-027 through Q-030 added.

**Why it changed:**
Four issues identified in `Planning/critic.v2.md` after evaluation of `firstpart.pdf` and `secondpart.pdf`. Running headers were silently zeroing instructional segments. Glossaries and question banks were being scored as content, distorting averages. Split PDF files were being penalised for material in sibling files. And `instructional_alignment` was architecturally misplaced in the Module Gate.

**Affected artifacts:**
- ADR-025 through ADR-028 → New
- Q-027 through Q-030 → New
- critic.v2.md Issues 1–4 → Fixed
